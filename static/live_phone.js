/* live-v2: AMP Live questions on the phone (Mason, 11-05). The room sends the current stem + choices only to joined phones of
   its class while it is open; math renders with the self-hosted KaTeX (/static/katex, loaded only when a question has $…$).
   Text is never put in as HTML: plain parts are text nodes, math parts are KaTeX's own output (trust off). */
(function () {
  'use strict';
  var KV = '0.16.11', waiting = null, lastQ = -1, imgQ = -1, MARKS = ['①', '②', '③', '④', '⑤'];
  function $(id) { return document.getElementById(id); }
  function loadKatex(cb) {
    if (window.katex) return cb();
    if (waiting) { waiting.push(cb); return; }
    waiting = [cb];
    var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = '/static/katex/katex.min.css?v=' + KV; document.head.appendChild(l);
    var s = document.createElement('script'); s.src = '/static/katex/katex.min.js?v=' + KV;
    s.onload = s.onerror = function () { var q = waiting; waiting = null; q.forEach(function (f) { f(); }); };
    document.head.appendChild(s);
  }
  function math(el, text) {
    el.textContent = '';
    String(text || '').split(/(\$[^$]+\$)/).forEach(function (p) {
      if (!p) return;
      if (p.length > 2 && p.charAt(0) === '$' && p.charAt(p.length - 1) === '$' && window.katex) {
        var sp = document.createElement('span');
        try { sp.innerHTML = window.katex.renderToString(p.slice(1, -1), { throwOnError: false, trust: false, maxSize: 10, maxExpand: 200 }); }
        catch (e) { sp.textContent = p; }
        el.appendChild(sp);
      } else el.appendChild(document.createTextNode(p));
    });
  }
  function plain(ch) {
    ch.classList.remove('txt');
    var bs = ch.querySelectorAll('button');
    for (var i = 0; i < bs.length; i++) bs[i].textContent = MARKS[i];
  }
  function render(st, ctx) {
    var box = $('lv-qbox'), stem = $('lv-stem'), im = $('lv-img'), ch = $('lv-choices');
    if (!box) return;
    if (!st || !st.open || !st.q || st.stem == null) { box.hidden = true; plain(ch); lastQ = -1; return; }
    box.hidden = false;
    if (st.q === lastQ) return;
    lastQ = st.q;
    var draw = function () {
      math(stem, st.stem);
      if (!st.choices) return plain(ch);
      ch.classList.add('txt');
      var bs = ch.querySelectorAll('button');
      for (var i = 0; i < bs.length; i++) {
        bs[i].textContent = '';
        var m = document.createElement('span'); m.className = 'lm'; m.textContent = MARKS[i]; bs[i].appendChild(m);
        var t = document.createElement('span'); t.className = 'lt'; math(t, st.choices[i]); bs[i].appendChild(t);
      }
    };
    draw();
    if (/\$/.test(st.stem + (st.choices || []).join('')) && !window.katex) loadKatex(draw);
    im.hidden = true; im.removeAttribute('src');
    if (st.img && imgQ !== st.q && ctx && ctx.worker) {          // the picture: once per question
      imgQ = st.q;
      fetch(ctx.worker + '/live/img?t=' + ctx.tok + '&room=' + ctx.room, { cache: 'no-store' }).then(function (r) { return r.json(); })
        .then(function (j) { if (j.img && j.q === lastQ) { im.src = 'data:image/png;base64,' + j.img; im.hidden = false; } }).catch(function () {});
    }
  }
  window.DaybookLivePhone = { render: render, math: math, load: loadKatex };
})();
