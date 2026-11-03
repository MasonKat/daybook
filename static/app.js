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
  /* FNV-1a 32-bit in base 36; same as console/generate.py set_hash() */
  function fnv(str) {
    var h = 0x811c9dc5;
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return h.toString(36);
  }

  var page = document.body.getAttribute('data-page');
  if (page === 'go') go();
  else if (page === 'student') student();
  else if (page === 'parent') parent();
  else if (page === 'board') board();

  /* ------------------------------------------------------------ /go/?set=... */
  function go() {
    var tok = LS.get('daybook.token');
    var set = (new URLSearchParams(location.search).get('set') || '').replace(/[^\w\-]/g, '').slice(0, 80);
    if (tok && TOKEN_RE.test(tok)) {
      location.replace('/s/' + tok + '/' + (set ? '?set=' + encodeURIComponent(set) : '') + '#today');
    }
  }

  /* ------------------------------------------------------------ parent room: open at the latest week */
  function parent() {
    try { history.scrollRestoration = 'manual'; } catch (e) { /* old browsers */ }
    var toBottom = function () { window.scrollTo(0, document.documentElement.scrollHeight); };
    toBottom();
    window.addEventListener('load', toBottom);
  }

  /* ------------------------------------------------------------ student page */
  function student() {
    var D = data();
    var S = D.S;
    var WD = S.weekdays.split(',');
    var m = location.pathname.match(/\/s\/([a-z2-7]{26})\//);
    var TASTE = !!D.taste;   /* taste build (F1): no Worker, nothing leaves this browser */
    var tok = TASTE ? 'kong' : (m ? m[1] : '');
    if (tok && !TASTE) LS.set('daybook.token', tok);
    var set = (new URLSearchParams(location.search).get('set') || '').replace(/[^\w\-]/g, '').slice(0, 80);
    var today = kstDay();
    var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* tabs (bottom nav); the stories row sits on top of 영상 and 오늘 */
    var tabs = ['videos', 'today', 'me'];
    function showTab(name) {
      if (tabs.indexOf(name) < 0) name = 'today';
      tabs.forEach(function (t) {
        $('tab-' + t).hidden = t !== name;
        $('t-' + t).setAttribute('aria-selected', t === name ? 'true' : 'false');
      });
      $('stories').hidden = name === 'me';
      $('s-main').classList.toggle('feed-mode', name === 'videos' && !!($('feed') || $('longs')));
      if (name !== 'videos') stopVideos();
    }
    tabs.forEach(function (t) {
      $('t-' + t).addEventListener('click', function () {
        history.replaceState(null, '', location.pathname + location.search + '#' + t);
        showTab(t);
      });
    });
    showTab((location.hash || '#today').slice(1));

    $('today-date').textContent = Number(today.slice(5, 7)) + '월 ' + Number(today.slice(8, 10)) + '일 (' + WD[weekday(today)] + ')';
    if (set) {
      /* never show the raw set id: the page only knows hash(set_id) -> label */
      var label = (D.sets || {})[fnv(set)] || fmt(S.set_default, { md: Number(today.slice(5, 7)) + '/' + Number(today.slice(8, 10)) });
      $('today-set').textContent = fmt(S.today_set, { label: label });
      $('today-set').hidden = false;
    }

    /* known submission days: published + this device + the Worker */
    var published = {}, known = {};
    var LKEY = 'daybook.days.' + tok;
    (D.subDays || []).forEach(function (d) { published[d] = 1; known[d] = 1; });
    try { (JSON.parse(LS.get(LKEY) || '[]') || []).forEach(function (d) { known[d] = 1; }); } catch (e) { /* ignore */ }
    function remember(days) {
      (days || []).forEach(function (d) { if (/^\d{4}-\d{2}-\d{2}$/.test(d) && d <= today) known[d] = 1; });
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
    function nSubNow() {
      var extra = Object.keys(known).filter(function (d) { return d >= D.pub && !published[d]; }).length;
      return (D.nSub || 0) + extra;
    }
    function setText(sel, text) {
      var els = document.querySelectorAll(sel);
      for (var i = 0; i < els.length; i++) els[i].textContent = text;
    }
    function renderLive() {
      var n = streakNow();
      setText('[data-live="streak"]', n);
      setText('[data-live="streak-label"]', fmt(S.story_streak, { n: n }));
      $('c-streak').textContent = n;
      $('c-sub').textContent = nSubNow();
      /* profile tiles: add the days since publish, then colour every known day */
      var tiles = $('tiles'), firstTile = tiles.querySelector('.tile');
      var top = firstTile ? firstTile.getAttribute('data-date') : addDays(today, -1);
      for (var d = addDays(top, 1); d <= today; d = addDays(d, 1)) {
        var li = document.createElement('li');
        li.className = 'tile off';
        li.setAttribute('data-date', d);
        li.innerHTML = '<span class="tl tnum">' + Number(d.slice(5, 7)) + '/' + Number(d.slice(8, 10)) + '</span><span class="tw">' + WD[weekday(d)] + '</span>';
        var head = tiles.firstElementChild;
        if (head && head.className === 'mhead' && head.id === 'm-' + d.slice(0, 7)) tiles.insertBefore(li, head.nextSibling);
        else {
          var mh = document.createElement('li');
          mh.className = 'mhead';
          mh.id = 'm-' + d.slice(0, 7);
          mh.textContent = Number(d.slice(5, 7)) + '월';
          tiles.insertBefore(li, tiles.firstElementChild);
          tiles.insertBefore(mh, li);
        }
      }
      var lis = tiles.querySelectorAll('.tile');
      for (var i = 0; i < lis.length; i++) {
        var day = lis[i].getAttribute('data-date'), cl = lis[i].classList;
        if (cl.contains('hl')) continue;                       /* a 3-point day stays the highlight */
        var wasMiss = cl.contains('miss');
        cl.remove('sub', 'open', 'miss', 'off');
        cl.add(known[day] ? 'sub' : day === today ? 'open' : (wasMiss || (isClassDay(day) && day < today)) ? 'miss' : 'off');
        var md = lis[i].querySelector('.mdot');
        if (md && !cl.contains('miss')) md.parentNode.removeChild(md);
      }
      var dots = $('sv-dots') ? $('sv-dots').children : [];
      for (var j = 0; j < dots.length; j++) {
        if (known[dots[j].getAttribute('data-date')]) { dots[j].classList.add('on'); dots[j].querySelector('.dot').textContent = '✓'; }
      }
    }

    /* ---- stories */
    var sv = $('sv'), segs = $('sv-segs'), cur = { btn: null, slides: [], i: 0 }, timer = null, y0 = null;
    var storyBtns = Array.prototype.slice.call(document.querySelectorAll('.story'));
    storyBtns.forEach(function (b) {
      if (LS.get('daybook.seen.' + b.getAttribute('data-story')) === b.getAttribute('data-hash')) b.classList.add('seen');
      b.addEventListener('click', function () { openStory(b); });
    });
    function openStory(btn) {
      var key = btn.getAttribute('data-story');
      var groups = sv.querySelectorAll('.sv-group');
      for (var i = 0; i < groups.length; i++) groups[i].hidden = groups[i].getAttribute('data-story') !== key;
      cur = { btn: btn, slides: sv.querySelectorAll('.sv-group[data-story="' + key + '"] .slide'), i: 0 };
      segs.innerHTML = '';
      for (var k = 0; k < cur.slides.length; k++) segs.appendChild(document.createElement('span')).className = 'sv-seg';
      for (var s = 0; s < segs.children.length; s++) segs.children[s].appendChild(document.createElement('i'));
      sv.hidden = false;
      LS.set('daybook.seen.' + key, btn.getAttribute('data-hash'));
      btn.classList.add('seen');
      slide(0);
    }
    function slide(i) {
      clearTimeout(timer);
      if (i < 0) i = 0;
      if (i >= cur.slides.length) {
        var next = storyBtns[storyBtns.indexOf(cur.btn) + 1];
        return next ? openStory(next) : closeStory();
      }
      cur.i = i;
      for (var k = 0; k < cur.slides.length; k++) {
        cur.slides[k].hidden = k !== i;
        var seg = segs.children[k];
        seg.className = 'sv-seg' + (k < i ? ' done' : '');
        if (k === i) { void seg.offsetWidth; seg.className = 'sv-seg ' + (reduced ? 'done' : 'run'); }
      }
      if (!reduced) timer = setTimeout(function () { slide(cur.i + 1); }, 5000);
    }
    function closeStory() { clearTimeout(timer); sv.hidden = true; }
    $('sv-x').addEventListener('click', closeStory);
    $('sv-prev').addEventListener('click', function () { slide(cur.i - 1); });
    $('sv-next').addEventListener('click', function () { slide(cur.i + 1); });
    sv.addEventListener('touchstart', function (e) { y0 = e.touches[0].clientY; }, { passive: true });
    sv.addEventListener('touchend', function (e) {
      if (y0 !== null && e.changedTouches[0].clientY - y0 > 80) closeStory();
      y0 = null;
    });
    document.addEventListener('keydown', function (e) {
      if (sv.hidden) return;
      if (e.key === 'Escape') closeStory();
      else if (e.key === 'ArrowRight') slide(cur.i + 1);
      else if (e.key === 'ArrowLeft') slide(cur.i - 1);
    });

    /* ---- 영상: shorts (reels) and long videos; the iframe is created only on tap (0 third-party requests on load) */
    var vtab = $('tab-videos'), feed = $('feed'), longs = $('longs');
    function stopVideos() {
      if (!vtab) return;   /* showTab() runs before this section is wired */
      var frames = vtab.querySelectorAll('iframe');
      for (var i = 0; i < frames.length; i++) {
        var media = frames[i].parentNode;
        media.removeChild(frames[i]);
        for (var c = 0; c < media.children.length; c++) media.children[c].hidden = false;
      }
    }
    vtab.addEventListener('click', function (e) {
      var b = e.target.closest('button.play');
      if (!b) return;
      var id = b.closest('[data-yt]').getAttribute('data-yt');
      if (!/^[A-Za-z0-9_-]{11}$/.test(id)) return;
      stopVideos();
      var media = b.parentNode, f = document.createElement('iframe');
      for (var c = 0; c < media.children.length; c++) media.children[c].hidden = true;
      f.src = 'https://www.youtube-nocookie.com/embed/' + id + '?autoplay=1&playsinline=1&rel=0';
      f.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
      f.allowFullscreen = true;
      f.referrerPolicy = 'strict-origin';   /* only the origin, never the token path */
      f.title = b.getAttribute('aria-label') || 'video';
      media.appendChild(f);
    });
    var segs2 = vtab.querySelectorAll('.seg-b'), tg = $('grid-toggle');
    for (var sb = 0; sb < segs2.length; sb++) {
      segs2[sb].addEventListener('click', function () {
        var v = this.getAttribute('data-v');
        stopVideos();
        for (var k = 0; k < segs2.length; k++) segs2[k].setAttribute('aria-selected', segs2[k] === this ? 'true' : 'false');
        if (feed) feed.hidden = v !== 'shorts';
        if (longs) longs.hidden = v !== 'long';
        if (tg) tg.hidden = v !== 'shorts';
      });
    }
    if (tg && feed) {
      tg.addEventListener('click', function () {
        var grid = feed.classList.toggle('grid');
        tg.setAttribute('aria-pressed', grid ? 'true' : 'false');
        tg.textContent = tg.getAttribute(grid ? 'data-feed' : 'data-grid');
        feed.scrollTop = 0;
      });
    }

    /* ---- 내 기록: month highlights jump to that month in the grid */
    var hls = document.querySelectorAll('.hl-b');
    for (var h = 0; h < hls.length; h++) {
      hls[h].addEventListener('click', function () {
        var target = $('m-' + this.getAttribute('data-month'));
        if (target) target.scrollIntoView({ block: 'start', behavior: reduced ? 'auto' : 'smooth' });
      });
    }

    /* ---- 오늘: the ritual. Class day: ① 출석 (4-digit code) → ② 과제 끝 (photo). Other days: 오늘 공부 인증 (photo). */
    var classToday = TASTE || (D.classDays || []).indexOf(today) >= 0 || isClassDay(today);
    var CKEY = 'daybook.ck.' + tok + '.';
    var ckTime = LS.get(CKEY + today) || (today === D.pub ? D.checkinTime || '' : '');
    function hhmm(iso) { return new Date(Date.parse(iso) + 9 * 3600e3).toISOString().slice(11, 16); }
    function renderRitual() {
      $('r-title').textContent = classToday ? S.ritual_class : S.ritual_study;
      $('steps').hidden = !classToday;
      $('cta').textContent = classToday ? S.end_cta : S.study_cta;
      $('cta-vh').textContent = $('cta').textContent;
      var ck = $('sp-ck'), en = $('sp-end');
      ck.classList.toggle('ok', !!ckTime);
      $('sp-ck-v').textContent = ckTime;
      en.classList.toggle('ok', !!known[today]);
      $('sp-end-v').textContent = known[today] ? (LS.get(TKEY + today) || (today === D.pub ? D.todayTime : '') || '✓') : '';
      ck.classList.toggle('now', classToday && !ckTime);
      en.classList.toggle('now', !!(classToday && ckTime && !known[today]));
    }
    function idleState() { return classToday && !ckTime ? 'checkin' : 'idle'; }

    /* the 4-digit pad */
    var pad = $('pad'), digits = '', padBusy = false;
    function padRender(msg, bad) {
      var sp = $('pad-d').children;
      for (var i = 0; i < 4; i++) { sp[i].textContent = digits[i] || ''; sp[i].classList.toggle('on', i === digits.length); }
      if (msg !== undefined) { $('pad-err').textContent = msg; $('pad-err').classList.toggle('bad', !!bad); }
    }
    function padOpen() { digits = ''; padRender(); pad.hidden = false; }
    function padClose() { pad.hidden = true; }
    function checkedIn(iso) {
      ckTime = hhmm(iso);
      LS.set(CKEY + today, ckTime);
      padClose();
      renderRitual();
      if (!known[today]) show('idle');
    }
    function padSubmit() {
      padBusy = true;
      if (TASTE) {   /* simulated: any 4 digits, nothing is sent */
        setTimeout(function () { padBusy = false; checkedIn(new Date().toISOString()); }, 300);
        return;
      }
      var fd = new FormData();
      fd.append('t', tok);
      fd.append('code', digits);
      fetch(D.worker + '/checkin', { method: 'POST', body: fd })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j.status = r.status; return j; }); })
        .then(function (j) {
          padBusy = false;
          if (j.ok) return checkedIn(j.ts);
          digits = '';
          var msg = j.error === 'bad_code' ? fmt(S.ck_bad, { n: j.left }) : j.error === 'closed' ? S.ck_closed
            : j.error === 'no_class_today' ? S.ck_none : j.error === 'rate_limited' ? S.ck_rate : S.ck_error;
          padRender(msg, true);
        })
        .catch(function () { padBusy = false; digits = ''; padRender(S.ck_error, true); });
    }
    $('ck-open').addEventListener('click', padOpen);
    $('keys').addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b || padBusy) return;
      var k = b.getAttribute('data-k');
      if (k === 'x') return padClose();
      if (k === 'b') digits = digits.slice(0, -1);
      else if (digits.length < 4) digits += k;
      padRender('', false);
      if (digits.length === 4) padSubmit();
    });

    /* ---- 오늘: pick -> preview -> 올리기 -> done */
    var states = ['checkin', 'idle', 'preview', 'done', 'err'];
    function show(state) { states.forEach(function (s) { $('st-' + s).hidden = s !== state; }); }
    var TKEY = 'daybook.time.' + tok + '.';
    function showDone(replaced) {
      var t = LS.get(TKEY + today) || (today === D.pub ? D.todayTime : '');
      var md = Number(today.slice(5, 7)) + '/' + Number(today.slice(8, 10));
      $('d-shot').textContent = t ? fmt(S.recorded_at, { date: md, t: t }) : fmt(S.recorded, { date: md });
      $('d-replaced').hidden = !replaced;
      $('d-streak').textContent = fmt(S.streak_label, { n: streakNow() });
      $('d-class').textContent = fmt(S.class_plus, { n: D.classTotal || 0 });
      show('done');
    }
    function showErr(msg) { $('err-msg').textContent = msg; show('err'); }
    function busy(on) {
      $('pv-busy').hidden = !on;
      $('post').disabled = on;
      $('pv-retake').style.pointerEvents = on ? 'none' : '';
    }

    if (known[today]) showDone(false);
    else show(idleState());
    renderLive();
    renderRitual();
    if (D.worker && tok) {
      fetch(D.worker + '/status?t=' + tok, { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (!j || !j.days) return;
          remember(j.days);
          if (j.class_today) classToday = true;
          if (j.checkin_ts) { ckTime = hhmm(j.checkin_ts); LS.set(CKEY + today, ckTime); }
          renderLive();
          renderRitual();
          if (!known[today] && $('st-preview').hidden && $('st-err').hidden) show(idleState());
          if (known[today] && $('st-preview').hidden) showDone(false);
        })
        .catch(function () { /* offline: the published data is enough */ });
    }

    var picked = null, pickedUrl = null;
    $('photo').addEventListener('change', function (e) {
      var f = e.target.files && e.target.files[0];
      e.target.value = '';
      if (!f) return;
      if (!D.worker && !TASTE) { showErr(S.no_worker); return; }
      if (pickedUrl) URL.revokeObjectURL(pickedUrl);
      picked = f;
      pickedUrl = URL.createObjectURL(f);
      $('preview').src = pickedUrl;
      busy(false);
      show('preview');
    });
    function onResult(j) {
      busy(false);
      if (j.ok) {
        if (j.day) today = j.day;
        known[today] = 1;
        if (j.ts) {
          var kt = new Date(Date.parse(j.ts) + 9 * 3600e3).toISOString().slice(11, 16);
          LS.set(TKEY + today, kt);
        }
        remember((j.days || []).concat([today]));
        renderLive();
        renderRitual();
        picked = null;
        showDone(!!j.already);
      } else {
        showErr(j.status === 429 ? S.upload_rate : S.upload_error);
      }
    }
    $('post').addEventListener('click', function () {
      if (!picked) return;
      busy(true);
      if (TASTE) {   /* simulate the Worker: the photo never leaves the browser */
        var first = LS.get(TKEY + today) ? null : new Date().toISOString();
        setTimeout(function () { onResult({ ok: true, day: today, ts: first, already: !!known[today], days: [today] }); }, 500);
        return;
      }
      shrink(picked)
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
        .then(onResult)
        .catch(function () {
          busy(false);
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
        var ck = row.querySelector('.ck');
        if (ck && s.ck) ck.textContent = (D.S_in || '출석') + ' ' + s.ck;
        var bs = row.querySelectorAll('.pt');
        for (var j = 0; j < bs.length; j++) bs[j].classList.toggle('sel', Number(bs[j].getAttribute('data-p')) === s.points);
      }
      $('b-fill').style.width = st.pct.toFixed(1) + '%';
      $('b-togo').textContent = st.togo;
      $('b-three').textContent = st.three;
      if (st.arrivals) {
        var a = $('b-arrivals');
        if (st.arrivals.length) {
          a.innerHTML = '';
          st.arrivals.forEach(function (x) {
            var sp = document.createElement('span'), b = document.createElement('b');
            sp.className = 'arr'; b.textContent = x.initial;
            sp.appendChild(b); sp.appendChild(document.createTextNode(' ' + x.time));
            a.appendChild(sp);
          });
        }
      }
    }
    $('roster').addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      var id = Number(b.closest('.row').getAttribute('data-id'));
      if (b.classList.contains('pt')) post('/api/attitude', { student_id: id, points: Number(b.getAttribute('data-p')) }).then(apply);
      else if (b.classList.contains('subfor')) post('/api/submit-for', { student_id: id }).then(apply);
      else if (b.classList.contains('ckfor')) post('/api/checkin-for', { student_id: id }).then(apply);
    });
    setInterval(function () {
      fetch('/api/state', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(apply).catch(function () {});
    }, (D.refresh || 10) * 1000);
  }
})();
