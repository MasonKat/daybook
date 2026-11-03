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
  $('lv-open').onclick = function () { api('/api/live/open', { set: $('lv-set').value }).then(function (st) { render(st); if (st.open) poll(true); }); };
  $('lv-next').onclick = function () { $('lv-bonus').hidden = true; api('/api/live/next', {}).then(render); };
  $('lv-reveal').onclick = function () { api('/api/live/reveal', {}).then(render); };
  $('lv-moment').onclick = function () { api('/api/live/moment', {}).then(function (st) { render(st); $('lv-msg').textContent = st.ok === false ? st.error : '✓ 오늘의 장면'; }); };
  $('lv-close').onclick = function () { api('/api/live/close', {}).then(render); };
})();
