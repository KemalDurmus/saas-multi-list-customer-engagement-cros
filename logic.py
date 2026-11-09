"""Çekirdek iş mantığı: CSV okuma, e-posta temizleme, eşleştirme ve skorlama.

Flask'a bağımlı değildir; veri yalnızca bellekte işlenir.
"""
import csv
import io
import re
from collections import Counter
from datetime import date, timedelta
from urllib.parse import quote

MAX_FILES = 20
MAX_ROWS = 50000
MAX_BYTES = 5 * 1024 * 1024
TABLE_LIMIT = 500

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
EMAIL_KEYS = ("email", "e-mail", "e-posta", "eposta", "mail")


class InputError(Exception):
    """Kullanıcıya gösterilecek girdi hatası."""


def decode_bytes(raw, label):
    """Yüklenen baytları metne çevirir; ikili dosyaları reddeder."""
    if not raw:
        raise InputError(f"{label}: dosya boş.")
    head = raw[:4096]
    if b"\x00" in head or head.startswith((b"PK\x03\x04", b"%PDF", b"\xd0\xcf\x11\xe0", b"\x89PNG", b"\xff\xd8\xff")):
        raise InputError(f"{label}: Bu dosya metin değil gibi görünüyor (Excel, PDF, görsel veya sıkıştırılmış dosya olabilir). Lütfen CSV olarak dışa aktarıp tekrar yükleyin.")
    for enc in ("utf-8-sig", "cp1254"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def normalize_email(cell):
    """Hücreden e-postayı çıkarır: küçük harf, boşluk/<> temizliği, +alias kaldırma.
    (e-posta, alias_temizlendi_mi) döner; bulunamazsa (None, False)."""
    if cell is None:
        return None, False
    m = EMAIL_RE.search(cell.strip().strip("<>\"' "))
    if not m:
        return None, False
    raw = m.group(0).lower()
    local, domain = raw.split("@", 1)
    base = local.split("+")[0]
    if not base:
        base = local
    return base + "@" + domain, base != local


def _detect_delimiter(text):
    for line in text.splitlines():
        if line.strip():
            counts = {d: line.count(d) for d in ",;\t|"}
            best = max(counts, key=counts.get)
            return best if counts[best] > 0 else ","
    return ","


def _is_name_header(h):
    h = h.strip().lower()
    return ("name" in h) or ("isim" in h) or h in ("ad", "adı", "adi", "ad soyad", "adsoyad")


def parse_list(label, text, max_rows=MAX_ROWS):
    """CSV metnini okur ve e-posta listesini döner."""
    if text is None or not text.strip():
        raise InputError(f"{label}: içerik boş.")
    text = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    delim = _detect_delimiter(text)
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    except csv.Error as exc:
        raise InputError(f"{label}: CSV okunamadı ({exc}).")
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise InputError(f"{label}: okunabilir satır bulunamadı.")
    first = rows[0]
    headerless = any(EMAIL_RE.search(c) for c in first)
    ncols = max(len(r) for r in rows)
    if headerless:
        headers = [f"sütun {i + 1}" for i in range(ncols)]
        data = rows
    else:
        headers = list(first) + [""] * (ncols - len(first))
        data = rows[1:]
    truncated = len(data) > max_rows
    data = data[:max_rows]
    if not data:
        raise InputError(f"{label}: başlık satırından sonra veri satırı yok.")

    valid_counts = [0] * ncols
    for row in data:
        for i, cell in enumerate(row[:ncols]):
            if EMAIL_RE.search(cell):
                valid_counts[i] += 1
    if max(valid_counts) == 0:
        raise InputError(f"{label}: e-posta adresi içeren bir sütun bulunamadı. Dosyanın bir e-posta sütunu (ör. email, e-posta) içerdiğinden emin olun.")
    cands = [i for i, h in enumerate(headers) if any(k in h.strip().lower() for k in EMAIL_KEYS) and valid_counts[i] > 0]
    pool = cands if cands else range(ncols)
    col = max(pool, key=lambda i: valid_counts[i])

    name_col = None
    for i, h in enumerate(headers):
        if i != col and _is_name_header(h):
            name_col = i
            break

    emails, names = [], []
    invalid = 0
    aliases = 0
    for row in data:
        cell = row[col] if col < len(row) else ""
        email, aliased = normalize_email(cell)
        if email is None:
            invalid += 1
            continue
        if aliased:
            aliases += 1
        emails.append(email)
        nm = ""
        if name_col is not None and name_col < len(row):
            nm = row[name_col].strip()
        names.append(nm)
    if not emails:
        raise InputError(f"{label}: geçerli e-posta adresi içeren satır bulunamadı.")
    return {
        "label": label,
        "emails": emails,
        "names": names,
        "invalid": invalid,
        "aliases": aliases,
        "truncated": truncated,
        "email_column": headers[col].strip() or f"sütun {col + 1}",
        "rows": len(data),
    }


def _safe_cell(v):
    v = str(v)
    if v and v[0] in "=+-@\t\r":
        return "'" + v
    return v


def status_of(files_present, events, total_files):
    if files_present == 0:
        return "pasif"
    need = -(-total_files * 3 // 4)  # tavan(0.75 * N)
    if events >= 3 and files_present >= need:
        return "power"
    return "aktif"


STATUS_LABELS = {"pasif": "Pasif", "aktif": "Aktif", "power": "Power user"}


def analyze(master, activities):
    """Ana liste ile etkinlik listelerini eşleştirir."""
    n = len(activities)
    seen = {}
    for em, nm in zip(master["emails"], master["names"]):
        if em not in seen:
            seen[em] = nm
        elif not seen[em] and nm:
            seen[em] = nm
    duplicates = len(master["emails"]) - len(seen)

    counters = [Counter(a["emails"]) for a in activities]
    users = []
    for em, nm in seen.items():
        per_file = [c.get(em, 0) for c in counters]
        files_present = sum(1 for x in per_file if x > 0)
        events = sum(per_file)
        score = round(files_present * 100 / n) if n else 0
        users.append({
            "email": em,
            "name": nm,
            "per_file": per_file,
            "files": files_present,
            "events": events,
            "score": score,
            "status": status_of(files_present, events, n),
        })
    users.sort(key=lambda u: (-u["files"], -u["events"], u["email"]))
    for u in users:
        u["status_label"] = STATUS_LABELS[u["status"]]
        u["file_names"] = [activities[i]["label"] for i, x in enumerate(u["per_file"]) if x > 0]

    master_set = set(seen)
    file_stats = []
    for a, c in zip(activities, counters):
        matched_users = sum(1 for e in c if e in master_set)
        matched_rows = sum(v for e, v in c.items() if e in master_set)
        unmatched_rows = sum(v for e, v in c.items() if e not in master_set)
        file_stats.append({
            "label": a["label"],
            "rows": a["rows"],
            "email_column": a["email_column"],
            "matched_users": matched_users,
            "matched_rows": matched_rows,
            "unmatched_rows": unmatched_rows,
            "invalid": a["invalid"],
            "aliases": a["aliases"],
        })

    total = len(users)
    summary = {
        "total": total,
        "files": n,
        "passive": sum(1 for u in users if u["status"] == "pasif"),
        "active": sum(1 for u in users if u["status"] != "pasif"),
        "power": sum(1 for u in users if u["status"] == "power"),
        "avg_score": round(sum(u["score"] for u in users) / total) if total else 0,
        "duplicates": duplicates,
        "master_invalid": master["invalid"],
        "master_aliases": master["aliases"],
        "master_column": master["email_column"],
        "has_names": any(u["name"] for u in users),
    }
    return {
        "summary": summary,
        "file_stats": file_stats,
        "users": users,
        "shown": users[:TABLE_LIMIT],
        "hidden": max(0, total - TABLE_LIMIT),
        "csv_uri": build_csv_uri(users, activities),
    }


def build_csv_uri(users, activities):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "ad", "katildigi_dosya_sayisi", "toplam_kayit", "etkilesim_skoru_yuzde", "durum"] + [_safe_cell("kayit: " + a["label"]) for a in activities])
    for u in users:
        w.writerow([_safe_cell(u["email"]), _safe_cell(u["name"]), u["files"], u["events"], u["score"], u["status_label"]] + u["per_file"])
    return "data:text/csv;charset=utf-8," + quote("﻿" + buf.getvalue(), safe="")


def demo_data():
    """Bugüne göreli tarihlerle gömülü demo veri: (ana_metin, [(ad, metin), ...])."""
    today = date.today()

    def d(n):
        return (today - timedelta(days=n)).isoformat()

    people = [
        ("Ayse.Yilmaz@Example.com", "Ayşe Yılmaz", 160),
        ("mehmet.demir@example.com", "Mehmet Demir", 140),
        ("zeynep@startup.io", "Zeynep Kaya", 120),
        ("can.ozturk@mail.com", "Can Öztürk", 100),
        ("elif.sahin@example.com", "Elif Şahin", 90),
        ("burak@agency.co", "Burak Arslan", 75),
        ("selin.aydin@example.com", "Selin Aydın", 60),
        ("ahmet.celik@startup.io", "Ahmet Çelik", 45),
        ("deniz@studio.dev", "Deniz Koç", 30),
        ("ece.polat@example.com", "Ece Polat", 12),
    ]
    master = "email,name,signup_date\n"
    for i, (em, nm, days) in enumerate(people):
        shown = "  " + em.upper() + " " if i == 3 else em
        master += f"{shown},{nm},{d(days)}\n"

    events = "user_email,event,date\n"
    ev_rows = [
        ("ayse.yilmaz@example.com", "login", 2), ("ayse.yilmaz+beta@example.com", "export", 3),
        ("AYSE.YILMAZ@example.com", "dashboard_view", 5), ("mehmet.demir@example.com", "login", 4),
        ("mehmet.demir@example.com", "invite_sent", 6), ("zeynep@startup.io", "login", 1),
        ("can.ozturk@mail.com", "login", 9), ("selin.aydin@example.com", "login", 7),
        ("selin.aydin@example.com", "export", 8), ("elif.sahin@example.com", "login", 20),
        ("bilinmeyen@other.org", "login", 3),
    ]
    for em, ev, days in ev_rows:
        events += f"{em},{ev},{d(days)}\n"

    survey = "Zaman;E-posta;Memnuniyet\n"
    sv_rows = [
        ("ayse.yilmaz@example.com", 5, 10), ("mehmet.demir@example.com", 4, 11),
        (" Zeynep@Startup.io ", 5, 12), ("burak@agency.co", 3, 14), ("selin.aydin+nps@example.com", 4, 9),
    ]
    for em, score, days in sv_rows:
        survey += f"{d(days)};{em};{score}\n"

    beta = "email,feedback\n"
    bt_rows = [
        ("Ayse.Yilmaz@example.com", "Raporlama harika"), ("selin.aydin@example.com", "Dışa aktarma hızlı olabilir"),
        ("ahmet.celik@startup.io", "Entegrasyon istiyorum"), ("mehmet.demir@example.com", "Arayüz sade"),
    ]
    for em, fb in bt_rows:
        beta += f"{em},{fb}\n"

    return master, [("etkinlikler.csv", events), ("anket.csv", survey), ("beta_geri_bildirim.csv", beta)]
