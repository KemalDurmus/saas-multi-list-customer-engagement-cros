from flask import Blueprint, flash, render_template, request

import logic

bp = Blueprint("feature", __name__)


def _read_upload(storage, label, warnings):
    raw = storage.read(logic.MAX_BYTES + 1)
    cut = len(raw) > logic.MAX_BYTES
    if cut:
        raw = raw[: logic.MAX_BYTES]
        warnings.append(f"{label}: dosya {logic.MAX_BYTES // (1024 * 1024)} MB sınırını aştı; yalnızca ilk {logic.MAX_BYTES // (1024 * 1024)} MB okundu.")
    text = logic.decode_bytes(raw, label)
    if cut and "\n" in text:
        text = text.rsplit("\n", 1)[0]
    return text


@bp.route("/")
def index():
    return render_template("index.html", result=None)


@bp.route("/analyze", methods=["GET", "POST"])
def analyze():
    if request.method == "GET":
        return render_template("index.html", result=None)
    warnings = []
    try:
        if request.form.get("demo"):
            master_text, acts = logic.demo_data()
            sources = list(acts)
        else:
            master_text = ""
            mf = request.files.get("master_file")
            if mf and mf.filename:
                master_text = _read_upload(mf, "Ana liste (" + mf.filename + ")", warnings)
            else:
                master_text = request.form.get("master_text", "")
            if not master_text.strip():
                raise logic.InputError("Ana müşteri listesi eksik: bir CSV yükleyin, yapıştırın ya da örnek veriyi deneyin.")
            sources = []
            uploads = [f for f in request.files.getlist("activity_files") if f and f.filename]
            if len(uploads) > logic.MAX_FILES:
                warnings.append(f"En fazla {logic.MAX_FILES} etkinlik dosyası işlenir; fazla gelen {len(uploads) - logic.MAX_FILES} dosya yok sayıldı.")
                uploads = uploads[: logic.MAX_FILES]
            for f in uploads:
                sources.append((f.filename, _read_upload(f, f.filename, warnings)))
            pasted = request.form.get("activity_text", "")
            if pasted.strip():
                if len(sources) >= logic.MAX_FILES:
                    warnings.append("Yapıştırılan liste, dosya sınırı nedeniyle yok sayıldı.")
                else:
                    sources.append(("yapıştırılan_liste", pasted))
            if not sources:
                raise logic.InputError("En az bir etkinlik/anket listesi gerekli: dosya yükleyin ya da metin olarak yapıştırın.")

        master = logic.parse_list("Ana liste", master_text)
        if master["truncated"]:
            warnings.append(f"Ana liste {logic.MAX_ROWS} satırdan uzun; yalnızca ilk {logic.MAX_ROWS} satır işlendi.")
        activities = []
        used = {}
        for name, text in sources:
            label = name
            used[name] = used.get(name, 0) + 1
            if used[name] > 1:
                label = f"{name} ({used[name]})"
            act = logic.parse_list(label, text)
            if act["truncated"]:
                warnings.append(f"{label}: {logic.MAX_ROWS} satırdan uzun; yalnızca ilk {logic.MAX_ROWS} satır işlendi.")
            activities.append(act)
        result = logic.analyze(master, activities)
    except logic.InputError as exc:
        flash(str(exc), "error")
        return render_template("index.html", result=None)
    except Exception:
        flash("Dosyalar işlenirken beklenmeyen bir sorun oluştu. Lütfen dosyaların CSV biçiminde olduğunu kontrol edip tekrar deneyin.", "error")
        return render_template("index.html", result=None)
    for w in warnings:
        flash(w, "warning")
    return render_template("index.html", result=result)
