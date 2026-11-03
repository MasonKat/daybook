/* F6: the Teacher tab (local board app only; never published). Plain JS, same-origin JSON calls. */
(function () {
  'use strict';
  var D = JSON.parse(document.getElementById('data').textContent), S = D.S;
  function $(id) { return document.getElementById(id); }
  function api(path, body) {
    var o = body === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    return fetch(path, o).then(function (r) { return r.json(); });
  }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function copy(text, btn) {
    var done = function () { var t = btn.textContent; btn.textContent = S.copied; setTimeout(function () { btn.textContent = t; }, 1200); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { fallback(); });
    else fallback();
    function fallback() { var ta = el('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); done(); } catch (e) { /* ignore */ } document.body.removeChild(ta); }
  }

  /* roster */
  function loadRoster() {
    api('/api/teacher/roster').then(function (j) {
      var tb = $('roster-rows'); tb.innerHTML = '';
      j.students.forEach(function (s) {
        var tr = el('tr', { 'data-slug': s.slug });
        var name = el('input', { type: 'text', value: s.display_name, maxlength: '20', 'aria-label': s.slug });
        var save = el('button', { type: 'button', 'class': 'btn ghost sm' }, S.save);
        save.onclick = function () { api('/api/teacher/roster', { slug: s.slug, display_name: name.value }).then(msg); };
        var act = el('input', { type: 'checkbox' }); act.checked = s.active;
        act.onchange = function () { api('/api/teacher/roster', { slug: s.slug, active: act.checked }).then(msg); };
        var cs = el('button', { type: 'button', 'class': 'btn ghost sm' }, S.student);
        cs.onclick = function () { copy(s.student_link, cs); };
        var cp = el('button', { type: 'button', 'class': 'btn ghost sm' }, S.parent);
        cp.onclick = function () { copy(s.parent_link, cp); };
        var td1 = el('td'); td1.appendChild(name); td1.appendChild(save);
        var td2 = el('td'); td2.appendChild(act);
        var td3 = el('td'); td3.appendChild(cs); td3.appendChild(cp);
        tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3); tb.appendChild(tr);
      });
    });
  }
  function msg(j) { $('roster-msg').textContent = j.ok ? (S.saved + (j.message ? ' · ' + j.message : '')) : (j.error || ''); }

  /* notes: the existing note, else the data-based suggestion, prefilled */
  function loadNotes() {
    api('/api/teacher/notes').then(function (j) {
      var box = $('notes-rows'); box.innerHTML = '';
      $('notes-week').textContent = j.week;
      j.students.forEach(function (s) {
        var row = el('div', { 'class': 'nrow', 'data-slug': s.slug });
        var lab = el('label', {}, s.display_name);
        var ta = el('input', { type: 'text', maxlength: '120' }); ta.value = s.note || s.suggestion;
        if (!s.note) ta.classList.add('draft');
        var b = el('button', { type: 'button', 'class': 'btn ghost sm' }, S.save);
        var st = el('span', { 'class': 'muted' }, s.note ? '✓' : '');
        b.onclick = function () {
          api('/api/teacher/note', { slug: s.slug, week: j.week, text: ta.value }).then(function (r) {
            st.textContent = r.ok ? '✓ ' + S.saved : (r.error || ''); if (r.ok) ta.classList.remove('draft');
          });
        };
        row.appendChild(lab); row.appendChild(ta); row.appendChild(b); row.appendChild(st); box.appendChild(row);
      });
    });
  }

  /* imports: files are read in the browser and handed to the same importers as the CLI */
  function readFiles(input) {
    return Promise.all(Array.prototype.map.call(input.files, function (f) {
      return new Promise(function (res) { var r = new FileReader(); r.onload = function () { res({ name: f.name, data: r.result }); }; r.readAsDataURL(f); });
    }));
  }
  Array.prototype.forEach.call(document.querySelectorAll('[data-import]'), function (b) {
    b.onclick = function () {
      var kind = b.getAttribute('data-import');
      readFiles($(kind === 'screeners' ? 'imp-screeners' : 'imp-tests')).then(function (files) {
        return api('/api/teacher/import', { kind: kind, files: files });
      }).then(function (j) {
        $('import-out').textContent = j.ok
          ? kind + ': ' + j.imported + '/' + j.rows + ' imported' + (j.unmatched.length ? '\nunmatched:\n  ' + j.unmatched.join('\n  ') : '') + (j.errors.length ? '\nerrors:\n  ' + j.errors.join('\n  ') : '')
          : (j.error || 'error');
        loadNotes();
      });
    };
  });

  /* moments + 휴강 */
  function loadMoments() {
    api('/api/teacher/moments').then(function (j) {
      var ul = $('moment-list'); ul.innerHTML = '';
      j.moments.forEach(function (m) { ul.appendChild(el('li', {}, m.date + ' · ' + (m.text || '–'))); });
      var cl = $('cancel-list'); cl.innerHTML = '';
      j.cancelled.forEach(function (c) { cl.appendChild(el('li', {}, c.date + ' · ' + c.reason)); });
    });
  }
  $('tm-post').onclick = function () {
    var f = $('tm-file').files && $('tm-file').files[0];
    if (!$('tm-gate').checked) { $('tm-msg').textContent = S.need; return; }
    if (!f) return;
    var r = new FileReader();
    r.onload = function () {
      api('/api/moment', { image: r.result, text: $('tm-text').value, confirm: true }).then(function (j) {
        $('tm-msg').textContent = j.message || j.error || '';
        if (j.ok !== false) { $('tm-file').value = ''; $('tm-text').value = ''; $('tm-gate').checked = false; loadMoments(); }
      });
    };
    r.readAsDataURL(f);
  };
  function cancelCall(undo) {
    api('/api/teacher/cancel', { date: $('cx-date').value, reason: $('cx-reason').value, undo: undo }).then(function (j) {
      if (!j.ok) { alert(j.error); return; }
      loadMoments();
    });
  }
  $('cx-do').onclick = function () { cancelCall(false); };
  $('cx-undo').onclick = function () { cancelCall(true); };

  /* publish / deploy */
  $('pub-do').onclick = function () {
    $('pub-out').textContent = S.running;
    api('/api/teacher/publish', {}).then(function (j) {
      $('pub-out').textContent = j.steps.join('\n') + (j.findings.length ? '\n\n' + j.findings.join('\n') : '');
      $('pub-out').className = 'tout ' + (j.ok ? 'ok' : 'bad');
      loadWeekly();
    });
  };
  $('dep-do').onclick = function () {
    if (!window.confirm(S.confirm_deploy)) return;
    $('pub-out').textContent = S.running;
    api('/api/teacher/deploy', {}).then(function (j) {
      $('pub-out').textContent = (j.command ? '$ ' + j.command + '\n' : '') + (j.output || j.error || '');
      $('pub-out').className = 'tout ' + (j.ok ? 'ok' : 'bad');
    });
  };

  /* weekly paste messages */
  function loadWeekly() {
    api('/api/teacher/weekly').then(function (j) {
      var box = $('weekly-rows'); box.innerHTML = '';
      j.messages.forEach(function (m) {
        var w = el('div', { 'class': 'wrow' });
        var pre = el('pre', { 'class': 'tout' }, m);
        var b = el('button', { type: 'button', 'class': 'btn ghost sm' }, S.copy);
        b.onclick = function () { copy(m, b); };
        w.appendChild(pre); w.appendChild(b); box.appendChild(w);
      });
    });
  }

  loadRoster(); loadNotes(); loadMoments(); loadWeekly();
})();
