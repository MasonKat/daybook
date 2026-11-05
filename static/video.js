/* video-library: the in-page player at /v/<key>/ (one implementation). It speaks the YouTube iframe's own postMessage
   protocol (enablejsapi=1, youtube-nocookie); no YouTube script is ever loaded into this page. Chapters seek and follow the
   current time; speed 1-2x; 넓게 (theater) and 전체화면. Watch time goes to /watch ONLY with the token this phone stored when it
   opened its own /s/ page AND the parent's consent flag; no token -> it plays and counts nothing. No token in any URL here. */
(function () {
  'use strict';
  var YT = 'https://www.youtube-nocookie.com', TOKEN_RE = /^[a-z2-7]{26}$/;
  var D = JSON.parse(document.getElementById('data').textContent), S = D.S;
  function $(id) { return document.getElementById(id); }
  function ls(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  var tok = ls('daybook.token'), counts = !!(tok && TOKEN_RE.test(tok) && ls('daybook.watchok') === '1' && D.worker);
  var frame = null, st = { sec: 0, pos: 0, maxPos: 0, dur: 0, playing: false, sentSec: 0, sentPct: 0 }, sent = [];
  if (tok && TOKEN_RE.test(tok)) { var mine = $('vp-mine'); mine.href = '/s/' + tok + '/#videos'; mine.hidden = false; }   /* href only, never shared */
  function cmd(func, args) {
    var m = JSON.stringify({ event: 'command', func: func, args: args || [] });
    sent.push(m);
    if (frame && frame.contentWindow) { try { frame.contentWindow.postMessage(m, YT); } catch (e) { /* ignore */ } }
    return m;
  }
  function start(t) {
    if (frame) return;
    var box = $('vp-frame'); box.textContent = '';
    frame = document.createElement('iframe');
    frame.src = YT + '/embed/' + D.yt + '?autoplay=1&playsinline=1&rel=0&enablejsapi=1&origin=' + encodeURIComponent(location.origin) + (t ? '&start=' + t : '');
    frame.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen'; frame.allowFullscreen = true;
    frame.referrerPolicy = 'strict-origin'; frame.title = D.title;
    frame.addEventListener('load', function () { try { frame.contentWindow.postMessage(JSON.stringify({ event: 'listening', id: 1, channel: 'widget' }), YT); } catch (e) { /* ignore */ } });
    box.appendChild(frame);
  }
  function flush(beacon) {
    if (st.sec < 1) return false;
    var sec = Math.round(st.sec), pct = st.dur > 0 ? Math.min(100, Math.round(100 * st.maxPos / st.dur)) : 0;
    if (sec === st.sentSec && pct === st.sentPct) return false;
    st.sentSec = sec; st.sentPct = pct;
    if (!counts) return false;                                             /* no token or no consent: nothing leaves */
    var fd = new FormData(); fd.append('t', tok); fd.append('v', D.yt); fd.append('sec', String(sec)); fd.append('pct', String(pct));
    if (beacon && navigator.sendBeacon) navigator.sendBeacon(D.worker + '/watch', fd);
    else fetch(D.worker + '/watch', { method: 'POST', body: fd, keepalive: true }).catch(function () {});
    return true;
  }
  function highlight(t) {
    var bs = document.querySelectorAll('.vp-ch'), cur = -1;
    for (var i = 0; i < bs.length; i++) if (Number(bs[i].getAttribute('data-t')) <= t) cur = i;
    for (var j = 0; j < bs.length; j++) { bs[j].classList.toggle('now', j === cur); if (j === cur) bs[j].setAttribute('aria-current', 'true'); else bs[j].removeAttribute('aria-current'); }
  }
  window.addEventListener('message', function (e) {
    if (!frame || e.origin !== YT || e.source !== frame.contentWindow) return;
    var d; try { d = typeof e.data === 'string' ? JSON.parse(e.data) : e.data; } catch (x) { return; }
    if (!d || !d.event) return;
    var info = d.info || {};
    if (d.event === 'onStateChange') info = { playerState: d.info };
    if (typeof info.duration === 'number' && info.duration > 0) st.dur = info.duration;
    if (typeof info.playerState === 'number') {
      st.playing = info.playerState === 1;
      $('vp-pp').textContent = st.playing ? S.vp_pause : S.vp_play;
      if (info.playerState === 2 || info.playerState === 0) flush(false);
    }
    if (typeof info.currentTime === 'number') {
      var dt = info.currentTime - st.pos;
      if (st.playing && dt > 0 && dt < 3) st.sec += dt;
      st.pos = info.currentTime; st.maxPos = Math.max(st.maxPos, info.currentTime);
      highlight(info.currentTime);
    }
  });
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') flush(true); });
  $('vp-start').addEventListener('click', function () { start(0); });
  $('vp-chapters').addEventListener('click', function (e) {
    var b = e.target.closest('.vp-ch'); if (!b) return;
    var t = Number(b.getAttribute('data-t'));
    if (!frame) start(t); else cmd('seekTo', [t, true]);
    highlight(t);
  });
  $('vp-pp').addEventListener('click', function () { if (!frame) return start(0); cmd(st.playing ? 'pauseVideo' : 'playVideo'); });
  var rates = document.querySelectorAll('.vp-rate');
  for (var i = 0; i < rates.length; i++) rates[i].addEventListener('click', function () {
    var r = Number(this.getAttribute('data-r'));
    for (var k = 0; k < rates.length; k++) rates[k].setAttribute('aria-pressed', rates[k] === this ? 'true' : 'false');
    cmd('setPlaybackRate', [r]);
  });
  $('vp-wide').addEventListener('click', function () {
    var on = document.body.classList.toggle('vp-theater');
    this.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  $('vp-full').addEventListener('click', function () {
    var el = $('vp-frame');
    var go = el.requestFullscreen || el.webkitRequestFullscreen;
    if (!go) return;
    Promise.resolve(go.call(el)).then(function () {
      try { if (screen.orientation && screen.orientation.lock) screen.orientation.lock('landscape').catch(function () {}); } catch (e) { /* ignore */ }
    }).catch(function () {});
  });
  window.DaybookVideo = { cmd: cmd, sent: sent, flush: flush, counts: function () { return counts; }, _state: st, highlight: highlight };
})();
