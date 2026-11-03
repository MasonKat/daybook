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
      if (!D.mask || (D.cancelled || []).indexOf(day) >= 0) return false;
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
      $('bio').textContent = fmt(S.bio, { cls: D.className || '', n: nSubNow() });   /* H6: one value for both */
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
        if (cl.contains('hl') || (cl.contains('cancel') && !known[day])) continue;   /* the highlight / 휴강 stay */
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
      if (key === 'moments' && momentToday()) { LS.set('daybook.mseen.' + tok + '.' + today, '1'); renderRitual(); }
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
    var CKEY = 'daybook.ck.' + tok + '.', LKEY2 = 'daybook.line.' + tok + '.';
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
      var mm = $('sp-m'), tm = momentToday(), mv = momentMark();
      mm.classList.toggle('off', !tm);
      mm.classList.toggle('ok', !!(tm && mv));
      $('sp-m-v').textContent = tm ? mv : '–';
    }
    /* ② 오늘의 장면: today's moment (any baked one in the taste build), seen or reacted */
    function momentSlides() { return Array.prototype.slice.call(document.querySelectorAll('.sv-group[data-story="moments"] .slide')); }
    function momentToday() {
      var sl = momentSlides().filter(function (x) { return TASTE || x.getAttribute('data-k').indexOf(':' + today + ':') > 0; });
      return sl.length ? sl[sl.length - 1].getAttribute('data-k') : '';
    }
    function momentMark() {
      var k = momentToday();
      if (!k) return '';
      var r = LS.get('daybook.react.' + tok + '.' + k);
      return r ? S['react_' + r] : (LS.get('daybook.mseen.' + tok + '.' + today) ? '✓' : '');
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
      if (padMode === 'live') return liveJoin(digits);
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
    var padMode = 'checkin';
    $('ck-open').addEventListener('click', function () {
      padMode = 'checkin';
      $('pad-t').textContent = S.pad_title;
      padOpen();
      if (TASTE) padRender(S.pad_taste, false);
    });
    $('live-open').addEventListener('click', function () {
      padMode = 'live';
      $('pad-t').textContent = S.live_pad;
      padOpen();
      padRender(TASTE ? S.live_taste : '', false);
    });

    /* ---- AMP Live (F7): only answer buttons on the phone; the question is on the projector */
    var LV = { room: null, timer: null, st: null, buf: '', picked: {}, stop: true, sim: null };
    function liveShow(on) {
      $('live').hidden = !on;
      if (!on) { LV.stop = true; clearTimeout(LV.timer); clearInterval(LV.timer); LV.timer = null; }
    }
    function liveJoin(code) {
      if (TASTE) { padBusy = false; padClose(); return tasteLive(); }
      var fd = new FormData();
      fd.append('t', tok); fd.append('room', code);
      fetch(D.worker + '/live/join', { method: 'POST', body: fd })
        .then(function (r) { return r.json(); })
        .then(function (st) {
          padBusy = false;
          if (!st.open) { digits = ''; padRender(S.live_noroom, true); return; }
          LV.room = code; LV.stop = false; LV.picked = {};
          padClose(); liveShow(true); liveRender(st);
          LV.timer = setInterval(livePoll, 2000);
        })
        .catch(function () { padBusy = false; digits = ''; padRender(S.ck_error, true); });
    }
    function livePoll() {
      if (LV.stop) return;
      fetch(D.worker + '/live/state?t=' + tok + '&room=' + LV.room, { cache: 'no-store' })
        .then(function (r) { return r.json(); }).then(liveRender).catch(function () { /* next tick */ });
    }
    function liveAnswer(a) {
      var st = LV.st;
      if (!st || !st.accepting || (st.mine && st.mine.answered)) return;
      LV.picked[st.q] = String(a);
      if (TASTE) return LV.sim.answer(String(a));
      var fd = new FormData();
      fd.append('t', tok); fd.append('room', LV.room); fd.append('q', st.q); fd.append('a', String(a));
      fetch(D.worker + '/live/answer', { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(liveRender).catch(function () {});
    }
    function liveRender(st) {
      if (!st) return;
      if (!st.open) {
        $('lv-status').textContent = S.live_closed;
        LV.stop = true; clearInterval(LV.timer); clearTimeout(LV.timer);
        setTimeout(function () { liveShow(false); }, 2500);
        return;
      }
      LV.st = st;
      var mine = st.mine || {}, size = Math.max(1, st.size || 1);
      $('lv-qn').textContent = st.q ? st.q + ' / ' + st.n : '';
      $('lv-status').textContent = !st.q ? S.live_wait
        : st.revealed ? (mine.correct ? S.live_right : (mine.answered ? S.live_wrong : ''))
        : (mine.answered ? S.live_sent : '');
      $('lv-mine').textContent = st.revealed && mine.correct ? '✓' : '';
      var isNum = st.kind === 'num';
      $('lv-choices').hidden = !st.q || isNum;
      $('lv-numbox').hidden = !st.q || !isNum;
      var lock = !st.accepting || mine.answered;
      var bs = $('lv-choices').children;
      for (var i = 0; i < bs.length; i++) {
        bs[i].disabled = lock;
        bs[i].classList.toggle('mine', LV.picked[st.q] === bs[i].getAttribute('data-a'));
      }
      var ks = $('lv-keys').children;
      for (var k = 0; k < ks.length; k++) ks[k].disabled = lock;
      $('lv-send').disabled = lock;
      if (lock && isNum && LV.picked[st.q]) $('lv-num').textContent = LV.picked[st.q];
      if (!lock && LV.lastQ !== st.q) { LV.buf = ''; $('lv-num').textContent = ''; }
      LV.lastQ = st.q;
      var val = st.revealed && st.correct != null ? st.correct : st.answered;
      $('lv-fill').style.width = Math.min(100, 100 * (val || 0) / size).toFixed(1) + '%';
      $('lv-class').textContent = st.revealed && st.correct != null ? fmt(S.live_class_c, { c: st.correct, s: st.size })
        : fmt(S.live_class, { a: st.answered || 0, s: st.size });
    }
    $('lv-x').addEventListener('click', function () { liveShow(false); });
    $('lv-choices').addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) liveAnswer(b.getAttribute('data-a')); });
    $('lv-keys').addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      var k = b.getAttribute('data-k');
      if (k === 'b') LV.buf = LV.buf.slice(0, -1);
      else if (k === '-') LV.buf = LV.buf ? LV.buf : '-';
      else if (LV.buf.replace('-', '').length < 6) LV.buf += k;
      $('lv-num').textContent = LV.buf;
    });
    $('lv-send').addEventListener('click', function () { if (/^-?\d+$/.test(LV.buf)) liveAnswer(LV.buf); });

    /* the taste build: an 8-student practice room that plays itself; nothing is sent */
    function tasteLive() {
      var KINDS = ['choice', 'num', 'choice', 'num', 'choice'], KEYS = ['3', '6', '3', '7', '4'], size = 8;
      var sim = { q: 0, n: 5, answered: 0, correct: 0, accepting: false, revealed: false, mine: null };
      function view() {
        return { open: true, q: sim.q, n: sim.n, kind: KINDS[sim.q - 1], accepting: sim.accepting, revealed: sim.revealed, size: size,
          joined: size, answered: sim.answered + (sim.mine ? 1 : 0), correct: sim.revealed ? sim.correct + (sim.mine && sim.mine.correct ? 1 : 0) : null,
          mine: { joined: true, answered: !!sim.mine, correct: sim.revealed && sim.mine ? sim.mine.correct : null } };
      }
      function reveal() { if (LV.stop || sim.revealed) return; sim.accepting = false; sim.revealed = true; liveRender(view()); LV.timer = setTimeout(next, 3000); }
      function bot() {
        if (LV.stop || !sim.accepting) return;
        if (sim.answered < size - 1) { sim.answered++; if (Math.random() < 0.75) sim.correct++; liveRender(view()); }
        if (sim.answered >= size - 1 && sim.mine) { LV.timer = setTimeout(reveal, 600); return; }
        LV.timer = setTimeout(bot, 400 + Math.random() * 600);
      }
      function next() {
        if (LV.stop) return;
        if (sim.q >= sim.n) return liveRender({ open: false });
        sim.q++; sim.accepting = true; sim.revealed = false; sim.answered = 0; sim.correct = 0; sim.mine = null;
        liveRender(view());
        LV.timer = setTimeout(bot, 500);
      }
      LV.sim = { answer: function (a) {
        if (!sim.accepting || sim.mine) return;
        sim.mine = { a: a, correct: a === KEYS[sim.q - 1] };
        liveRender(view());
        if (sim.answered >= size - 1) { clearTimeout(LV.timer); LV.timer = setTimeout(reveal, 600); }
      } };
      LV.stop = false; LV.picked = {};
      liveShow(true);
      liveRender({ open: true, q: 0, n: 5, size: size, joined: size, answered: 0, mine: { joined: true } });
      LV.timer = setTimeout(next, 1200);
    }
    $('sp-m').addEventListener('click', function () {
      var b = document.querySelector('.story[data-story="moments"]');
      if (b && !b.hidden && momentSlides().length) b.click();
    });

    /* ---- reactions: anonymous class counters, one per student per moment (the first one counts) */
    function setCounts(k, counts, mine) {
      var box = document.querySelector('.reacts[data-k="' + k + '"]');
      if (!box) return;
      var bs = box.querySelectorAll('.rx');
      for (var i = 0; i < bs.length; i++) {
        var r = bs[i].getAttribute('data-r');
        if (counts) bs[i].querySelector('.rc').textContent = counts[r] ? counts[r] : '';
        bs[i].setAttribute('aria-pressed', mine === r ? 'true' : 'false');
      }
    }
    document.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('.rx');
      if (!b) return;
      var k = b.closest('.reacts').getAttribute('data-k'), r = b.getAttribute('data-r'), RK = 'daybook.react.' + tok + '.' + k;
      var done = function (mine, counts) { LS.set(RK, mine); setCounts(k, counts, mine); renderRitual(); };
      if (LS.get(RK)) return;   /* already reacted: one per moment */
      if (TASTE) {
        var c = {}; c[r] = 1;
        return done(r, c);
      }
      var fd = new FormData();
      fd.append('t', tok); fd.append('k', k); fd.append('r', r);
      fetch(D.worker + '/react', { method: 'POST', body: fd })
        .then(function (x) { return x.json(); })
        .then(function (j) { if (j && j.ok) done(j.mine, j.counts); })
        .catch(function () { /* offline: try again later */ });
    });
    momentSlides().forEach(function (sl) {
      var k = sl.getAttribute('data-k'), mine = LS.get('daybook.react.' + tok + '.' + k);
      if (mine) { var c = {}; if (TASTE) c[mine] = 1; setCounts(k, TASTE ? c : null, mine); }
    });

    /* ---- live moments: fetched from our own Worker only after the page has loaded */
    window.addEventListener('load', function () {
      if (!D.worker || !tok || TASTE) return;
      fetch(D.worker + '/moments?t=' + tok, { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (!j || !j.items) return;
          var group = document.querySelector('.sv-group[data-story="moments"]'), ring = document.querySelector('.story[data-story="moments"]');
          var added = [];
          j.items.forEach(function (m) {
            if (m.date < (D.momentsFrom || D.weekStart)) return;
            if (!group.querySelector('.slide[data-k="' + m.k + '"]')) {
              group.appendChild(buildMoment(m));
              added.push(m.k);
              loadMomentImage(m.k);
            }
            setCounts(m.k, m.counts, m.mine);
            if (m.mine) LS.set('daybook.react.' + tok + '.' + m.k, m.mine);
          });
          if (added.length) {
            var h = ring.getAttribute('data-hash') + ':' + added.join(',');
            ring.setAttribute('data-hash', h);
            ring.classList.toggle('seen', LS.get('daybook.seen.moments') === h);
          }
          if (momentSlides().length) ring.hidden = false;
          renderRitual();
        })
        .catch(function () { /* offline: the published moments are enough */ });
    });
    function buildMoment(m) {
      var sl = document.createElement('div'), d = m.date;
      sl.className = 'slide s-moment';
      sl.setAttribute('data-k', m.k);
      sl.hidden = true;
      var kick = document.createElement('p');
      kick.className = 'sv-kicker';
      kick.textContent = S.story_moments + ' · ' + Number(d.slice(5, 7)) + '/' + Number(d.slice(8, 10)) + ' (' + WD[weekday(d)] + ')';
      var img = document.createElement('img');
      img.className = 'mimg'; img.alt = '';
      sl.appendChild(kick); sl.appendChild(img);
      if (m.text) { var t = document.createElement('p'); t.className = 'mtext'; t.textContent = m.text; sl.appendChild(t); }
      var box = document.createElement('div');
      box.className = 'reacts'; box.setAttribute('data-k', m.k);
      ['fire', 'clap', 'mind'].forEach(function (r) {
        var b = document.createElement('button'), e1 = document.createElement('span'), c = document.createElement('span');
        b.type = 'button'; b.className = 'rx'; b.setAttribute('data-r', r); b.setAttribute('aria-pressed', 'false');
        e1.textContent = S['react_' + r]; c.className = 'rc tnum';
        b.appendChild(e1); b.appendChild(document.createTextNode(' ')); b.appendChild(c);
        box.appendChild(b);
      });
      sl.appendChild(box);
      return sl;
    }
    function loadMomentImage(k) {
      fetch(D.worker + '/moment?t=' + tok + '&k=' + encodeURIComponent(k))
        .then(function (r) { return r.ok ? r.blob() : null; })
        .then(function (b) {
          if (!b) return;
          var url = URL.createObjectURL(b);
          var img = document.querySelector('.slide[data-k="' + k + '"] img');
          if (img) img.src = url;
          var ring = $('ring-moments');
          if (ring) { ring.innerHTML = ''; var ri = document.createElement('img'); ri.alt = ''; ri.src = url; ring.appendChild(ri); }
        })
        .catch(function () {});
    }
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
    var states = ['checkin', 'idle', 'preview', 'done', 'err', 'queued'];
    function show(state) { states.forEach(function (s) { $('st-' + s).hidden = s !== state; }); }
    var TKEY = 'daybook.time.' + tok + '.';
    function showDone(replaced) {
      var t = LS.get(TKEY + today) || (today === D.pub ? D.todayTime : '');
      var md = Number(today.slice(5, 7)) + '/' + Number(today.slice(8, 10));
      $('d-shot').textContent = t ? fmt(S.recorded_at, { date: md, t: t }) : fmt(S.recorded, { date: md });
      var mk = momentMark();
      $('d-day').textContent = !classToday ? fmt(S.day_study, { out: t || '✓' })
        : mk ? fmt(S.day_class_m, { 'in': ckTime || '–', m: mk, out: t || '✓' }) : fmt(S.day_class, { 'in': ckTime || '–', out: t || '✓' });
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

    try { var saved = JSON.parse(LS.get(LKEY2 + today) || 'null'); if (saved) addMyLine(saved.t, saved.c); } catch (e) { /* ignore */ }
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

    var picked = null, pickedUrl = null, chip = '';
    $('chips').addEventListener('click', function (e) {
      var b = e.target.closest('.cbtn');
      if (!b) return;
      chip = chip === b.getAttribute('data-chip') ? '' : b.getAttribute('data-chip');
      var all = $('chips').children;
      for (var i = 0; i < all.length; i++) all[i].setAttribute('aria-pressed', all[i].getAttribute('data-chip') === chip ? 'true' : 'false');
    });
    function lineNow() { return ($('line').value || '').replace(/[\u0000-\u001f]/g, ' ').trim().slice(0, 40); }
    function addMyLine(text, chipKey) {
      if (!text && !chipKey) return;
      var ul = $('mylines'), li = ul.querySelector('li[data-date="' + today + '"]');
      var empty = ul.querySelector('.empty');
      if (empty) ul.removeChild(empty);
      if (!li) { li = document.createElement('li'); li.setAttribute('data-date', today); ul.insertBefore(li, ul.firstChild); }
      li.innerHTML = '';
      var add = function (cls, t) { if (!t) return; var sp = document.createElement('span'); sp.className = cls; sp.textContent = t; li.appendChild(sp); };
      add('ld', Number(today.slice(5, 7)) + '/' + Number(today.slice(8, 10)) + ' (' + WD[weekday(today)] + ')');
      add('lchip', chipKey ? S['chip_' + chipKey] : '');
      add('lt2', text);
    }
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
    function sendRec(rec) {
      var fd = new FormData();
      fd.append('t', tok);
      if (rec.set) fd.append('set', rec.set);
      if (rec.line) fd.append('line', rec.line);   /* empty = keep what an earlier upload today sent */
      if (rec.chip) fd.append('chip', rec.chip);
      var ext = rec.blob.type === 'image/png' ? 'png' : (/hei[cf]/.test(rec.blob.type) ? 'heic' : 'jpg');
      fd.append('photo', rec.blob, 'photo.' + ext);
      return fetch(D.worker + '/submit', { method: 'POST', body: fd }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) { j.status = r.status; return j; });
      });
    }
    /* ---- F9: the offline queue (one photo per day per phone) */
    function idb() {
      return new Promise(function (res, rej) {
        try {
          var r = indexedDB.open('daybook', 1);
          r.onupgradeneeded = function () { r.result.createObjectStore('queue', { keyPath: 'key' }); };
          r.onsuccess = function () { res(r.result); };
          r.onerror = function () { rej(r.error); };
        } catch (e) { rej(e); }
      });
    }
    function qTx(mode, fn) {
      return idb().then(function (db) {
        return new Promise(function (res, rej) {
          var tx = db.transaction('queue', mode), out = fn(tx.objectStore('queue'));
          tx.oncomplete = function () { res(out && out.result); };
          tx.onerror = function () { rej(tx.error); };
        });
      });
    }
    function qPut(rec) { return qTx('readwrite', function (st) { st.put(rec); }); }
    function qAll() { return qTx('readonly', function (st) { return st.getAll(); }); }
    function qDel(key) { return qTx('readwrite', function (st) { st.delete(key); }); }
    /* claim this student's queued photos with a short lease in ONE read-write transaction (IndexedDB serialises
       those across tabs and page loads): only the claimer sends; the record is deleted only after a confirmed upload,
       so a page that dies mid-upload loses nothing (the lease expires and the next open retries) */
    var LEASE_MS = 60000;
    function qClaim() {
      var now = Date.now(), me = Math.random().toString(36).slice(2);
      return idb().then(function (db) {
        return new Promise(function (res, rej) {
          var tx = db.transaction('queue', 'readwrite'), st = tx.objectStore('queue'), got = [];
          var rq = st.getAll();
          rq.onsuccess = function () {
            (rq.result || []).forEach(function (r) {
              if (r.key.indexOf(tok + ':') !== 0 || (r.lease && now - r.lease < LEASE_MS)) return;
              r.lease = now; r.owner = me; st.put(r); got.push(r);
            });
          };
          tx.oncomplete = function () { res(got); };
          tx.onerror = function () { rej(tx.error); };
        });
      });
    }
    function qRelease(rec) { rec.lease = 0; return qPut(rec); }
    var flushing = false;
    function flushQueue() {
      if (flushing || TASTE || !D.worker || navigator.onLine === false) return;
      flushing = true;
      qClaim().then(function (recs) {
        return recs.reduce(function (p, rec) {
          return p.then(function () {
            return sendRec(rec).then(function (j) {
              if (j.ok) return qDel(rec.key).then(function () { onResult(j); });
              if (!j.status || j.status >= 500 || j.status === 429) return qRelease(rec);   /* try again later */
              return qDel(rec.key);                                                           /* refused for good */
            }, function () { return qRelease(rec); });                                       /* still offline */
          });
        }, Promise.resolve());
      }).catch(function () { /* no IndexedDB: nothing queued */ }).then(function () { flushing = false; });
    }
    window.addEventListener('online', flushQueue);
    window.addEventListener('load', function () {
      flushQueue();
      if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js', { scope: '/s/' }).catch(function () {});
    });
    qAll().then(function (recs) {
      if ((recs || []).some(function (r) { return r.key === tok + ':' + today; }) && !known[today]) show('queued');
    }).catch(function () {});

    /* ---- F9: "홈 화면에 추가" hint, shown once, never inside the installed app */
    var standalone = (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) || window.navigator.standalone;
    if (!standalone && !LS.get('daybook.a2hs')) { $('a2hs').hidden = false; LS.set('daybook.a2hs', '1'); }
    $('a2hs-x').addEventListener('click', function () { $('a2hs').hidden = true; });

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
        if (lineNow() || chip) {
          LS.set(LKEY2 + today, JSON.stringify({ t: lineNow(), c: chip }));
          addMyLine(lineNow(), chip);
        }
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
      var rec = null;
      shrink(picked)
        .then(function (blob) {
          rec = { key: tok + ':' + today, day: today, blob: blob, set: set, line: lineNow(), chip: chip, at: Date.now() };
          return sendRec(rec);
        })
        .then(onResult)
        .catch(function () {
          /* F9: no network: keep it in this phone (IndexedDB) and send it on the next open / when online */
          if (!rec) { busy(false); return showErr(S.upload_error); }
          qPut(rec).then(function () { busy(false); show('queued'); }, function () { busy(false); showErr(S.upload_offline); });
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
        var bl = row.querySelector('.bline');
        if (bl) bl.textContent = s.line || '';
        var ck = row.querySelector('.ck');
        if (ck && s.ck) ck.textContent = (D.S_in || '출석') + ' ' + s.ck;
        var bs = row.querySelectorAll('.pt');
        for (var j = 0; j < bs.length; j++) bs[j].classList.toggle('sel', Number(bs[j].getAttribute('data-p')) === s.points);
      }
      $('b-fill').style.width = st.pct.toFixed(1) + '%';
      $('b-togo').textContent = st.togo;
      $('b-three').textContent = st.three;
      if (st.reactions && $('b-rx')) $('b-rx').textContent = (D.rxLabel || '') + ' · ' + st.reactions;
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
    var mPost = $('m-post');
    if (mPost) {
      mPost.addEventListener('click', function () {
        var f = $('m-file').files && $('m-file').files[0];
        if (!$('m-gate').checked) { $('m-msg').textContent = D.need || '확인란에 체크해야 올라가요'; return; }
        if (!f) return;
        var rd = new FileReader();
        mPost.disabled = true;
        rd.onload = function () {
          post('/api/moment', { image: rd.result, text: $('m-text').value, confirm: true }).then(function (st) {
            mPost.disabled = false;
            $('m-msg').textContent = st.message || st.error || '';
            if (st.ok !== false) { $('m-file').value = ''; $('m-text').value = ''; $('m-gate').checked = false; apply(st); }
          }).catch(function () { mPost.disabled = false; });
        };
        rd.readAsDataURL(f);
      });
    }
    setInterval(function () {
      fetch('/api/state', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(apply).catch(function () {});
    }, (D.refresh || 10) * 1000);
  }
})();
