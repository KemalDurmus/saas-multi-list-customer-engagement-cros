(function () {
  var box = document.getElementById('filters');
  var table = document.getElementById('users-table');
  if (!box || !table) { return; }
  box.addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-filter]');
    if (!btn) { return; }
    var f = btn.getAttribute('data-filter');
    var buttons = box.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) { buttons[i].classList.remove('is-on'); }
    btn.classList.add('is-on');
    var rows = table.querySelectorAll('tbody tr');
    for (var j = 0; j < rows.length; j++) {
      var st = rows[j].getAttribute('data-status');
      rows[j].style.display = (f === 'all' || st === f) ? '' : 'none';
    }
  });
})();
