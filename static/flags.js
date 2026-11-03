/* F13: the board's "선생님 보기" toggle (local board app only; never published, see generate.LOCAL_ONLY_STATIC).
   The projected board never contains flags: they are fetched only while the toggle is on and removed when it is off. */
(function () {
  'use strict';
  var b = document.getElementById('tv-toggle'), box = document.getElementById('tv-flags');
  if (!b || !box) return;
  var D = JSON.parse(b.getAttribute('data-s'));
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  function off() { box.innerHTML = ''; box.hidden = true; b.setAttribute('aria-pressed', 'false'); }
  function on() {
    b.setAttribute('aria-pressed', 'true');
    fetch('/api/teacher/flags', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (j) {
      if (b.getAttribute('aria-pressed') !== 'true') return;
      box.innerHTML = '';
      box.appendChild(el('p', 'muted', D.hint));
      if (j.three) box.appendChild(el('p', '', D.three + ' · ' + (j.three.join(', ') || '–')));   /* F16.1 manitoba */
      if (!j.flags.length) box.appendChild(el('p', 'muted', D.none));
      j.flags.forEach(function (f) {
        var d = el('div', 'tvf ' + f.level);
        d.appendChild(el('b', '', f.display_name + ' · ' + (f.level === 'need' ? D.need : D.notice)));
        f.rules.forEach(function (r) { d.appendChild(el('div', 'muted', r.reason)); });
        box.appendChild(d);
      });
      box.hidden = false;
    });
  }
  b.onclick = function () { if (b.getAttribute('aria-pressed') === 'true') off(); else on(); };
  off();
})();
