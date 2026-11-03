/* DAYBOOK pages: student (today / videos / me), go (handout QR), board (localhost). No dependencies. */
(function () {
  'use strict';

  var TOKEN_RE = /^[a-z2-7]{26}$/;
  var LS = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  };
  function $(id) { return document.getElementById(id); }
  function fmt(s, o) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return o[k] != null ? o[k] : m; }); }
  function kstDay(ms) { return new Date((ms == null ? Date.now() : ms) + 9 * 3600e3).toISOString().slice(0, 10); }
  function addDays(day, n) {
    var d = new Date(day + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }
  function weekday(day) { return (new Date(day + 'T00:00:00Z').getUTCDay() + 6) % 7; } /* Mon = 0 */
  function data() { return JSON.parse($('data').textContent); }

  var page = document.body.getAttribute('data-page');
  if (page === 'go') go();
  else if (page === 'student') student();
  else if (page === 'board') board();

  /* ------------------------------------------------------------ /go/?set=... */
  function go() {
    var tok = LS.get('daybook.token');
    var set = (new URLSearchParams(location.search).get('set') || '').replace(/[^\w\-]/g, '').slice(0, 80);
    if (tok && TOKEN_RE.test(tok)) {
      location.replace('/s/' + tok + '/' + (set ? '?set=' + encodeURIComponent(set) : '') + '#today');
    }
  }

  /* ------------------------------------------------------------ student page */
  function student() {
    var D = data();
    var S = D.S;
    var WD = S.weekdays.split(',');
    var m = location.pathname.match(/\/s\/([a-z2-7]{26})\//);
    var tok = m ? m[1] : '';
    if (tok) LS.set('daybook.token', tok);
    var set = (new URLSearchParams(location.search).get('set') || '').replace(/[^\w\-]/g, '').slice(0, 80);
    var today = kstDay();

    /* tabs */
    var tabs = ['videos', 'today', 'me'];
    function showTab(name) {
      if (tabs.indexOf(name) < 0) name = 'today';
      tabs.forEach(function (t) {
        $('tab-' + t).hidden = t !== name;
        $('t-' + t).setAttribute('aria-selected', t === name ? 'true' : 'false');
      });
    }
    tabs.forEach(function (t) {
      $('t-' + t).addEventListener('click', function () {
        history.replaceState(null, '', location.pathname + location.search + '#' + t);
        showTab(t);
      });
    });
    showTab((location.hash || '#today').slice(1));

    /* header of the today tab */
    $('today-date').textContent = Number(today.slice(5, 7)) + '월 ' + Number(today.slice(8, 10)) + '일 (' + WD[weekday(today)] + ')';
    if (set) { $('today-set').textContent = fmt(S.today_set, { set: set }); $('today-set').hidden = false; }

    /* known submission days: published + this device + the Worker */
    var known = {};
    var LKEY = 'daybook.days.' + tok;
    (D.subDays || []).forEach(function (d) { known[d] = 1; });
    try { (JSON.parse(LS.get(LKEY) || '[]') || []).forEach(function (d) { known[d] = 1; }); } catch (e) { /* ignore */ }
    function remember(days) {
      (days || []).forEach(function (d) { if (/^\d{4}-\d{2}-\d{2}$/.test(d)) known[d] = 1; });
      var keep = Object.keys(known).filter(function (d) { return d >= addDays(today, -21); }).sort();
      LS.set(LKEY, JSON.stringify(keep));
    }
    function isClassDay(day) {
      if (!D.mask) return false;
      if (D.termStart && day < D.termStart) return false;
      if (D.termEnd && day > D.termEnd) return false;
      return !!(D.mask & (1 << weekday(day)));
    }
    /* same rule as console/models.py streak(): days after the publish date are computed here */
    function streakNow() {
      var n = 0, d = today, stop = addDays(D.pub, -1);
      while (d > stop) {
        if (known[d]) n++;
        else if (isClassDay(d) && d !== today) return n;
        d = addDays(d, -1);
      }
      return n + (D.streakPrev || 0);
    }
    function renderMe() {
      var lis = $('me-dots').getElementsByTagName('li');
      for (var i = 0; i < lis.length; i++) {
        var d = lis[i].getAttribute('data-date');
        if (known[d]) { lis[i].classList.add('on'); lis[i].querySelector('.dot').textContent = '✓'; }
      }
      $('me-streak').textContent = fmt(S.streak_label, { n: streakNow() });
    }

    /* states */
    var states = ['idle', 'busy', 'done', 'err'];
    function show(state) { states.forEach(function (s) { $('st-' + s).hidden = s !== state; }); }
    function showDone(replaced) {
      $('d-replaced').hidden = !replaced;
      $('d-streak').textContent = fmt(S.streak_label, { n: streakNow() });
      $('d-class').textContent = fmt(S.class_plus, { n: D.classTotal || 0 });
      $('d-reward').textContent = D.rewardLabel ? fmt(S.reward_to_go, { label: D.rewardLabel, n: D.toGo }) : '';
      show('done');
    }
    function showErr(msg) { $('err-msg').textContent = msg; show('err'); }

    if (known[today]) showDone(false);
    renderMe();
    if (D.worker && tok) {
      fetch(D.worker + '/status?t=' + tok, { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (!j || !j.days) return;
          remember(j.days);
          renderMe();
          if (known[today] && $('st-busy').hidden) showDone(false);
        })
        .catch(function () { /* offline: the published data is enough */ });
    }

    $('photo').addEventListener('change', function (e) {
      var f = e.target.files && e.target.files[0];
      e.target.value = '';
      if (!f) return;
      if (!D.worker) { showErr(S.no_worker); return; }
      show('busy');
      var url = URL.createObjectURL(f);
      $('preview').src = url;
      shrink(f)
        .then(function (blob) {
          var fd = new FormData();
          fd.append('t', tok);
          if (set) fd.append('set', set);
          var ext = blob.type === 'image/png' ? 'png' : (/hei[cf]/.test(blob.type) ? 'heic' : 'jpg');
          fd.append('photo', blob, 'photo.' + ext);
          return fetch(D.worker + '/submit', { method: 'POST', body: fd });
        })
        .then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (j) { j.status = r.status; return j; });
        })
        .then(function (j) {
          URL.revokeObjectURL(url);
          if (j.ok) {
            if (j.day) today = j.day;
            known[today] = 1;
            remember((j.days || []).concat([today]));
            renderMe();
            showDone(!!j.already);
          } else {
            showErr(j.status === 429 ? S.upload_rate : S.upload_error);
          }
        })
        .catch(function () {
          URL.revokeObjectURL(url);
          showErr(navigator.onLine === false ? S.upload_offline : S.upload_error);
        });
    });
  }

  /* Downscale in the browser: long edge 1600 px, JPEG 0.82 (also turns HEIC into JPEG where decodable). */
  function decodeViaImg(file) {
    return new Promise(function (res, rej) {
      var u = URL.createObjectURL(file), im = new Image();
      im.onload = function () { res(im); };
      im.onerror = function () { URL.revokeObjectURL(u); rej(new Error('decode')); };
      im.src = u;
    });
  }
  function decode(file) {
    if (window.createImageBitmap) {
      return createImageBitmap(file, { imageOrientation: 'from-image' }).catch(function () { return decodeViaImg(file); });
    }
    return decodeViaImg(file);
  }
  function toBlob(c, q) {
    return new Promise(function (res, rej) {
      c.toBlob(function (b) { if (b) res(b); else rej(new Error('blob')); }, 'image/jpeg', q);
    });
  }
  function shrink(file) {
    return decode(file).then(function (img) {
      var w = img.width, h = img.height, s = Math.min(1, 1600 / Math.max(w, h));
      var c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(w * s));
      c.height = Math.max(1, Math.round(h * s));
      var g = c.getContext('2d');
      g.fillStyle = '#fff';
      g.fillRect(0, 0, c.width, c.height);
      g.drawImage(img, 0, 0, c.width, c.height);
      if (img.close) img.close();
      return toBlob(c, 0.82).then(function (b) { return b.size > 1e6 ? toBlob(c, 0.6) : b; });
    }).catch(function () {
      if (file.size <= 8 * 1024 * 1024) return file; /* the Worker accepts jpeg/png/heic up to 8 MB */
      throw new Error('too_big');
    });
  }

  /* ------------------------------------------------------------ board (127.0.0.1 only) */
  function board() {
    var D = data();
    function post(path, body) {
      return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        .then(function (r) { return r.json(); });
    }
    function apply(st) {
      if (!st || !st.students) return;
      var rows = $('roster').querySelectorAll('.row');
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i], s = st.students[row.getAttribute('data-id')];
        if (!s) continue;
        row.classList.toggle('on', !!s.done);
        row.querySelector('.sub').textContent = s.done ? '✓ ' + s.time : '';
        row.querySelector('.subfor').hidden = !!s.done;
        var bs = row.querySelectorAll('.pt');
        for (var j = 0; j < bs.length; j++) bs[j].classList.toggle('sel', Number(bs[j].getAttribute('data-p')) === s.points);
      }
      $('b-fill').style.width = st.pct.toFixed(1) + '%';
      $('b-togo').textContent = st.togo;
      $('b-three').textContent = st.three;
    }
    $('roster').addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      var id = Number(b.closest('.row').getAttribute('data-id'));
      if (b.classList.contains('pt')) post('/api/attitude', { student_id: id, points: Number(b.getAttribute('data-p')) }).then(apply);
      else if (b.classList.contains('subfor')) post('/api/submit-for', { student_id: id }).then(apply);
    });
    setInterval(function () {
      fetch('/api/state', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(apply).catch(function () {});
    }, (D.refresh || 10) * 1000);
  }
})();
