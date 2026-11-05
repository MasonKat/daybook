/* video-library: the 영상 tab's 라이브러리, search by quiz number (every item opens its /v/<key>/ page). */
(function () {
  'use strict';
  var q = document.getElementById('lib-q');
  if (!q) return;
  q.addEventListener('input', function () {
    var v = q.value.replace(/\D/g, ''), hit = 0, rows = document.querySelectorAll('#lib .lib-g li[data-no]');
    for (var i = 0; i < rows.length; i++) {
      var on = !v || rows[i].getAttribute('data-no').replace(/\D/g, '').indexOf(v) >= 0;
      rows[i].hidden = !on; if (on) hit++;
    }
    var gs = document.querySelectorAll('#lib .lib-g');
    for (var g = 0; g < gs.length; g++) if (v && gs[g].querySelector('li[data-no]')) gs[g].open = true;
    document.getElementById('lib-none').hidden = !v || hit > 0;
  });
})();
