// F22.2 first-visit tutorial (coach marks): a dimmed overlay spotlights real elements in turn, with a short caption, an
// arrow, 다음 / 건너뛰기 and a dot counter. One source: the console publishes it as /static/tour.js (student and parent pages)
// and the Worker serves it as /t/remote/tour.js. Plain browser JS in a String.raw literal: never a backtick or a dollar-brace.
export const TOUR_JS = String.raw`(function () {
  'use strict';
  var CSS = '.tour-hole{position:fixed;z-index:9001;border-radius:14px;box-shadow:0 0 0 9999px rgba(5,8,15,.62);pointer-events:none;transition:all .25s ease}'
    + '.tour-cap{position:fixed;z-index:9002;left:12px;right:12px;max-width:420px;margin:0 auto;background:#fff;color:#111827;border-radius:16px;'
    + 'padding:14px 14px 10px;box-shadow:0 8px 30px rgba(0,0,0,.35);font:15px/1.5 -apple-system,system-ui,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}'
    + '.tour-cap .t-arrow{display:block;font-size:18px;line-height:1;color:#7c3aed;margin-bottom:2px}'
    + '.tour-cap .t-key{display:inline-block;background:#7c3aed;color:#fff;border-radius:999px;padding:1px 8px;font-size:12px;font-weight:700;margin-bottom:4px}'
    + '.tour-cap p{margin:0 0 10px;font-weight:600}.tour-row{display:flex;align-items:center;justify-content:space-between;gap:8px}'
    + '.tour-dots{display:flex;gap:5px}.tour-dots i{width:7px;height:7px;border-radius:50%;background:#d1d5db}.tour-dots i.on{background:#7c3aed}'
    + '.tour-btns{display:flex;gap:6px}.tour-btns button{min-height:40px;border-radius:999px;border:0;padding:0 14px;font-family:inherit;font-weight:700;font-size:14px;cursor:pointer}'
    + '.tour-skip{background:#f3f4f6;color:#374151}.tour-next{background:#7c3aed;color:#fff}'
    + '@media (prefers-color-scheme: dark){.tour-cap{background:#1a2230;color:#f2f4f8}.tour-skip{background:#2a3240;color:#e5e7eb}.tour-dots i{background:#3b4556}}'
    + '@media (prefers-reduced-motion: reduce){.tour-hole{transition:none}}';
  var STORE = (function () {
    try { var k = '__t'; localStorage.setItem(k, '1'); localStorage.removeItem(k); return localStorage; } catch (e) { /* no localStorage */ }
    try { return sessionStorage; } catch (e2) { return null; }
  })();
  function seen(key) { try { return STORE && STORE.getItem(key) === '1'; } catch (e) { return false; } }
  function mark(key) { try { if (STORE) STORE.setItem(key, '1'); } catch (e) { /* ignore */ } }
  function visible(el) { if (!el) return false; var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }
  function find(sel) {
    if (!sel) return null;
    var parts = sel.split('||');
    for (var i = 0; i < parts.length; i++) {
      var list = document.querySelectorAll(parts[i].trim());
      for (var j = 0; j < list.length; j++) if (visible(list[j])) return list[j];
    }
    return null;
  }
  var cur = null;
  function close(key) {
    if (!cur) return;
    cur.hole.remove(); cur.cap.remove(); cur = null; mark(key);
    document.removeEventListener('keydown', onKey);
  }
  function onKey(e) { if (cur && e.key === 'Escape') cur.skip(); }
  function start(steps, key, L, force) {
    if (cur || (!force && seen(key))) return false;
    steps = (steps || []).filter(function (s) { return !s.optional || find(s.sel); });
    if (!steps.length) return false;
    if (!document.getElementById('tour-css')) { var st = document.createElement('style'); st.id = 'tour-css'; st.textContent = CSS; document.head.appendChild(st); }
    var hole = document.createElement('div'); hole.className = 'tour-hole'; hole.setAttribute('aria-hidden', 'true');
    var cap = document.createElement('div'); cap.className = 'tour-cap'; cap.setAttribute('role', 'dialog'); cap.setAttribute('aria-live', 'polite');
    document.body.appendChild(hole); document.body.appendChild(cap);
    var i = 0;
    cur = { hole: hole, cap: cap, skip: function () { close(key); } };
    function show() {
      var s = steps[i], el = find(s.sel);
      if (el && el.scrollIntoView) el.scrollIntoView({ block: 'center' });
      var r = el ? el.getBoundingClientRect() : null, vh = window.innerHeight;
      if (r) { hole.style.display = ''; hole.style.left = (r.left - 6) + 'px'; hole.style.top = (r.top - 6) + 'px'; hole.style.width = (r.width + 12) + 'px'; hole.style.height = (r.height + 12) + 'px'; }
      else { hole.style.display = ''; hole.style.left = '50%'; hole.style.top = '50%'; hole.style.width = '0'; hole.style.height = '0'; }
      cap.innerHTML = '';
      var below = !r || r.bottom + 190 < vh;
      var ar = document.createElement('span'); ar.className = 't-arrow'; ar.textContent = r ? (below ? '▲' : '▼') : ''; cap.appendChild(ar);
      if (s.key) { var k = document.createElement('span'); k.className = 't-key'; k.textContent = L.key; cap.appendChild(k); }
      var p = document.createElement('p'); p.textContent = s.text; cap.appendChild(p);
      var row = document.createElement('div'); row.className = 'tour-row';
      var dots = document.createElement('span'); dots.className = 'tour-dots';
      for (var d = 0; d < steps.length; d++) { var dot = document.createElement('i'); if (d === i) dot.className = 'on'; dots.appendChild(dot); }
      var btns = document.createElement('span'); btns.className = 'tour-btns';
      var sk = document.createElement('button'); sk.type = 'button'; sk.className = 'tour-skip'; sk.textContent = L.skip; sk.onclick = cur.skip;
      var nx = document.createElement('button'); nx.type = 'button'; nx.className = 'tour-next'; nx.textContent = i < steps.length - 1 ? L.next : L.done;
      nx.onclick = function () { if (i < steps.length - 1) { i++; show(); } else close(key); };
      btns.appendChild(sk); btns.appendChild(nx); row.appendChild(dots); row.appendChild(btns); cap.appendChild(row);
      if (r && below) { cap.style.top = Math.min(vh - 170, r.bottom + 14) + 'px'; cap.style.bottom = ''; }
      else if (r) { cap.style.top = ''; cap.style.bottom = Math.min(vh - 170, vh - r.top + 14) + 'px'; }
      else { cap.style.top = '35%'; cap.style.bottom = ''; }
      cap.setAttribute('data-step', String(i));
      nx.focus();
    }
    document.addEventListener('keydown', onKey);
    show();
    return true;
  }
  window.DaybookTour = { start: start, seen: seen, find: find };
})();
`;
