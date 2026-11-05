/* F7: AMP Live projector (local board app only). Polls the room every 2 s while it is open; shows counts, never names. */
(function () {
  'use strict';
  var S = JSON.parse(document.getElementById('data').textContent).S;
  var MARK = ['①', '②', '③', '④', '⑤'];
  function $(id) { return document.getElementById(id); }
  function fmt(s, o) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return o[k] != null ? o[k] : m; }); }
  function api(path, body) {
    var o = body === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    return fetch(path, o).then(function (r) { return r.json(); });
  }
  var timer = null;
  function poll(on) { clearInterval(timer); timer = on ? setInterval(function () { api('/api/live/state').then(render); }, 2000) : null; }

  function render(st) {
    if (!st || st.ok === false) { $('lv-msg').textContent = (st && st.error) || ''; return; }
    if (!st.open) { $('lv-room').hidden = true; $('lv-setup').hidden = false; poll(false); $('lv-msg').textContent = S.closed; return; }
    $('lv-setup').hidden = true; $('lv-room').hidden = false;
    $('lv-code').textContent = st.room; $('lv-title').textContent = st.title || '';
    $('lv-qn').textContent = st.q ? fmt(S.qn, { q: st.q, n: st.n }) : S.wait;
    var q = st.question;
    $('lv-q').textContent = q ? q.text : '';
    var ol = $('lv-choices'); ol.innerHTML = '';
    if (q && q.kind === 'choice') q.choices.forEach(function (c, i) {
      var li = document.createElement('li');
      li.textContent = MARK[i] + ' ' + c;
      if (st.revealed && q.answer === String(i + 1)) li.className = 'right';
      ol.appendChild(li);
    });
    if (q && q.kind === 'num' && st.revealed) { var li = document.createElement('li'); li.className = 'right'; li.textContent = '= ' + q.answer; ol.appendChild(li); }
    var size = Math.max(1, st.size || 1);
    var val = st.revealed && st.correct != null ? st.correct : st.answered;
    $('lv-fill').style.width = Math.min(100, 100 * val / size).toFixed(1) + '%';
    $('lv-fill').className = 'fill' + (st.revealed ? ' rev' : '');
    $('lv-mark').style.left = (100 * st.needed / size).toFixed(1) + '%';
    $('lv-count').textContent = st.revealed && st.correct != null
      ? fmt(S.correct, { c: st.correct, s: st.size, p: Math.round(100 * st.correct / size) })
      : fmt(S.answered, { a: st.answered, s: st.size, j: st.joined });
    if (st.bonus) { $('lv-bonus').textContent = fmt(S.bonus, { n: st.bonus, t: st.class_total }); $('lv-bonus').hidden = false; }
    $('lv-next').disabled = st.q >= st.n && st.revealed;
    $('lv-reveal').disabled = !st.q || st.revealed;
    $('lv-moment').disabled = !st.revealed;
  }

  api('/api/live/sets').then(function (j) {
    var sel = $('lv-set');
    (j.sets || []).forEach(function (s) { var o = document.createElement('option'); o.value = s.name; o.textContent = s.title + ' (' + s.n + ')'; sel.appendChild(o); });
    if (j.open) api('/api/live/state').then(function (st) { render(st); poll(true); });
  });
  /* live-v2: 세트 미리보기 → 확인했어요 → 승인하고 방 열기 (the room opens only with the digest of exactly what was shown) */
  var PV = null;
  function pvMath(el, t) { if (window.DaybookLivePhone) window.DaybookLivePhone.math(el, t); else el.textContent = t; }
  function pvDraw() {
    var ol = $('lv-pv-list'); ol.innerHTML = '';
    PV.questions.forEach(function (q) {
      var li = document.createElement('li'), stem = document.createElement('p'); pvMath(stem, q.q); li.appendChild(stem);
      if (q.image) { var im = document.createElement('img'); im.src = q.image; im.alt = ''; im.className = 'lv-pv-img'; li.appendChild(im); }
      if (q.kind === 'choice') {
        var cl = document.createElement('ol'); cl.className = 'lv-pv-ch';
        q.choices.forEach(function (c, i) { var x = document.createElement('li'); pvMath(x, c); if (String(i + 1) === q.answer) { x.className = 'ans'; x.appendChild(document.createTextNode(' ✓')); } cl.appendChild(x); });
        li.appendChild(cl);
      } else { var a = document.createElement('p'); a.className = 'ans'; a.textContent = '정답 ' + q.answer; li.appendChild(a); }
      if (q.explain) { var e = document.createElement('p'); e.className = 'muted'; pvMath(e, q.explain); li.appendChild(e); }
      ol.appendChild(li);
    });
  }
  $('lv-preview').onclick = function () {
    PV = null; $('lv-ok').checked = false; $('lv-open').disabled = true;
    fetch('/api/live/preview?set=' + encodeURIComponent($('lv-set').value), { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) { $('lv-msg').textContent = j.error || ''; return; }
      PV = j; $('lv-pv').hidden = false; $('lv-pv-title').textContent = j.title + ' · ' + j.questions.length;
      pvDraw();
      if (window.DaybookLivePhone) window.DaybookLivePhone.load(pvDraw);
    });
  };
  $('lv-set').onchange = function () { PV = null; $('lv-pv').hidden = true; $('lv-open').disabled = true; };
  $('lv-ok').onchange = function () { $('lv-open').disabled = !(PV && $('lv-ok').checked); };
  $('lv-open').onclick = function () {
    if (!PV || !$('lv-ok').checked) { $('lv-msg').textContent = S.need; return; }
    api('/api/live/open', { set: PV.name, approved: PV.digest, phone: $('lv-phone').checked }).then(function (st) {
      if (st.ok === false) { $('lv-msg').textContent = st.error || ''; return; }
      render(st); if (st.open) poll(true);
    });
  };
  $('lv-next').onclick = function () { $('lv-bonus').hidden = true; api('/api/live/next', {}).then(render); };
  $('lv-reveal').onclick = function () { api('/api/live/reveal', {}).then(render); };
  $('lv-moment').onclick = function () { api('/api/live/moment', {}).then(function (st) { render(st); $('lv-msg').textContent = st.ok === false ? st.error : '✓ 오늘의 장면'; }); };
  $('lv-close').onclick = function () { api('/api/live/close', {}).then(render); };
})();
