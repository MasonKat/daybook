// F19.4 the teacher remote ("tap who, speak what"): the phone page's script. The Worker serves it at /t/remote/remote.js;
// the console's taste build publishes the same text (static/remote.js) with a simulated roster. Plain browser JS, kept in
// a String.raw literal: never use a backtick or a dollar-brace sequence in it.
export const REMOTE_JS = String.raw`(function () {
  'use strict';
  var CSS = 'body{margin:0;background:#0b0f17;color:#f2f4f8;font:16px/1.4 -apple-system,system-ui,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;'
  + 'overflow-wrap:anywhere;-webkit-text-size-adjust:100%}*{box-sizing:border-box}[hidden]{display:none!important}'
  + '.top{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:calc(10px + env(safe-area-inset-top)) 16px 8px}'
  + '.top select{min-height:40px;max-width:60%;border-radius:10px;border:1px solid #2a3240;background:#161c27;color:#f2f4f8;font-family:inherit;font-weight:600;font-size:16px;padding:0 8px}'
  + '.note,.net{margin:4px 16px;color:#9aa3b2;font-size:13px}.net:empty,.note:empty{display:none}'
  + '.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:8px 16px calc(24px + env(safe-area-inset-bottom))}'
  + '.who{position:relative;min-height:64px;border:0;border-radius:16px;background:#1a2230;color:#f2f4f8;font-family:inherit;font-weight:700;font-size:20px;padding:8px;'
  + 'display:flex;align-items:center;justify-content:center;cursor:pointer;min-width:0}.who:active{background:#2563eb}.who.whole{grid-column:1/-1;min-height:56px;background:#2b2150}'
  + '.who .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.who .badge{position:absolute;top:6px;right:8px;font-size:12px;font-weight:600;color:#c7d2fe}'
  + '.sheet{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:flex-end;z-index:5}'
  + '.box{width:100%;background:#121826;border-radius:20px 20px 0 0;padding:14px 16px calc(16px + env(safe-area-inset-bottom))}'
  + '.sh-top{display:flex;justify-content:space-between;align-items:center;font-size:22px}.x{border:0;background:none;color:#f2f4f8;font-size:28px;min-width:44px;min-height:44px}'
  + '.pts{display:flex;gap:8px;margin:10px 0}.pts button{flex:1;min-height:52px;border-radius:14px;border:1px solid #2a3240;background:#1a2230;color:#f2f4f8;font-family:inherit;font-weight:800;font-size:20px}'
  + '.pts button[aria-pressed=true]{background:#2563eb;border-color:#2563eb}'
  + 'textarea{width:100%;border-radius:12px;border:1px solid #2a3240;background:#0b0f17;color:#f2f4f8;font-family:inherit;font-size:16px;padding:10px}'
  + '.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.qc{border:0;border-radius:999px;background:#1f2a3c;color:#dbe4ff;font-family:inherit;font-weight:600;font-size:14px;min-height:36px;padding:0 12px}'
  + '.save{width:100%;min-height:52px;border:0;border-radius:14px;background:#2563eb;color:#fff;font-family:inherit;font-weight:800;font-size:18px}'
  + '.toast{position:fixed;left:16px;right:16px;top:calc(8px + env(safe-area-inset-top));background:#1f2937;border-radius:14px;padding:12px 14px;'
  + 'display:flex;justify-content:space-between;align-items:center;gap:8px;z-index:6}.undo{border:0;border-radius:10px;background:#f59e0b;color:#111;font-family:inherit;font-weight:800;font-size:15px;min-height:40px;padding:0 14px}'
  + '.muted{color:#9aa3b2}';
  var st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);
  var tb = document.querySelector('.taste-bar');
  (tb || document.body).insertAdjacentHTML(tb ? 'afterend' : 'afterbegin', '<header class="top"><b>AMP 리모컨</b><select id="cls" aria-label="반"></select></header>'
    + '<p class="note" id="note"></p><p class="net" id="net" aria-live="polite"></p>'
    + '<main class="grid" id="grid"></main>'
    + '<div class="sheet" id="sheet" role="dialog" aria-modal="true" aria-labelledby="sh-name" hidden><div class="box">'
    + '<div class="sh-top"><b id="sh-name"></b><button type="button" class="x" id="sh-x" aria-label="닫기">×</button></div>'
    + '<div class="pts" id="pts" role="group" aria-label="점수"><button type="button" data-p="1" aria-pressed="true">+1</button>'
    + '<button type="button" data-p="2" aria-pressed="false">+2</button><button type="button" data-p="3" aria-pressed="false">+3</button></div>'
    + '<textarea id="txt" rows="3" maxlength="200" placeholder="칭찬 한마디 (키보드의 🎤로 말해도 돼요)" aria-label="칭찬"></textarea>'
    + '<div class="chips" id="chips"></div><button type="button" class="save" id="save">저장</button></div></div>'
    + '<div class="toast" id="toast" role="status" hidden></div>');
  var CFG = JSON.parse(document.getElementById('cfg').textContent || '{}'), TASTE = !!CFG.taste;
  var S = {
    setup: '선생님 탭 → 리모컨 연결의 QR을 이 휴대폰 카메라로 열어 주세요. 한 번만 하면 돼요.',
    bad: '연결 정보가 맞지 않아요. 선생님 탭에서 리모컨 QR을 다시 열어 주세요.',
    offline: '오프라인: 저장한 것은 연결되면 한 번만 보내요',
    pending: '보낼 것 {n}개', saved: '저장했어요', undone: '취소했어요', undo: '취소', tooFast: '잠깐만요 (3초)',
    late: '2분이 지나 취소할 수 없어요', save: '저장', ph: '칭찬 한마디 (키보드의 🎤로 말해도 돼요)', none: '이 반에 학생이 없어요',
    taste: '체험판 · 아무것도 보내지 않아요', whole: '반 전체 👏'
  };
  var Q_KEY = 'remote.q', R_KEY = 'remote.roster', LAST = {};
  var LS = { get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
             set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } } };
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function fmt(s, o) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return o[k] != null ? o[k] : m; }); }
  function kst(ms) { return new Date((ms == null ? Date.now() : ms) + 9 * 3600e3); }
  function rid() { var a = new Uint8Array(8); crypto.getRandomValues(a); return Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join(''); }
  function toast(t, undoId) {
    var b = $('toast'); b.innerHTML = ''; b.appendChild(el('span', '', t));
    if (undoId) { var u = el('button', 'undo', S.undo); u.type = 'button'; u.onclick = function () { undo(undoId); }; b.appendChild(u); }
    b.hidden = false; clearTimeout(toast.h); toast.h = setTimeout(function () { b.hidden = true; }, undoId ? 120000 : 2500);
  }

  // ---- setup: the teacher token and the roster key arrive once in the URL fragment (never sent to a server)
  var h = new URLSearchParams(location.hash.slice(1));
  if (h.get('t') && h.get('k')) { LS.set('remote.t', h.get('t')); LS.set('remote.k', h.get('k')); history.replaceState(null, '', location.pathname); }
  var TOK = LS.get('remote.t') || '', KEY = LS.get('remote.k') || '';

  // ---- the roster blob: nonce(16) | ciphertext | tag(32); keystream = HMAC(kEnc, nonce|i), tag = HMAC(kMac, nonce|ct)
  function b64u(s) { s = s.replace(/-/g, '+').replace(/_/g, '/'); while (s.length % 4) s += '='; var b = atob(s), a = new Uint8Array(b.length); for (var i = 0; i < b.length; i++) a[i] = b.charCodeAt(i); return a; }
  function hmacKey(raw) { return crypto.subtle.importKey('raw', raw, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']); }
  function hmac(k, data) { return crypto.subtle.sign('HMAC', k, data).then(function (b) { return new Uint8Array(b); }); }
  function cat(a, b) { var o = new Uint8Array(a.length + b.length); o.set(a); o.set(b, a.length); return o; }
  function decrypt(blob) {
    var raw = b64u(blob), key = b64u(KEY), nonce = raw.slice(0, 16), ct = raw.slice(16, raw.length - 32), tag = raw.slice(raw.length - 32);
    var enc = new TextEncoder();
    return hmacKey(key).then(function (k) { return Promise.all([hmac(k, enc.encode('enc')), hmac(k, enc.encode('mac'))]); })
      .then(function (ks) { return Promise.all([hmacKey(ks[0]), hmacKey(ks[1])]); })
      .then(function (ks) {
        return hmac(ks[1], cat(nonce, ct)).then(function (t) {
          var same = t.length === tag.length; for (var i = 0; i < t.length; i++) same = same && t[i] === tag[i];
          if (!same) throw new Error('tag');
          var n = Math.ceil(ct.length / 32), jobs = [];
          for (var j = 0; j < n; j++) { var c = new Uint8Array(4); new DataView(c.buffer).setUint32(0, j); jobs.push(hmac(ks[0], cat(nonce, c))); }
          return Promise.all(jobs).then(function (st) {
            var out = new Uint8Array(ct.length);
            for (var p = 0; p < ct.length; p++) out[p] = ct[p] ^ st[Math.floor(p / 32)][p % 32];
            return JSON.parse(new TextDecoder().decode(out));
          });
        });
      });
  }

  // ---- the API (same origin as the Worker), always with the teacher token
  function api(path, body) {
    var o = { headers: { 'X-Teacher-Token': TOK }, cache: 'no-store' };
    if (body) { o.method = 'POST'; o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    return fetch(path, o).then(function (r) { return r.json().then(function (j) { j.status = r.status; return j; }); });
  }

  var R = null, CUR = null, COUNTS = {}, SEL = null, PTS = 1;
  function pickClass() {
    var now = kst(), wd = (now.getUTCDay() + 6) % 7, hm = now.getUTCHours() * 60 + now.getUTCMinutes();
    var today = R.classes.filter(function (c) { return (c.days || []).indexOf(wd) >= 0; });
    function dist(c) { var p = (c.start || '00:00').split(':'); return Math.abs(Number(p[0]) * 60 + Number(p[1]) - hm); }
    today.sort(function (a, b) { return dist(a) - dist(b); });
    return (today[0] || R.classes[0] || {}).key;
  }
  function render() {
    var sel = $('cls'); sel.innerHTML = '';
    var wd = (kst().getUTCDay() + 6) % 7;
    R.classes.slice().sort(function (a, b) { return ((b.days || []).indexOf(wd) >= 0) - ((a.days || []).indexOf(wd) >= 0); }).forEach(function (c) {
      var o = el('option', '', c.name); o.value = c.key; sel.appendChild(o);
    });
    sel.value = CUR;
    var c = R.classes.filter(function (x) { return x.key === CUR; })[0] || { students: [] };
    var g = $('grid'); g.innerHTML = '';
    if (!c.students.length) g.appendChild(el('p', 'muted', S.none));
    else {                                                     // 반 전체: a class shout-out (the class goal, no one student)
      var w = el('button', 'who whole'); w.type = 'button'; w.setAttribute('data-t', '*');
      w.appendChild(el('span', 'nm', S.whole)); if (COUNTS['*']) w.appendChild(el('span', 'badge', '🎤 ' + COUNTS['*']));
      w.onclick = function () { openSheet({ t: '*', label: S.whole }); };
      g.appendChild(w);
    }
    c.students.slice().sort(function (a, b) {
      if (a.seat != null && b.seat != null) return a.seat - b.seat;
      if (a.seat != null) return -1; if (b.seat != null) return 1;
      return a.label.localeCompare(b.label, 'ko');
    }).forEach(function (s) {
      var b = el('button', 'who'); b.type = 'button'; b.setAttribute('data-t', s.t);
      b.appendChild(el('span', 'nm', s.label));
      if (COUNTS[s.t]) b.appendChild(el('span', 'badge', '🎤 ' + COUNTS[s.t]));
      b.onclick = function () { openSheet(s); };
      g.appendChild(b);
    });
    var q = queue();
    $('net').textContent = q.length ? fmt(S.pending, { n: q.length }) + (navigator.onLine === false ? ' · ' + S.offline : '') : '';
  }
  function loadCounts() {
    if (TASTE || !CUR) return Promise.resolve();
    return api('/t/remote/today?class=' + encodeURIComponent(CUR)).then(function (j) { if (j.ok) { COUNTS = j.counts || {}; render(); } }, function () {});
  }
  function openSheet(s) {
    SEL = s; PTS = 1;
    $('sh-name').textContent = s.label;
    Array.prototype.forEach.call(document.querySelectorAll('#pts button'), function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-p') === '1' ? 'true' : 'false'); });
    $('txt').value = ''; $('sheet').hidden = false;
  }
  // ---- the queue: every save gets an id; the Worker ignores an id it already has, so a replay never duplicates
  function queue() { try { return JSON.parse(LS.get(Q_KEY) || '[]') || []; } catch (e) { return []; } }
  function setQueue(q) { LS.set(Q_KEY, JSON.stringify(q)); }
  var flushing = false;
  function flush() {
    if (TASTE || flushing) return Promise.resolve();
    var q = queue(); if (!q.length) { render(); return Promise.resolve(); }
    flushing = true;
    var rec = q[0];
    return api('/t/remote/shout', rec).then(function (j) {
      flushing = false;
      if (j.status === 401) { toast(S.bad); return; }
      setQueue(queue().filter(function (x) { return x.id !== rec.id; }));   // ok, a duplicate, or refused: never resent
      if (j.ok && !j.dup) COUNTS[rec.t] = (COUNTS[rec.t] || 0) + 1;
      render(); return flush();
    }, function () { flushing = false; render(); });   // offline: stays queued
  }
  function save() {
    var now = Date.now();
    if (LAST[SEL.t] && now - LAST[SEL.t] < 3000) { $('sheet').hidden = true; toast(S.tooFast); return; }   // a double tap: the first one counts
    LAST[SEL.t] = now;
    var rec = { id: rid(), t: SEL.t, 'class': CUR, points: PTS, text: $('txt').value.replace(/\s+/g, ' ').trim().slice(0, 200), at: now };
    $('sheet').hidden = true;
    if (TASTE) { COUNTS[rec.t] = (COUNTS[rec.t] || 0) + 1; LAST.rec = rec; render(); toast(S.saved, rec.id); return; }
    var q = queue(); q.push(rec); setQueue(q); LAST.rec = rec;
    toast(S.saved, rec.id); render(); flush();
  }
  function undo(id) {
    var rec = LAST.rec; if (!rec || rec.id !== id) return;
    if (Date.now() - rec.at > 120000) { toast(S.late); return; }
    var q = queue();
    if (TASTE || q.some(function (x) { return x.id === id; })) {           // not sent yet: just drop it
      setQueue(q.filter(function (x) { return x.id !== id; }));
      COUNTS[rec.t] = Math.max(0, (COUNTS[rec.t] || 1) - 1); LAST.rec = null; render(); toast(S.undone); return;
    }
    api('/t/remote/undo', { id: id }).then(function (j) {
      if (j.ok) { COUNTS[rec.t] = Math.max(0, (COUNTS[rec.t] || 1) - 1); LAST.rec = null; render(); toast(S.undone); }
      else toast(S.late);
    }, function () { toast(S.offline); });
  }

  function start(roster) {
    R = roster; CUR = CUR || pickClass();
    var chips = $('chips'); chips.innerHTML = '';
    (R.chips || []).forEach(function (c) {
      var b = el('button', 'qc', c); b.type = 'button';
      b.onclick = function () { var t = $('txt'); t.value = t.value ? t.value.replace(/\s+$/, '') + ' ' + c : c; t.focus(); };
      chips.appendChild(b);
    });
    render(); loadCounts(); flush();
  }
  $('cls').onchange = function () { CUR = $('cls').value; COUNTS = {}; render(); loadCounts(); };
  $('pts').onclick = function (e) {
    var b = e.target.closest('button'); if (!b) return;
    PTS = Number(b.getAttribute('data-p'));
    Array.prototype.forEach.call(document.querySelectorAll('#pts button'), function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
  };
  $('save').onclick = save;
  $('sh-x').onclick = function () { $('sheet').hidden = true; };
  window.addEventListener('online', flush);
  setInterval(function () { flush(); loadCounts(); }, 30000);

  if (TASTE) { $('note').textContent = S.taste; start(CFG.roster); return; }
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/t/remote/sw.js', { scope: '/t/remote/' }).catch(function () {});
  if (!TOK || !KEY) { $('note').textContent = S.setup; return; }
  var cached = LS.get(R_KEY);
  api('/t/remote/roster').then(function (j) {
    if (j.status === 401) { $('note').textContent = S.bad; return; }
    if (!j.blob) { $('note').textContent = S.setup; return; }
    LS.set(R_KEY, j.blob);
    return decrypt(j.blob).then(start, function () { $('note').textContent = S.bad; });
  }, function () {                                                        // offline: the last roster on this phone
    if (cached) decrypt(cached).then(start, function () { $('note').textContent = S.bad; });
    else $('note').textContent = S.offline;
  });
})();
`;

// the page around it (the Worker's /t/remote and the taste build share it; `cfg` is JSON for the script)
export function remoteHtml(cfg, scriptSrc, manifestHref) {
  const safe = JSON.stringify(cfg).replace(/</g, '\\u003c');
  return '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
    + '<meta name="color-scheme" content="dark"><meta name="theme-color" content="#0b0f17">'
    + '<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">'
    + '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="AMP 리모컨">'
    + '<meta name="robots" content="noindex, nofollow">' + (manifestHref ? '<link rel="manifest" href="' + manifestHref + '">' : '')
    + '<title>AMP 학습 기록</title><style>body{margin:0;background:#0b0f17;color:#f2f4f8}</style></head><body>'
    + '<script type="application/json" id="cfg">' + safe + '</script><script src="' + scriptSrc + '" defer></script></body></html>';
}
