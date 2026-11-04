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

  /* roster (F11: every class, search + class filter, add / move / 퇴원 / 링크 재발급 / 안내 문구; full names stay local) */
  var MM = S.m, rosterAll = [], classesAll = [];
  function fillClassSelect(sel, withAll, keep) {
    var v = keep !== undefined ? keep : sel.value;
    sel.innerHTML = '';
    if (withAll) sel.appendChild(el('option', { value: '' }, withAll));
    classesAll.forEach(function (c) { sel.appendChild(el('option', { value: c.name }, c.name)); });
    sel.value = v;
  }
  function showLinks(j, box) {
    box.innerHTML = '';
    var w = el('div', { 'class': 'wrow links' });
    [[S.student, j.student_link], [S.parent, j.parent_link], [MM.intro_p, j.parent], [MM.intro_s, j.student]].forEach(function (p) {
      var b = btn(p[0]); b.onclick = function () { copy(p[1], b); }; w.appendChild(b);
    });
    box.appendChild(el('p', {}, MM.new_links + ' · ' + j.display_name));
    box.appendChild(el('pre', { 'class': 'tout' }, j.student_link + '\n' + j.parent_link));
    box.appendChild(w);
  }
  function renderRoster() {
    var q = ($('rs-q').value || '').trim(), cf = $('rs-class').value, tb = $('roster-rows');
    tb.innerHTML = '';
    rosterAll.filter(function (s) {
      return (!cf || s.class_name === cf) && (!q || (s.display_name + ' ' + s.full_name + ' ' + s.ext_id).indexOf(q) >= 0);
    }).forEach(function (s) {
      var tr = el('tr', { 'data-slug': s.slug, 'class': s.active ? '' : 'off' });
      var name = el('input', { type: 'text', value: s.display_name, maxlength: '20', 'aria-label': s.slug });
      var save = btn(S.save);
      save.onclick = function () { api('/api/teacher/roster', { slug: s.slug, display_name: name.value }).then(msg); };
      var td1 = el('td'); td1.appendChild(name); td1.appendChild(save);
      var td2 = el('td', { 'class': 'full' }, s.full_name + (s.ext_id ? ' · ' + s.ext_id : ''));
      var mv = el('select', { 'aria-label': MM.move }); classesAll.forEach(function (c) { mv.appendChild(el('option', { value: c.name }, c.name)); });
      mv.value = s.class_name;
      mv.onchange = function () { api('/api/teacher/student-move', { slug: s.slug, 'class': mv.value }).then(function (j) { msg(j); loadRoster(); }); };
      var td3 = el('td'); td3.appendChild(mv);
      var td4 = el('td');
      if (s.active) {
        var lv = btn(MM.leave);
        lv.onclick = function () { api('/api/teacher/student-leave', { slug: s.slug }).then(function (j) { msg(j); loadRoster(); }); };
        td4.appendChild(lv);
      } else {
        td4.appendChild(el('span', { 'class': 'muted' }, s.left_on ? fmt(MM.left, { d: s.left_on }) + ' ' : ''));
        var rt = btn(MM.ret);
        rt.onclick = function () { api('/api/teacher/student-leave', { slug: s.slug, undo: true }).then(function (j) { msg(j); loadRoster(); }); };
        td4.appendChild(rt);
      }
      var td5 = el('td');
      var cs = btn(S.student); cs.onclick = function () { copy(s.student_link, cs); };
      var cp = btn(S.parent); cp.onclick = function () { copy(s.parent_link, cp); };
      var ip = btn(MM.intro_p), is = btn(MM.intro_s);
      ip.onclick = function () { api('/api/teacher/intro?slug=' + encodeURIComponent(s.slug)).then(function (j) { copy(j.parent, ip); }); };
      is.onclick = function () { api('/api/teacher/intro?slug=' + encodeURIComponent(s.slug)).then(function (j) { copy(j.student, is); }); };
      var ro = btn(MM.rotate);
      ro.onclick = function () {
        if (!window.confirm(MM.rotate_confirm)) return;
        api('/api/teacher/student-rotate', { slug: s.slug, which: 'both' }).then(function (j) {
          if (!j.ok) { msg(j); return; }
          showLinks(j, $('sa-links')); loadRoster(); msg(j);
        });
      };
      [cs, cp, ip, is, ro].forEach(function (b) { td5.appendChild(b); });
      [td1, td2, td3, td4, td5].forEach(function (td) { tr.appendChild(td); });
      tb.appendChild(tr);
    });
  }
  function loadRoster() {
    api('/api/teacher/roster').then(function (j) {
      rosterAll = j.students; classesAll = j.classes;
      fillClassSelect($('rs-class'), S.all || '모든 반');
      fillClassSelect($('sa-class'), null, $('sa-class').value || j.board_class);
      fillClassSelect($('rc-class'), null, $('rc-class').value || j.board_class);
      var pick = $('cf-pick'), pv = pick.value;
      pick.innerHTML = ''; pick.appendChild(el('option', { value: '' }, pick.getAttribute('data-new') || '새 반'));
      classesAll.forEach(function (c) { pick.appendChild(el('option', { value: c.name }, c.name)); });
      pick.value = pv;
      renderRoster();
    });
  }
  $('rs-q').addEventListener('input', renderRoster);
  $('rs-class').addEventListener('change', renderRoster);
  $('sa-full').addEventListener('input', function () {        /* the display name is prefilled with the given name */
    var f = $('sa-full').value.trim();
    if (!$('sa-display').getAttribute('data-touched')) $('sa-display').value = f.length === 3 ? f.slice(1) : '';
  });
  $('sa-display').addEventListener('input', function () { $('sa-display').setAttribute('data-touched', '1'); });
  $('sa-save').onclick = function () {
    api('/api/teacher/student-add', { full_name: $('sa-full').value, display_name: $('sa-display').value, 'class': $('sa-class').value,
      track: $('sa-track').value, ext_id: $('sa-ext').value, parent_phone: $('sa-phone').value }).then(function (j) {
      $('sa-msg').textContent = j.ok ? (S.saved + (j.message ? ' · ' + j.message : '')) : (j.error || '');
      if (!j.ok) return;
      showLinks(j, $('sa-links'));
      ['sa-full', 'sa-display', 'sa-track', 'sa-ext', 'sa-phone'].forEach(function (k) { $(k).value = ''; });
      $('sa-display').removeAttribute('data-touched');
      loadRoster();
    });
  };
  $('cf-pick').onchange = function () {
    var c = classesAll.filter(function (x) { return x.name === $('cf-pick').value; })[0] || {};
    $('cf-name').value = c.name || ''; $('cf-grade').value = c.grade || ''; $('cf-days').value = c.days || '';
    $('cf-term').value = c.term || ''; $('cf-reward').value = c.reward_label || ''; $('cf-goal').value = c.goal || 0; $('cf-start').value = c.start_time || '';
  };
  $('cf-save').onclick = function () {
    var pick = $('cf-pick').value;
    api('/api/teacher/class', { create: !pick, name: pick || $('cf-name').value, new_name: pick ? $('cf-name').value : null,
      grade: $('cf-grade').value, days: $('cf-days').value, term: $('cf-term').value, reward_label: $('cf-reward').value,
      goal: $('cf-goal').value, start_time: $('cf-start').value }).then(function (j) {
      $('cf-msg').textContent = j.ok ? S.saved + ' · ' + j.name : (j.error || '');
      if (j.ok) { $('cf-pick').value = j.name; loadRoster(); loadRounds && loadRounds(); }
    });
  };
  function rosterCsv(write) {
    var f = $('rc-file').files && $('rc-file').files[0];
    if (!f) return;
    var r = new FileReader();
    r.onload = function () {
      api('/api/teacher/roster-import', { files: [{ name: f.name, data: r.result }], 'class': $('rc-class').value, write: write }).then(function (j) {
        if (!j.ok) { $('rc-counts').textContent = j.error || ''; return; }
        $('rc-counts').textContent = fmt(MM.counts, j.counts) + (j.written ? ' · ' + S.saved : '') + (j.message ? ' · ' + j.message : '');
        var tb = $('rc-rows'); tb.innerHTML = '';
        j.rows.forEach(function (x) {
          var tr = el('tr', { 'class': 'rc ' + x.status });
          tr.appendChild(el('td', {}, String(x.line))); tr.appendChild(el('td', {}, MM.status[x.status]));
          tr.appendChild(el('td', {}, x.name + (x.ext_id ? ' · ' + x.ext_id : ''))); tr.appendChild(el('td', { 'class': 'muted' }, x.why));
          tb.appendChild(tr);
        });
        $('rc-write').disabled = j.written;
        if (j.written) loadRoster();
      });
    };
    r.readAsDataURL(f);
  }
  $('rc-preview').onclick = function () { rosterCsv(false); };
  $('rc-write').onclick = function () { rosterCsv(true); };
  function msg(j) { $('roster-msg').textContent = j.ok ? (S.saved + (j.message ? ' · ' + j.message : '')) : (j.error || ''); }

  /* notes: the existing note, else the data-based suggestion, prefilled */
  function loadNotes() {
    api('/api/teacher/notes').then(function (j) {
      var box = $('notes-rows'); box.innerHTML = '';
      $('notes-week').textContent = j.week;
      j.students.forEach(function (s) {
        var row = el('div', { 'class': 'nrow', 'data-slug': s.slug });
        var lab = el('label', {}, s.display_name);
        var ta = el('input', { type: 'text', maxlength: '400' }); ta.value = s.note || s.suggestion;
        if (!s.note) ta.classList.add('draft');
        var b = el('button', { type: 'button', 'class': 'btn ghost sm' }, S.save);
        var st = el('span', { 'class': 'muted' }, s.note ? '✓' : '');
        var wn = el('div', { 'class': 'cwarn' });                /* F16.3: warnings only; saving is never blocked */
        function showWarn(list) {
          wn.textContent = (list || []).map(function (w) { return '⚠ ' + (S.cw[w.kind] || w.kind) + ': ' + w.match; }).join('  ');
        }
        showWarn(s.warnings);
        var tmr = null;
        ta.addEventListener('input', function () {
          clearTimeout(tmr);
          tmr = setTimeout(function () { api('/api/teacher/comment-lint', { slug: s.slug, text: ta.value }).then(function (r) { showWarn(r.warnings); }); }, 400);
        });
        b.onclick = function () {
          api('/api/teacher/note', { slug: s.slug, week: j.week, text: ta.value }).then(function (r) {
            st.textContent = r.ok ? '✓ ' + S.saved : (r.error || ''); if (r.ok) { ta.classList.remove('draft'); showWarn(r.warnings); }
          });
        };
        row.appendChild(lab); row.appendChild(ta); row.appendChild(b); row.appendChild(st); row.appendChild(wn); box.appendChild(row);
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
      readFiles($('imp-' + kind)).then(function (files) {
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

  /* ------------------------------------------------------------ F13: the parent-contact desk (laptop only) */
  var C = S.c;
  function fmt(t, o) { return t.replace(/\{(\w+)\}/g, function (_, k) { return o[k] === undefined ? '' : o[k]; }); }
  function btn(label, cls) { return el('button', { type: 'button', 'class': 'btn ' + (cls || 'ghost') + ' sm' }, label); }
  function reloadContacts() { loadContacts(); }

  function needCard(f) {
    var box = el('div', { 'class': 'need lv-' + f.level, 'data-slug': f.slug });
    var h = el('h3', {}, f.class_name + ' · ' + f.display_name + ' ');
    h.appendChild(el('span', { 'class': 'badge ' + f.level }, f.level === 'need' ? C.need : C.notice));
    box.appendChild(h);
    var ul = el('ul', { 'class': 'reasons' });
    f.rules.forEach(function (r) { ul.appendChild(el('li', {}, C.rule[r.rule] + ' · ' + r.reason)); });
    box.appendChild(ul);
    box.appendChild(el('p', { 'class': 'muted' }, C.script));
    var ol = el('ol', { 'class': 'script' });
    f.script.forEach(function (line) { ol.appendChild(el('li', {}, line)); });
    box.appendChild(ol);
    var det = el('details'); det.appendChild(el('summary', {}, C.kakao));
    var pre = el('pre', { 'class': 'tout' }, f.kakao); det.appendChild(pre);
    var cp = btn(S.copy); cp.onclick = function () { copy(f.kakao, cp); }; det.appendChild(cp);
    box.appendChild(det);
    var row = el('div', { 'class': 'trow' });
    var sum = el('input', { type: 'text', maxlength: '120', placeholder: C.summary_ph, 'class': 'csum' });
    var fu = el('input', { type: 'date', title: C.follow, 'class': 'cfu' });
    var called = btn(C.called, 'primary'), sent = btn(C.messaged), hold = btn(C.hold);
    var msg = el('span', { 'class': 'muted' });
    function log(kind) {
      api('/api/teacher/contact', { slug: f.slug, kind: kind, tag: 'trigger', summary: sum.value, follow_up: fu.value || null }).then(function (j) {
        if (j.ok) reloadContacts(); else msg.textContent = j.error || '';
      });
    }
    called.onclick = function () { log('call'); };
    sent.onclick = function () { log('message'); };
    hold.onclick = function () {
      api('/api/teacher/snooze', { slug: f.slug, rule: 'all' }).then(function (j) { if (j.ok) reloadContacts(); else msg.textContent = j.error || ''; });
    };
    [sum, fu, called, sent, hold, msg].forEach(function (e) { row.appendChild(e); });
    box.appendChild(row);
    return box;
  }

  function planRow(it) {
    var li = el('li', { 'class': 'pitem' + (it.locked ? ' locked' : '') + (it.done ? ' done' : ''), 'data-slug': it.slug, 'data-type': it.type });
    var cb = el('input', { type: 'checkbox', 'aria-label': it.display_name });
    cb.checked = it.done; cb.disabled = it.done;
    cb.onchange = function () {
      cb.disabled = true;
      var p = it.type === 'line'
        ? api('/api/teacher/note', { slug: it.slug, week: planWeek, text: it.why })
        : api('/api/teacher/contact', { slug: it.slug, kind: it.type, tag: it.tag || 'rotation', summary: '' });
      p.then(function (j) { if (j.ok) reloadContacts(); else { cb.disabled = false; cb.checked = false; } });
    };
    var lab = el('label');
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode(' ' + C.kind[it.type] + ' ' + it.minutes + '분 · ' + it.class_name + ' ' + it.display_name
      + (it.locked ? ' (' + C.locked + ')' : '')));
    li.appendChild(lab);
    if (it.why) li.appendChild(el('span', { 'class': 'muted why' }, ' ' + it.why));
    return li;
  }
  var planWeek = '';

  function statusRow(x) {
    var tr = el('tr', { 'class': 'st ' + x.level, 'data-slug': x.slug });
    tr.appendChild(el('td', { 'class': 'dd tnum' }, fmt(C.days, { n: x.days })));
    tr.appendChild(el('td', {}, x.class_name + ' · ' + x.display_name));
    tr.appendChild(el('td', { 'class': 'muted' }, x.last || C.never));
    function sel(field, opts) {
      var s = el('select', { 'aria-label': field });
      Object.keys(opts).forEach(function (k) { var o = el('option', { value: k }, opts[k]); if (x[field] === k) o.selected = true; s.appendChild(o); });
      s.onchange = function () { var b = { slug: x.slug }; b[field] = s.value; api('/api/teacher/profile', b).then(function (j) { $('status-msg').textContent = j.ok ? S.saved : (j.error || ''); }); };
      return s;
    }
    var td = el('td');
    td.appendChild(sel('channel', C.channel)); td.appendChild(sel('style', C.style));
    var cs = el('select', { 'aria-label': 'consent', 'class': 'consent ' + x.consent });     /* F17: the parent's slip */
    ['yes', 'no', 'unknown'].forEach(function (k) { var o = el('option', { value: k }, C.consent[k]); if (x.consent === k) o.selected = true; cs.appendChild(o); });
    cs.onchange = function () { api('/api/teacher/roster', { slug: x.slug, consent: cs.value }).then(function (j) { $('status-msg').textContent = j.ok ? S.saved + (j.message ? ' · ' + j.message : '') : (j.error || ''); cs.className = 'consent ' + cs.value; }); };
    td.appendChild(cs);
    td.appendChild(sel('greeting', { '어머님': '어머님', '아버님': '아버님', '보호자님': '보호자님' }));
    tr.appendChild(td);
    var td2 = el('td');
    var sum = el('input', { type: 'text', maxlength: '120', placeholder: C.summary_ph });
    var kind = el('select', { 'aria-label': 'kind' });
    ['call', 'message', 'meeting'].forEach(function (k) { kind.appendChild(el('option', { value: k }, C.kind[k])); });
    var b = btn(C.log);
    b.onclick = function () {
      api('/api/teacher/contact', { slug: x.slug, kind: kind.value, tag: tg.value, summary: sum.value }).then(function (j) {
        if (j.ok) reloadContacts(); else $('status-msg').textContent = j.error || '';
      });
    };
    var tg = el('select', { 'aria-label': 'tag' });
    ['rotation', 'trigger', 'positive'].forEach(function (k) { tg.appendChild(el('option', { value: k }, C.tag[k])); });
    td2.appendChild(kind); td2.appendChild(tg); td2.appendChild(sum); td2.appendChild(b);
    tr.appendChild(td2);
    return tr;
  }

  function loadContacts() {
    api('/api/teacher/contacts').then(function (j) {
      var need = $('need-rows'); need.innerHTML = '';
      if (!j.flags.length) need.appendChild(el('p', { 'class': 'muted' }, C.none));
      j.flags.forEach(function (f) { need.appendChild(needCard(f)); });

      planWeek = j.plan.week;
      var pr = $('plan-rows'); pr.innerHTML = '';
      j.plan.items.forEach(function (it) { pr.appendChild(planRow(it)); });
      var mv = $('plan-moved'); mv.innerHTML = '';
      j.plan.moved.forEach(function (it) { mv.appendChild(el('li', {}, C.moved + ' · ' + C.kind[it.type] + ' ' + it.class_name + ' ' + it.display_name)); });
      $('plan-total').textContent = fmt(C.total, { t: j.plan.total, b: j.plan.budget });
      $('plan-cover').textContent = j.plan.coverage.text;

      $('status-hint').textContent = fmt(C.status_hint, { a: j.settings.amber, r: j.settings.red })
        + ' · ' + fmt(C.mix, { r: j.mix.rotation, t: j.mix.trigger, p: j.mix.positive });
      var sr = $('status-rows'); sr.innerHTML = '';
      j.status.forEach(function (x) { sr.appendChild(statusRow(x)); });

      $('good-hint').textContent = j.month_end ? C.good_hint : C.good_not_yet;
      var gr = $('good-rows'); gr.innerHTML = '';
      j.good_news.forEach(function (g) {
        var w = el('div', { 'class': 'wrow', 'data-slug': g.slug });
        w.appendChild(el('h3', {}, g.class_name + ' · ' + g.display_name));
        var ta = el('textarea', { rows: '6', 'class': 'gtext' }); ta.value = g.text; w.appendChild(ta);
        var cp = btn(S.copy); cp.onclick = function () { copy(ta.value, cp); };
        var sent = btn(C.messaged);
        sent.onclick = function () {
          api('/api/teacher/contact', { slug: g.slug, kind: 'message', tag: 'positive', summary: '이달의 좋은 소식' }).then(function (r) {
            if (r.ok) { sent.disabled = true; sent.textContent = '✓ ' + C.messaged; loadContacts(); }
          });
        };
        w.appendChild(cp); w.appendChild(sent); gr.appendChild(w);
      });

      ['start_time', 'end_time', 'self_study_until', 'late_min', 'late_count', 'late_avg_min'].forEach(function (k) {
        var inp = $('pol-' + k), v = k === 'start_time' ? j.policy.start : k === 'end_time' ? j.policy.end : j.policy[k];
        if (inp && document.activeElement !== inp) inp.value = v === null || v === undefined ? '' : v;
      });
    });
  }
  $('pol-save').onclick = function () {
    var b = {};
    ['start_time', 'end_time', 'self_study_until', 'late_min', 'late_count', 'late_avg_min'].forEach(function (k) { b[k] = $('pol-' + k).value; });
    api('/api/teacher/policy', b).then(function (j) { $('pol-msg').textContent = j.ok ? S.saved : (j.error || ''); if (j.ok) loadContacts(); });
  };

  function loadSolved() {
    api('/api/teacher/solved').then(function (j) {
      var tb = $('solved-rows'); tb.innerHTML = '';
      j.rows.forEach(function (r) {
        var tr = el('tr', { 'data-slug': r.slug, 'data-day': r.day });
        tr.appendChild(el('td', {}, r.day.slice(5) + ' · ' + r.display_name));
        var inp = el('input', { type: 'number', min: '0', max: '60', inputmode: 'numeric', 'aria-label': r.display_name });
        inp.value = r.solved === null ? '' : r.solved;
        var b = btn(S.save), st = el('span', { 'class': 'muted' });
        b.onclick = function () {
          api('/api/teacher/solved', { slug: r.slug, day: r.day, solved: inp.value }).then(function (k) { st.textContent = k.ok ? '✓' : (k.error || ''); });
        };
        var td = el('td'); td.appendChild(inp); td.appendChild(b); td.appendChild(st);
        tr.appendChild(td); tb.appendChild(tr);
      });
    });
  }

  loadContacts(); loadSolved();

  /* ------------------------------------------------------------ F14: weekly-test rounds */
  var R = S.r, editing = null;
  function dtLocal(v) { return (v || '').replace(' ', 'T'); }
  function loadRounds() {
    api('/api/teacher/rounds').then(function (j) {
      var box = $('rf-classes'); box.innerHTML = '';
      j.classes.forEach(function (c) {
        var lab = el('label'); var cb = el('input', { type: 'checkbox', value: c, 'class': 'rf-cls' });
        lab.appendChild(cb); lab.appendChild(document.createTextNode(' ' + c)); box.appendChild(lab);
      });
      var ul = $('round-rows'); ul.innerHTML = '';
      j.rounds.forEach(function (r) {
        var li = el('li', { 'class': 'rrow2', 'data-id': r.id });
        var st = r.open ? R.open : (r.close_at < new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 16).replace('T', ' ') ? R.closed : R.upcoming);
        li.appendChild(el('b', {}, r.label + ' '));
        li.appendChild(el('span', { 'class': 'muted' }, r.id + ' · ' + r.classes.join(', ') + ' · N=' + r.n + ' · ' + r.open_at + ' ~ ' + r.close_at + ' · ' + st + ' · '));
        li.appendChild(el('span', { 'class': 'rprog' }, fmt(R.progress, { d: r.done, t: r.total })));
        var bd = btn('✓'), be = btn(R.edit), bx = btn(R.export), bc = btn(R.close);
        bd.onclick = function () { showProgress(r.id); };
        be.onclick = function () {
          editing = r.id; $('rf-h').textContent = R.edit + ' · ' + r.id;
          $('rf-id').value = r.id; $('rf-id').disabled = true; $('rf-grade').value = r.grade; $('rf-n').value = r.n;
          $('rf-open').value = dtLocal(r.open_at); $('rf-close').value = dtLocal(r.close_at); $('rf-label').value = r.label;
          Array.prototype.forEach.call(document.querySelectorAll('.rf-cls'), function (c) { c.checked = r.classes.indexOf(c.value) >= 0; });
        };
        bx.onclick = function () { api('/api/teacher/round', { action: 'export', id: r.id }).then(function (k) { $('rf-msg').textContent = k.ok ? k.message : (k.error || ''); }); };
        bc.onclick = function () {
          if (!window.confirm(R.confirm_close)) return;
          api('/api/teacher/round', { action: 'close', id: r.id }).then(function (k) { $('rf-msg').textContent = k.ok ? (k.message || S.saved) : (k.error || ''); loadRounds(); });
        };
        [bd, be, bx].forEach(function (b) { li.appendChild(b); });
        if (r.open) li.appendChild(bc);
        ul.appendChild(li);
      });
    });
  }
  function showProgress(id) {
    api('/api/teacher/round?id=' + encodeURIComponent(id)).then(function (p) {
      var box = $('round-detail'); box.innerHTML = '';
      box.appendChild(el('p', {}, p.label + ' · ' + fmt(R.progress, { d: p.done, t: p.total })));
      var ul = el('ul', { 'class': 'tlist rdone' });
      p.students.forEach(function (x) { ul.appendChild(el('li', { 'class': x.done ? 'ok' : '' }, (x.done ? '✓ ' : '· ') + x.class_name + ' ' + x.display_name + (x.at ? ' · ' + x.at : ''))); });
      box.appendChild(ul);
    });
  }
  function resetRoundForm() {
    editing = null; $('rf-h').textContent = R.add; $('rf-id').disabled = false;
    ['rf-id', 'rf-open', 'rf-close', 'rf-label'].forEach(function (k) { $(k).value = ''; });
    Array.prototype.forEach.call(document.querySelectorAll('.rf-cls'), function (c) { c.checked = false; });
  }
  $('rf-new').onclick = resetRoundForm;
  $('rf-save').onclick = function () {
    var cls = Array.prototype.filter.call(document.querySelectorAll('.rf-cls'), function (c) { return c.checked; }).map(function (c) { return c.value; });
    var body = { action: editing ? 'edit' : 'add', id: editing || $('rf-id').value.trim(), grade: $('rf-grade').value, n: Number($('rf-n').value),
      classes: cls, open_at: $('rf-open').value.replace('T', ' '), close_at: $('rf-close').value.replace('T', ' '), label: $('rf-label').value.trim() };
    api('/api/teacher/round', body).then(function (j) {
      $('rf-msg').textContent = j.ok ? (S.saved + (j.message ? ' · ' + j.message : '')) : (j.error || '');
      if (j.ok) { resetRoundForm(); loadRounds(); }
    });
  };
  loadRounds();

  /* ------------------------------------------------------------ G1: 점검 (doctor) + G2: 백업 */
  $('dr-run').onclick = function () {
    $('dr-msg').textContent = S.running;
    api('/api/teacher/doctor', {}).then(function (j) {
      var ul = $('dr-lines'); ul.innerHTML = '';
      j.lines.forEach(function (x) {
        var li = el('li', { 'class': 'dl ' + ({ '✓': 'ok', '⚠': 'warn', '✗': 'bad' }[x.status] || '') });
        li.appendChild(el('b', {}, x.status + ' ')); li.appendChild(document.createTextNode(x.label + (x.detail ? ' — ' + x.detail : '')));
        if (x.fix && x.status !== '✓') li.appendChild(el('div', { 'class': 'muted fix' }, '→ ' + x.fix));
        ul.appendChild(li);
      });
      $('dr-msg').textContent = j.ok ? '✓' : '✗';
    });
  };
  $('bk-run').onclick = function () {
    $('dr-msg').textContent = S.running;
    api('/api/teacher/backup', {}).then(function (j) { $('dr-msg').textContent = j.ok ? j.message : (j.error || ''); });
  };

  /* ------------------------------------------------------------ F15.1/F15.2: 영상 (class numbers; per student = teacher only) */
  var VV = S.v, vvText = '';
  function loadVideos(days) {
    api('/api/teacher/videos?days=' + days).then(function (j) {
      $('vv-range').textContent = j.from + ' ~ ' + j.to; vvText = j.text;
      var box = $('vv-rows'); box.innerHTML = '';
      j.classes.forEach(function (c) {
        box.appendChild(el('h3', {}, c.name));
        var t = el('table', { 'class': 'ttable vv' }), tb = el('tbody');
        if (!c.videos.length) box.appendChild(el('p', { 'class': 'muted' }, VV.none));
        c.videos.forEach(function (v) {
          var tr = el('tr', { 'data-v': v.id });
          tr.appendChild(el('td', {}, (v.id === c.popular ? '🔥 ' : '') + v.title));
          tr.appendChild(el('td', { 'class': 'tnum' }, fmt(VV.students, { n: v.students, t: c.active })));
          tr.appendChild(el('td', { 'class': 'tnum' }, fmt(VV.avg, { p: v.avg_pct }) + ' · ' + fmt(VV.minutes, { m: v.minutes })));
          tr.appendChild(el('td', { 'class': 'tnum muted' }, typeof v.views === 'number' || typeof v.views_7d === 'number'
            ? fmt(VV.views, { a: (v.views || 0).toLocaleString('ko-KR'), w: (v.views_7d || 0).toLocaleString('ko-KR'), n: v.students }) : ''));
          tb.appendChild(tr);
        });
        t.appendChild(tb); if (c.videos.length) box.appendChild(t);
        var det = el('details'); det.appendChild(el('summary', {}, VV.per_student));
        var ul = el('ul', { 'class': 'tlist' });
        c.students.forEach(function (s) { ul.appendChild(el('li', {}, s.display_name + ' · ' + s.n + (s.titles.length ? ' · ' + s.titles.join(', ') : ''))); });
        det.appendChild(ul); box.appendChild(det);
      });
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll('#t-videos [data-days]'), function (b) {
    b.onclick = function () { loadVideos(Number(b.getAttribute('data-days'))); };
  });
  $('vv-copy').onclick = function () { copy(vvText, $('vv-copy')); };
  loadVideos(7);

  /* ------------------------------------------------------------ F18.3: 학생 리뷰 (승인 / 숨김 before anyone sees it) */
  var RVT = S.rv;
  function rvtRow(r, actions) {
    var li = el('li', { 'class': 'wrow' });
    li.appendChild(el('span', {}, r.day + ' · ' + r.class_name + ' · ' + r.display_name + (r.hide_name ? ' (' + RVT.hidden_name + ')' : '') + ' — ' + r.text));
    actions.forEach(function (a) {
      var b = btn(RVT[a]); b.setAttribute('data-act', a); b.setAttribute('data-id', r.id); li.appendChild(b);
    });
    return li;
  }
  function loadReviews() {
    api('/api/teacher/reviews').then(function (j) {
      $('rvt-period').textContent = j.period || '';
      var p = $('rvt-pending'); p.innerHTML = '';
      j.pending.forEach(function (r) { p.appendChild(rvtRow(r, ['approve', 'hide'])); });
      if (!j.pending.length) p.appendChild(el('li', { 'class': 'muted' }, RVT.none));
      var a = $('rvt-approved'); a.innerHTML = '';
      j.approved.forEach(function (r) { a.appendChild(rvtRow(r, ['hide'])); });
      if (!j.approved.length) a.appendChild(el('li', { 'class': 'muted' }, RVT.none));
      var c = $('rvt-classes'); c.innerHTML = '';
      j.classes.forEach(function (k) {
        var li = el('li', { 'class': 'wrow' });
        li.appendChild(el('span', {}, k.name + ' · ' + fmt(RVT.approved_n, { n: k.approved }) + ' · ' + fmt(RVT['st_' + k.state], { at: k.at })
          + (k.votes ? ' · ' + fmt(RVT.votes, { n: k.votes }) : '')));
        if (k.state !== 'open') { var b = btn(RVT.vote_now); b.setAttribute('data-vote', k.name); li.appendChild(b); }
        c.appendChild(li);
      });
      var w = $('rvt-wins'); w.innerHTML = '';
      j.wins.forEach(function (x) { w.appendChild(el('li', {}, x.period + ' · ' + x.class_name + ' · ' + x.display_name + ' · ' + fmt(RVT.votes, { n: x.votes }) + ' — ' + x.text)); });
      if (!j.wins.length) w.appendChild(el('li', { 'class': 'muted' }, RVT.none));
    });
  }
  $('t-reviews').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    var call = b.getAttribute('data-vote') ? api('/api/teacher/review-vote', { 'class': b.getAttribute('data-vote') })
      : b.getAttribute('data-act') ? api('/api/teacher/review', { id: b.getAttribute('data-id'), action: b.getAttribute('data-act') }) : null;
    if (!call) return;
    b.disabled = true;
    call.then(function (j) { $('rvt-msg').textContent = j.ok ? (j.message || S.saved) : (j.error || ''); loadReviews(); });
  });
  loadReviews();

  /* ------------------------------------------------------------ F15.3: 알림 보내기 (counts only, never who) */
  var NP = { texts: {} };
  function loadPush() {
    api('/api/teacher/push').then(function (j) {
      NP.texts = j.texts || {};
      var sel = $('np-class'); while (sel.options.length > 1) sel.remove(1);
      (classesAll.length ? classesAll : []).forEach(function (c) { sel.appendChild(el('option', { value: c.name }, c.name)); });
      $('np-count').textContent = !j.enabled ? S.n.off : j.ok ? fmt(S.n.count, { s: j.students, p: j.parents }) : (j.error || '');
      $('np-send').disabled = !j.enabled;
      if (!$('np-text').value) $('np-text').value = NP.texts[$('np-kind').value] || '';
    });
  }
  $('np-kind').onchange = function () { $('np-text').value = NP.texts[$('np-kind').value] || ''; };
  $('np-send').onclick = function () {
    $('np-msg').textContent = S.running;
    api('/api/teacher/push-send', { 'class': $('np-class').value, kind: $('np-kind').value, text: $('np-text').value }).then(function (j) {
      $('np-msg').textContent = !j.ok ? (j.error || '') : j.quiet ? '22:00–08:00: 보내지 않았어요 (기록됨)'
        : '보냄 ' + j.sent + ' · 오늘 이미 받음 ' + j.capped + (j.removed ? ' · 해지됨 ' + j.removed : '');
      loadPush();
    });
  };
  setTimeout(loadPush, 300);   /* after the roster has loaded the class list */

  /* ------------------------------------------------------------ F16: 캐나다 연습 모드 (local switch, logged in events) */
  function showPractice(mode) {
    $('pm-toggle').checked = mode === 'manitoba';
    $('pm-help-k').hidden = mode === 'manitoba'; $('pm-help-m').hidden = mode !== 'manitoba';
  }
  api('/api/teacher/practice').then(function (j) { showPractice(j.mode); });
  $('pm-toggle').onchange = function () {
    api('/api/teacher/practice', { mode: $('pm-toggle').checked ? 'manitoba' : 'korea' }).then(function (j) {
      if (j.ok) { showPractice(j.mode); loadNotes(); }
    });
  };

  /* ------------------------------------------------------------ F16.4: 측정 (class level, last 8 weeks) */
  function loadMetrics() {
    var c = $('mt-class').value;
    api('/api/teacher/metrics' + (c ? '?class=' + encodeURIComponent(c) : '')).then(function (j) {
      var head = $('mt-head'), body = $('mt-rows'); head.innerHTML = ''; body.innerHTML = '';
      var hr = el('tr'); j.columns.forEach(function (k) { if (k !== 'class') hr.appendChild(el('th', {}, k)); }); head.appendChild(hr);
      j.rows.forEach(function (r) {
        var tr = el('tr'); j.columns.forEach(function (k) { if (k !== 'class') tr.appendChild(el('td', { 'class': 'tnum' }, String(r[k]))); });
        body.appendChild(tr);
      });
    });
  }
  $('mt-class').onchange = loadMetrics;
  setTimeout(function () {
    var sel = $('mt-class'); classesAll.forEach(function (c) { sel.appendChild(el('option', { value: c.name }, c.name)); });
    loadMetrics();
  }, 400);

  loadRoster(); loadNotes(); loadMoments(); loadWeekly();
})();
