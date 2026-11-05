/* games-v1 수학 용어 찾기: a cooperative break-time word search (no dependencies). One view, two screens:
   the phone (app.js calls DaybookGame.init / start when a Live room code is a game) and the board's projector (/game).
   Juice, all hand-written: a glowing stroke over each found word, a toast with its meaning, a 연속 combo for finds within
   10 s, a countdown ring that turns orange in the last 30 s, confetti when the class finds everything. No names anywhere.
   prefers-reduced-motion: no confetti, no drawing animation, no pulsing. */
(function () {
  'use strict';
  var REDUCE = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var SVGNS = 'http://www.w3.org/2000/svg';
  var S = {}, fmt = function (s, o) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return o[k] != null ? o[k] : m; }); };
  function $(id) { return document.getElementById(id); }
  function q(root, sel) { return root.querySelector(sel); }

  /* ---------------------------------------------------------------- the view (phone + projector) */
  function View(root) { this.root = root; this.st = null; this.seen = null; this.lastAt = 0; this.combo = 0; this.party = false; this.toastT = null; }
  View.prototype.build = function (st) {
    var g = q(this.root, '.gm-grid'), n = st.grid.length; g.innerHTML = ''; g.style.setProperty('--n', n);
    for (var r = 0; r < n; r++) for (var c = 0; c < n; c++) {
      var b = document.createElement('button'); b.type = 'button'; b.className = 'gm-c'; b.textContent = st.grid[r].charAt(c);
      b.setAttribute('data-r', r); b.setAttribute('data-c', c); g.appendChild(b);
    }
    var svg = document.createElementNS(SVGNS, 'svg'); svg.setAttribute('class', 'gm-lines'); svg.setAttribute('aria-hidden', 'true');
    g.appendChild(svg);
    this.seen = null; this.party = false;
  };
  View.prototype.center = function (rc) {
    var n = this.st.grid.length, b = q(this.root, '.gm-grid').children[rc[0] * n + rc[1]];
    return [b.offsetLeft + b.offsetWidth / 2, b.offsetTop + b.offsetHeight / 2, b.offsetWidth];
  };
  View.prototype.line = function (cells, fresh) {
    var svg = q(this.root, '.gm-lines'), a = this.center(cells[0]), z = this.center(cells[cells.length - 1]);
    var l = document.createElementNS(SVGNS, 'line');
    l.setAttribute('x1', a[0]); l.setAttribute('y1', a[1]); l.setAttribute('x2', z[0]); l.setAttribute('y2', z[1]);
    l.setAttribute('stroke-width', Math.round(a[2] * 0.62)); l.setAttribute('class', 'gm-line' + (fresh && !REDUCE ? ' new' : ''));
    if (fresh && !REDUCE) { var len = Math.hypot(z[0] - a[0], z[1] - a[1]) + 1; l.style.strokeDasharray = len; l.style.strokeDashoffset = len; }
    svg.appendChild(l);
    if (fresh && !REDUCE) requestAnimationFrame(function () { requestAnimationFrame(function () { l.style.strokeDashoffset = '0'; }); });
  };
  View.prototype.toast = function (text) {
    var t = q(this.root, '.gm-toast'); if (!t) return;
    t.textContent = text; t.hidden = false; t.classList.remove('show'); void t.offsetWidth; t.classList.add('show');
    clearTimeout(this.toastT); this.toastT = setTimeout(function () { t.hidden = true; }, 2800);
  };
  View.prototype.paint = function (st) {
    this.st = st;
    var self = this, first = this.seen === null, seen = this.seen || {}, now = Date.now(), fresh = [];
    if (first) { var svg0 = q(this.root, '.gm-lines'); if (svg0) svg0.innerHTML = ''; }
    var on = {}, done = {};
    (st.found || []).forEach(function (f) {
      done[f.i] = 1; f.cells.forEach(function (x) { on[x[0] + ',' + x[1]] = 1; });
      if (!seen[f.i]) { seen[f.i] = 1; self.line(f.cells, !first); if (!first) fresh.push(f.i); }
    });
    this.seen = seen;
    var cs = q(this.root, '.gm-grid').children;
    for (var i = 0; i < cs.length; i++) if (cs[i].classList) cs[i].classList.toggle('f', !!on[cs[i].getAttribute('data-r') + ',' + cs[i].getAttribute('data-c')]);
    var ul = q(this.root, '.gm-words'); ul.innerHTML = '';
    st.words.forEach(function (w, k) { var li = document.createElement('li'); li.textContent = w; if (done[k]) li.className = 'done'; ul.appendChild(li); });
    q(this.root, '.gm-count').textContent = fmt(S.gm_count, { k: (st.found || []).length, n: st.n });
    if (fresh.length) {                                                 /* a new find: its meaning, and the combo */
      var k = fresh[fresh.length - 1];
      this.toast('✨ ' + st.words[k] + (st.means && st.means[k] ? ' — ' + st.means[k] : ''));
      this.combo = now - this.lastAt <= 10000 ? this.combo + fresh.length : fresh.length;
      this.lastAt = now;
      var cb = q(this.root, '.gm-combo');
      if (cb) { cb.hidden = this.combo < 2; if (this.combo >= 2) { cb.textContent = fmt(S.gm_combo, { n: this.combo }); cb.classList.remove('pop'); void cb.offsetWidth; cb.classList.add('pop'); } }
    } else if (now - this.lastAt > 10000) { var cb2 = q(this.root, '.gm-combo'); if (cb2) cb2.hidden = true; this.combo = 0; }
    this.clock();
    var msg = q(this.root, '.gm-msg');
    if (st.done) { msg.textContent = S.gm_done; if (!this.party) { this.party = true; if (!first || st.found.length) confetti(); } }
    else if (st.over) msg.textContent = S.gm_over;
  };
  View.prototype.clock = function () {
    var st = this.st; if (!st) return;
    var left = Math.max(0, Math.round((Date.parse(st.ends) - Date.now()) / 1000)), total = st.secs || 480;
    q(this.root, '.gm-time').textContent = Math.floor(left / 60) + ':' + ('0' + (left % 60)).slice(-2);
    var ring = q(this.root, '.gm-ring-fg'), box = q(this.root, '.gm-clock');
    if (ring) ring.style.strokeDashoffset = String(100 - Math.max(0, Math.min(100, 100 * left / total)));
    if (box) box.classList.toggle('late', left <= 30 && !st.done);
  };
  function confetti() {                                                 /* one burst when the class finishes */
    if (REDUCE) return;
    var cv = document.createElement('canvas'), cx = cv.getContext('2d'), w = innerWidth, h = innerHeight, d = Math.min(devicePixelRatio || 1, 1.5);
    cv.className = 'gm-confetti'; cv.width = w * d; cv.height = h * d; cx.setTransform(d, 0, 0, d, 0, 0); document.body.appendChild(cv);
    var C = ['#7c3aed', '#ec4899', '#f59e0b', '#10b981', '#0ea5e9', '#ef4444'], ps = [];
    for (var i = 0; i < 90; i++) ps.push({ x: w / 2, y: h * 0.35, vx: (Math.random() - 0.5) * 9, vy: -Math.random() * 9 - 3, s: 4 + Math.random() * 5, c: C[i % 6], r: Math.random() * 6, vr: (Math.random() - 0.5) * 0.4 });
    var t0 = performance.now();
    (function step(t) {
      cx.clearRect(0, 0, w, h);
      ps.forEach(function (p) { p.vy += 0.25; p.x += p.vx; p.y += p.vy; p.r += p.vr; cx.save(); cx.translate(p.x, p.y); cx.rotate(p.r); cx.fillStyle = p.c; cx.fillRect(-p.s / 2, -p.s / 4, p.s, p.s / 2); cx.restore(); });
      if (t - t0 < 2200) requestAnimationFrame(step); else cv.remove();
    })(t0);
  }

  /* ---------------------------------------------------------------- the phone */
  var D, tok, V, GM = { room: null, timer: null, tick: null, sel: null, drag: null, demo: null, msgT: null };
  function gmShow(on) {
    $('gm').hidden = !on;
    if (!on) { clearInterval(GM.timer); clearInterval(GM.tick); GM.timer = GM.tick = null; GM.room = null; if (GM.demo) { clearInterval(GM.demo.t); GM.demo = null; } }
  }
  function gmStart(st, code) {
    GM.room = code; GM.sel = null; V.build(st); gmShow(true); V.paint(st);
    GM.timer = setInterval(gmPoll, 3000); GM.tick = setInterval(function () { V.clock(); }, 1000);
  }
  function gmPoll() {
    if (GM.demo) return gmRender(GM.demo.view());
    if (!GM.room) return;
    fetch(D.worker + '/live/state?t=' + tok + '&room=' + GM.room, { cache: 'no-store' }).then(function (r) { return r.json(); }).then(gmRender).catch(function () {});
  }
  function gmSay(t) { var m = $('gm').querySelector('.gm-msg'); m.textContent = t; clearTimeout(GM.msgT); GM.msgT = setTimeout(function () { m.textContent = ''; if (V.st) V.paint(V.st); }, 2000); }
  function gmRender(st) {
    if (!st) return;
    if (!st.open) { $('gm').querySelector('.gm-msg').textContent = S.gm_closed; clearInterval(GM.timer); setTimeout(function () { gmShow(false); }, 2500); return; }
    V.paint(st);
  }
  function gmCell(el) { var b = el && el.closest ? el.closest('.gm-c') : null; return b ? [Number(b.getAttribute('data-r')), Number(b.getAttribute('data-c'))] : null; }
  function gmMark(cell, on) { var b = cell && V.st && $('gm-grid').children[cell[0] * V.st.grid.length + cell[1]]; if (b) b.classList.toggle('sel', on); }
  function gmFind(a, b) {
    gmMark(a, false); gmMark(b, false);
    if (!V.st || V.st.done || V.st.over) return;
    if (GM.demo) { var h = GM.demo.find(a, b); gmRender(GM.demo.view()); if (h < 0) gmSay(S.gm_miss); return; }
    var fd = new FormData(); fd.append('t', tok); fd.append('room', GM.room); fd.append('a', a.join(',')); fd.append('b', b.join(','));
    fetch(D.worker + '/live/find', { method: 'POST', body: fd }).then(function (r) { return r.json(); })
      .then(function (st) { if (st.grid) gmRender(st); if (!(st.hit >= 0)) gmSay(S.gm_miss); }).catch(function () {});
  }
  function gmPractice() {                                               /* taste: practice friends find a word now and then */
    var g = D.gameDemo; if (!g) return;
    var found = [], secs = 8 * 60, ends = new Date(Date.now() + secs * 1000).toISOString();
    function view() {
      return { ok: true, open: true, game: true, grid: g.grid, words: g.words.map(function (x) { return x.w; }), means: g.words.map(function (x) { return x.m || ''; }),
        n: g.words.length, secs: secs, found: found.map(function (i) { return { i: i, cells: g.words[i].cells }; }), ends: ends,
        done: found.length === g.words.length, over: Date.now() > Date.parse(ends) };
    }
    function find(a, b) {
      var hit = -1;
      g.words.forEach(function (x, i) {
        var f = x.cells[0], l = x.cells[x.cells.length - 1];
        var s1 = f[0] === a[0] && f[1] === a[1] && l[0] === b[0] && l[1] === b[1], s2 = f[0] === b[0] && f[1] === b[1] && l[0] === a[0] && l[1] === a[1];
        if (hit < 0 && (s1 || s2) && found.indexOf(i) < 0) hit = i;
      });
      if (hit >= 0) found.push(hit);
      return hit;
    }
    var t = setInterval(function () {
      var left = g.words.map(function (x, i) { return i; }).filter(function (i) { return found.indexOf(i) < 0; });
      if (left.length > 1 && Math.random() < 0.35) { found.push(left[Math.floor(Math.random() * left.length)]); gmRender(view()); }
    }, 4000);
    GM.demo = { view: view, find: find, t: t };
    gmStart(view(), 'taste');
  }
  function init(ctx) {
    D = ctx.D; S = ctx.S; tok = ctx.tok; if (ctx.fmt) fmt = ctx.fmt;
    if (!$('gm')) return;
    V = new View($('gm'));
    $('gm-grid').addEventListener('pointerdown', function (e) { var c = gmCell(e.target); if (!c) return; e.preventDefault(); GM.drag = c; gmMark(c, true); });
    $('gm-grid').addEventListener('pointerup', function (e) {
      var start = GM.drag; GM.drag = null;
      var end = gmCell(document.elementFromPoint(e.clientX, e.clientY));
      if (!start || !end) { gmMark(start, false); return; }
      if (end[0] !== start[0] || end[1] !== start[1]) { if (GM.sel) gmMark(GM.sel, false); GM.sel = null; return gmFind(start, end); }
      if (!GM.sel) { GM.sel = start; return; }
      var first = GM.sel; GM.sel = null;
      if (first[0] === end[0] && first[1] === end[1]) return gmMark(end, false);
      gmFind(first, end);
    });
    $('gm-x').addEventListener('click', function () { gmShow(false); });
    if ($('gm-practice')) $('gm-practice').addEventListener('click', gmPractice);
    window.DaybookGame.start = gmStart;
  }

  /* ---------------------------------------------------------------- the projector (board /game): the grid big, no names */
  function projector() {
    var data = JSON.parse($('data').textContent); S = data.S;
    var PV = new View($('gb')), room = null, timer = null, built = false;
    function api(path, body) {
      var o = body === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
      return fetch(path, o).then(function (r) { return r.json(); });
    }
    function show(st) {
      if (!st || !st.open) { $('gb-room').hidden = true; $('gb-setup').hidden = false; clearInterval(timer); built = false; return; }
      $('gb-setup').hidden = true; $('gb-room').hidden = false;
      if (!built) { PV.build(st); built = true; }
      PV.paint(st);
    }
    $('gb-open').onclick = function () {
      api('/api/game/open', { round: $('gb-round').value, minutes: Number($('gb-min').value) }).then(function (j) {
        if (!j.ok) { $('gb-msg').textContent = j.error || ''; return; }
        room = j.room; $('gb-code').textContent = j.room;
        api('/api/game/state').then(show); clearInterval(timer);
        timer = setInterval(function () { api('/api/game/state').then(show); }, 2000);
        setInterval(function () { PV.clock(); }, 1000);
      });
    };
    $('gb-close').onclick = function () { api('/api/game/close', {}).then(function (j) { $('gb-msg').textContent = j.message || ''; show(null); }); };
    api('/api/game/rounds').then(function (j) {
      (j.rounds || []).forEach(function (r) { var o = document.createElement('option'); o.value = r; o.textContent = r; $('gb-round').appendChild(o); });
      if (j.room) { room = j.room; $('gb-code').textContent = j.room; api('/api/game/state').then(show); timer = setInterval(function () { api('/api/game/state').then(show); }, 2000); setInterval(function () { PV.clock(); }, 1000); }
    });
  }

  window.DaybookGame = { init: init, start: function () {}, View: View };
  if (document.body && document.body.getAttribute('data-page') === 'gameboard') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', projector); else projector();
  }
})();
