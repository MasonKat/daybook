/* games-v1 수학 용어 찾기: a cooperative break-time word search on the student page (no dependencies).
   app.js calls DaybookGame.init({D, S, tok, fmt}) and, when a Live room code turns out to be a game, DaybookGame.start(state, code). */
(function () {
  'use strict';
  var D, S, tok, fmt;
  function $(id) { return document.getElementById(id); }
  function init(ctx) {
    D = ctx.D; S = ctx.S; tok = ctx.tok; fmt = ctx.fmt;
    if (!$('gm')) return;
  /* ---- games-v1 수학 용어 찾기: one shared board per class (the room checks each find); no names, no ranking */
  var GM = { room: null, timer: null, st: null, sel: null, drag: null, demo: null, msgT: null };
  function gmShow(on) {
    $('gm').hidden = !on;
    if (!on) { clearInterval(GM.timer); GM.timer = null; GM.room = null; if (GM.demo) { clearInterval(GM.demo.t); GM.demo = null; } }
  }
  function gmBuild(st) {
    var g = $('gm-grid'), n = st.grid.length; g.innerHTML = ''; g.style.setProperty('--n', n);
    for (var r = 0; r < n; r++) for (var c = 0; c < n; c++) {
      var b = document.createElement('button'); b.type = 'button'; b.className = 'gm-c'; b.textContent = st.grid[r].charAt(c);
      b.setAttribute('data-r', r); b.setAttribute('data-c', c); g.appendChild(b);
    }
  }
  function gmStart(st, code) { GM.room = code; GM.sel = null; gmBuild(st); gmShow(true); gmRender(st); GM.timer = setInterval(gmPoll, 3000); }
  function gmPoll() {
    if (GM.demo) return gmRender(GM.demo.view());
    if (!GM.room) return;
    fetch(D.worker + '/live/state?t=' + tok + '&room=' + GM.room, { cache: 'no-store' })
      .then(function (r) { return r.json(); }).then(gmRender).catch(function () { /* next tick */ });
  }
  function gmSay(t) { $('gm-msg').textContent = t; clearTimeout(GM.msgT); GM.msgT = setTimeout(function () { gmRender(GM.st); }, 2200); }
  function gmRender(st) {
    if (!st) return;
    if (!st.open) { $('gm-msg').textContent = S.gm_closed; clearInterval(GM.timer); setTimeout(function () { gmShow(false); }, 2500); return; }
    GM.st = st;
    var on = {}, done = {};
    (st.found || []).forEach(function (f) { done[f.i] = 1; f.cells.forEach(function (x) { on[x[0] + ',' + x[1]] = 1; }); });
    var cs = $('gm-grid').children;
    for (var i = 0; i < cs.length; i++) cs[i].classList.toggle('f', !!on[cs[i].getAttribute('data-r') + ',' + cs[i].getAttribute('data-c')]);
    var ul = $('gm-words'); ul.innerHTML = '';
    st.words.forEach(function (w, i) { var li = document.createElement('li'); li.textContent = w; if (done[i]) li.className = 'done'; ul.appendChild(li); });
    $('gm-count').textContent = fmt(S.gm_count, { k: (st.found || []).length, n: st.n });
    var left = Math.max(0, Math.round((Date.parse(st.ends) - Date.now()) / 1000));
    $('gm-time').textContent = Math.floor(left / 60) + ':' + ('0' + (left % 60)).slice(-2);
    if (st.done) $('gm-msg').textContent = S.gm_done;
    else if (st.over || !left) $('gm-msg').textContent = S.gm_over;
  }
  function gmCell(el) { var b = el && el.closest ? el.closest('.gm-c') : null; return b ? [Number(b.getAttribute('data-r')), Number(b.getAttribute('data-c'))] : null; }
  function gmMark(cell, on) { var b = cell && $('gm-grid').children[cell[0] * GM.st.grid.length + cell[1]]; if (b) b.classList.toggle('sel', on); }
  function gmFind(a, b) {
    gmMark(a, false); gmMark(b, false);
    if (!GM.st || GM.st.done || GM.st.over) return;
    if (GM.demo) { var h = GM.demo.find(a, b); gmRender(GM.demo.view()); return gmSay(h >= 0 ? S.gm_hit : S.gm_miss); }
    var fd = new FormData(); fd.append('t', tok); fd.append('room', GM.room); fd.append('a', a.join(',')); fd.append('b', b.join(','));
    fetch(D.worker + '/live/find', { method: 'POST', body: fd }).then(function (r) { return r.json(); })
      .then(function (st) { if (st.grid) gmRender(st); gmSay(st.hit >= 0 ? S.gm_hit : S.gm_miss); }).catch(function () {});
  }
  $('gm-grid').addEventListener('pointerdown', function (e) { var c = gmCell(e.target); if (!c) return; e.preventDefault(); GM.drag = c; gmMark(c, true); });
  $('gm-grid').addEventListener('pointerup', function (e) {
    var start = GM.drag; GM.drag = null;
    var end = gmCell(document.elementFromPoint(e.clientX, e.clientY));
    if (!start || !end) { gmMark(start, false); return; }
    if (end[0] !== start[0] || end[1] !== start[1]) { if (GM.sel) gmMark(GM.sel, false); GM.sel = null; return gmFind(start, end); }   /* a drag */
    if (!GM.sel) { GM.sel = start; return; }                                    /* a tap: the first letter… */
    var first = GM.sel; GM.sel = null;
    if (first[0] === end[0] && first[1] === end[1]) return gmMark(end, false);   /* the same cell twice: cancel */
    gmFind(first, end);                                                        /* …then the last letter */
  });
  $('gm-x').addEventListener('click', function () { gmShow(false); });
  /* the taste build: a self-playing practice board (practice friends find a word now and then; nothing is sent) */
  function gmPractice() {
    var g = D.gameDemo; if (!g) return;
    var found = [], ends = new Date(Date.now() + 8 * 60e3).toISOString();
    function view() {
      return { ok: true, open: true, game: true, grid: g.grid, words: g.words.map(function (x) { return x.w; }), n: g.words.length,
        found: found.map(function (i) { return { i: i, cells: g.words[i].cells }; }), ends: ends, done: found.length === g.words.length,
        over: Date.now() > Date.parse(ends) };
    }
    function find(a, b) {
      var hit = -1;
      g.words.forEach(function (x, i) {
        var f = x.cells[0], l = x.cells[x.cells.length - 1], s1 = f[0] === a[0] && f[1] === a[1] && l[0] === b[0] && l[1] === b[1],
          s2 = f[0] === b[0] && f[1] === b[1] && l[0] === a[0] && l[1] === a[1];
        if (hit < 0 && (s1 || s2) && found.indexOf(i) < 0) hit = i;
      });
      if (hit >= 0) found.push(hit);
      return hit;
    }
    var t = setInterval(function () {                                        /* a practice friend finds one, sometimes */
      var left = g.words.map(function (x, i) { return i; }).filter(function (i) { return found.indexOf(i) < 0; });
      if (left.length > 1 && Math.random() < 0.35) found.push(left[Math.floor(Math.random() * left.length)]);
    }, 4000);
    GM.demo = { view: view, find: find, t: t };
    gmStart(view(), 'taste');
  }
  if ($('gm-practice')) $('gm-practice').addEventListener('click', gmPractice);
  /* ---- end games-v1 */
    window.DaybookGame.start = gmStart;
  }
  window.DaybookGame = { init: init, start: function () {} };
})();
