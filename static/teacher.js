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
      var call = el('input', { type: 'text', value: s.callsign || '', maxlength: '10', size: '5', placeholder: S.vc.callsign, 'aria-label': S.vc.callsign, 'class': 'callsign' });
      var save = btn(S.save);
      save.onclick = function () { api('/api/teacher/roster', { slug: s.slug, display_name: name.value, callsign: call.value }).then(msg); };
      var td1 = el('td'); td1.appendChild(name); td1.appendChild(call); td1.appendChild(save);
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
      vcClasses(j.board_class);
      skClasses(j.board_class);
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
    var sh = el('select', { 'aria-label': 'share_classwork', 'class': 'share ' + x.share_classwork });   /* F21.1: 수업 문제 on the parent page */
    ['no', 'yes'].forEach(function (k) { var o = el('option', { value: k }, C.share[k]); if (x.share_classwork === k) o.selected = true; sh.appendChild(o); });
    sh.onchange = function () { api('/api/teacher/roster', { slug: x.slug, share_classwork: sh.value }).then(function (j) { $('status-msg').textContent = j.ok ? S.saved : (j.error || ''); sh.className = 'share ' + sh.value; }); };
    td.appendChild(sh);
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
          var rxd = el('td', { 'class': 'tnum vrx-t' });                     /* F19.2: reactions, 어려워요 included (teacher only) */
          (j.labels || []).forEach(function (l) {
            var n = (v.rx || {})[l.key] || 0; if (!n) return;
            rxd.appendChild(el(l.key === 'hard' ? 'b' : 'span', { 'class': 'vrx-n' + (l.key === 'hard' ? ' hard' : ''), title: l.text }, l.emoji + ' ' + n + ' '));
          });
          tr.appendChild(rxd);
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

  /* ------------------------------------------------------------ F19.1: 음성 메모 가져오기 (preview first; 저장 stores) */
  var VC = S.vc, vcRows = [], vcStudents = [];
  function vcClasses(boardClass) {
    var sel = $('vc-class'), v = sel.value;
    sel.innerHTML = ''; classesAll.forEach(function (c) { sel.appendChild(el('option', { value: c.name }, c.name)); });
    sel.value = v || boardClass || (classesAll[0] || {}).name || '';
  }
  function vcRender() {
    var tb = $('vc-rows'); tb.innerHTML = '';
    vcRows.forEach(function (r) {
      var tr = el('tr', { 'class': r.dropped ? 'off' : '' });
      var keep = el('input', { type: 'checkbox', 'aria-label': '✓' }); keep.checked = !!r.keep; keep.disabled = !!r.dropped;
      keep.onchange = function () { r.keep = keep.checked; };
      var t0 = el('td'); t0.appendChild(keep); tr.appendChild(t0);
      tr.appendChild(el('td', {}, VC.kinds[r.kind] + (r.dropped ? ' · ' + VC.dropped : '')));
      tr.appendChild(el('td', {}, r.kind === 'praise' ? (r.heard || '') + (r.match === 'fuzzy' ? ' (' + VC.fuzzy + ')' : '') : ''));
      var td = el('td');
      if (r.kind === 'praise' && r.target === 'class') td.appendChild(el('b', {}, VC['class']));   /* 반 전체: the class goal */
      else if (r.kind === 'praise') {
        var sel = el('select', { 'aria-label': VC.who });
        sel.appendChild(el('option', { value: '' }, VC.who));
        vcStudents.forEach(function (st) { sel.appendChild(el('option', { value: String(st.id) }, st.display_name + (st.callsign ? ' (' + st.callsign + ')' : ''))); });
        sel.value = r.student_id ? String(r.student_id) : '';
        sel.onchange = function () { r.student_id = sel.value ? Number(sel.value) : null; r.keep = !!sel.value && !r.dropped; keep.checked = r.keep; };
        td.appendChild(sel);
        if (!r.student_id && !r.dropped) (r.candidates || []).forEach(function (c) {      /* 누구?: 2-3 candidates, never guessed */
          var b = btn(c.display_name); b.className += ' chipbtn';
          b.onclick = function () { sel.value = String(c.id); sel.onchange(); };
          td.appendChild(b);
        });
      }
      tr.appendChild(td);
      var pts = el('input', { type: 'number', min: '0', max: '5', value: String(r.points || 0), 'aria-label': '🎤', 'class': 'pts' });
      pts.onchange = function () { r.points = Number(pts.value) || 0; };
      var tp = el('td'); if (r.kind === 'praise') tp.appendChild(pts); tr.appendChild(tp);
      var tx = el('input', { type: 'text', value: r.text, maxlength: '200', 'aria-label': 'text', 'class': 'vc-line' });
      tx.onchange = function () { r.text = tx.value; };
      var tt = el('td'); tt.appendChild(tx);
      if (r.private === 'negative') {                       /* 확인 필요: the teacher decides (a negated one leans 공개) */
        var pub = el('select', { 'aria-label': VC.hold, 'class': 'hold' });
        pub.appendChild(el('option', { value: '1' }, VC.hold + ' · ' + VC.show)); pub.appendChild(el('option', { value: '0' }, VC.hold + ' · ' + VC.hide));
        pub.value = r.publish ? '1' : '0'; pub.onchange = function () { r.publish = pub.value === '1'; };
        tt.appendChild(pub);
      } else if (r.private) tt.appendChild(el('span', { 'class': 'chip warn' }, VC.private + ' · ' + (VC.why[r.private] || r.private)));
      tr.appendChild(tt);
      tb.appendChild(tr);
    });
    $('vc-save').disabled = !vcRows.length;
  }
  $('vc-file').onchange = function () {
    var f = $('vc-file').files[0]; if (!f) return;
    var rd = new FileReader(); rd.onload = function () { $('vc-text').value = String(rd.result || ''); }; rd.readAsText(f, 'utf-8');
  };
  $('vc-preview').onclick = function () {
    api('/api/teacher/voice-preview', { 'class': $('vc-class').value, text: $('vc-text').value }).then(function (j) {
      if (!j.ok) { $('vc-msg').textContent = j.error || ''; return; }
      vcRows = j.rows; vcStudents = j.students;
      $('vc-msg').textContent = !j.callsigns ? VC.nocalls : j.who ? VC.who + ' ' + j.who : '';
      vcRender();
    });
  };
  $('vc-save').onclick = function () {
    $('vc-save').disabled = true;
    api('/api/teacher/voice-save', { 'class': $('vc-class').value, date: $('vc-date').value, rows: vcRows.map(function (r) {
      return { kind: r.kind, keep: !!r.keep && !r.dropped, student_id: r.student_id, points: r.points, text: r.text, target: r.target, publish: !!r.publish };
    }) }).then(function (j) {
      $('vc-msg').textContent = j.ok ? fmt(VC.saved, j) : (j.error || '');
      if (j.ok) { vcRows = []; vcRender(); $('vc-text').value = ''; loadVoice(); }
    });
  };
  function loadVoice() {
    api('/api/teacher/voice').then(function (j) {
      var c = $('vc-concepts'); c.innerHTML = '';
      j.concepts.forEach(function (x) { c.appendChild(el('li', {}, x.day + ' · ' + x.class_name + ' — ' + x.text)); });
      if (!j.concepts.length) c.appendChild(el('li', { 'class': 'muted' }, VC.none));
      var d = $('vc-drafts'); d.innerHTML = '';
      j.drafts.forEach(function (x) {
        var li = el('li', { 'class': 'wrow' }); li.appendChild(el('span', {}, x.day + ' · ' + x.class_name + ' — ' + x.text));
        [['approve', VC.approve], ['drop', VC.drop]].forEach(function (a) {
          var b = btn(a[1]); b.onclick = function () { api('/api/teacher/draft', { id: x.id, action: a[0] }).then(loadVoice); }; li.appendChild(b);
        });
        d.appendChild(li);
      });
      if (!j.drafts.length) d.appendChild(el('li', { 'class': 'muted' }, VC.none));
      var r = $('vc-recent'); r.innerHTML = '';
      j.shoutouts.forEach(function (x) {
        var li = el('li', { 'class': x.hold ? 'wrow hold' : '' }, x.day + ' · ' + x.class_name + ' · ' + x.display_name + ' 🎤' + x.points + ' — ' + x.text
          + (x.hold ? ' (' + VC.hold + ')' : x.private ? ' (' + VC.private + ')' : '') + (x.source === 'remote' ? ' 📱' : ''));
        if (x.hold) [[true, VC.show], [false, VC.hide]].forEach(function (a) {
          var b = btn(a[1]); b.onclick = function () { api('/api/teacher/shout', { id: x.id, show: a[0] }).then(loadVoice); }; li.appendChild(b);
        });
        r.appendChild(li);
      });
      (j.class_shouts || []).forEach(function (x) { r.appendChild(el('li', {}, x.day + ' · ' + x.class_name + ' · ' + VC['class'] + ' 🎤' + x.points + ' — ' + x.text)); });
      if (!j.shoutouts.length) r.appendChild(el('li', { 'class': 'muted' }, VC.none));
    });
  }
  loadVoice();

  /* ------------------------------------------------------------ F21.3: 기록 by class and day (every row carries its class) */
  function loadHistory() {
    api('/api/teacher/history?class=' + encodeURIComponent($('hi-class').value) + '&day=' + $('hi-day').value).then(function (j) {
      var ul = $('hi-rows'), H = S.hi; ul.innerHTML = '';
      if (!j.ok) { ul.appendChild(el('li', { 'class': 'muted' }, j.error || '')); return; }
      j.attitude.forEach(function (x) { ul.appendChild(el('li', {}, H.att + ' · ' + x.display_name + ' ' + x.points)); });
      j.shouts.forEach(function (x) { ul.appendChild(el('li', {}, H.shout + ' · ' + x.display_name + ' 🎤' + x.points + ' — ' + x.text + (x.source === 'remote' ? ' 📱' : ''))); });
      j.classwork.forEach(function (x) { ul.appendChild(el('li', {}, H.cw + ' · ' + x.label + ' · ' + x.n + ' · ' + x.k)); });
      j.notes.forEach(function (x) { ul.appendChild(el('li', {}, H.note + ' · ' + x.display_name + ' — ' + x.text)); });
      if (!ul.children.length) ul.appendChild(el('li', { 'class': 'muted' }, H.none));
    });
  }
  $('hi-load').onclick = loadHistory;
  $('hi-class').onchange = loadHistory;
  loadHistory();

  /* ------------------------------------------------------------ F21.2 / F21.4 / F21.5: 수업 문제, 실력 지도, 서술형, 사진 확인 */
  var SK = S.sk, MK = { o: '○', x: '✕', m: '–', p: '△' };
  function skClasses(boardClass) {
    var sel = $('sk-class'), v = sel.value;
    sel.innerHTML = ''; classesAll.forEach(function (c) { sel.appendChild(el('option', { value: c.name }, c.name)); });
    sel.value = v || boardClass || (classesAll[0] || {}).name || '';
    loadSkills();
  }
  function loadSkills() {
    var c = $('sk-class').value; if (!c) return;
    api('/api/teacher/classwork?class=' + encodeURIComponent(c)).then(function (j) {
      if (!j.ok) return;
      var dl = $('sk-sets'); dl.innerHTML = ''; j.set_names.forEach(function (n) { dl.appendChild(el('option', { value: n })); });
      var es = $('es-tags'); var keep = Array.prototype.map.call(es.selectedOptions, function (o) { return o.value; });
      es.innerHTML = ''; j.tags.forEach(function (t) { var o = el('option', { value: t }, t); if (keep.indexOf(t) >= 0) o.selected = true; es.appendChild(o); });
      var td = $('sk-today'); td.innerHTML = '';
      j.today.forEach(function (x) { td.appendChild(el('li', {}, SK.today + ': ' + x.label + ' · ' + x.n + ' · ' + x.k)); });
      var hd = $('sk-head'), rows = $('sk-rows'); hd.innerHTML = ''; rows.innerHTML = '';
      var tr = el('tr'); tr.appendChild(el('th', {}, '')); tr.appendChild(el('th', {}, '○ ✕ – △'));
      j.grid.tags.forEach(function (t) { tr.appendChild(el('th', {}, t)); }); hd.appendChild(tr);
      j.grid.students.forEach(function (s) {
        var r = el('tr'); r.appendChild(el('td', {}, s.display_name));
        r.appendChild(el('td', { 'class': 'tnum' }, s.totals.o + ' ' + s.totals.x + ' ' + s.totals.m + ' ' + s.totals.p));
        j.grid.tags.forEach(function (t) {
          var c2 = s.cells[t];
          r.appendChild(el('td', { 'class': 'tnum sk-cell ' + (c2 ? c2.status : '') }, c2 ? '○' + c2.o + ' ✕' + c2.x + (c2.m ? ' –' + c2.m : '') + (c2.p ? ' △' + c2.p : '') : ''));
        });
        rows.appendChild(r);
      });
      var pq = $('pq-rows'); pq.innerHTML = '';
      if (!j.queue.length) pq.appendChild(el('li', { 'class': 'muted' }, SK.none));
      j.queue.forEach(function (x) {
        var li = el('li', { 'class': 'wrow' + (x.flag ? ' hold' : '') });
        var img = el('img', { src: '/api/teacher/classwork-photo?student=' + x.student_id + '&day=' + x.day, alt: '', 'class': 'pq-img', loading: 'lazy' });
        img.onerror = function () { img.remove(); };
        li.appendChild(img);
        li.appendChild(el('span', {}, x.day + ' · ' + x.class_name + ' · ' + x.display_name + ' · ' + x.label + ' #' + x.quiz + ' · '
          + fmt(SK.read, { a: x.read_answer }) + ' · ' + fmt(SK.photo, { m: MK[x.photo_mark], c: Math.round(100 * (x.confidence || 0)) })
          + (x.self_mark ? ' · ' + fmt(SK.self, { m: MK[x.self_mark] }) : '') + (x.flag ? ' · ' + SK.flag : '')));
        ['o', 'x', 'm'].forEach(function (m) {
          var b = btn(MK[m]); b.onclick = function () { api('/api/teacher/classwork-decide', { id: x.id, mark: m }).then(loadSkills); }; li.appendChild(b);
        });
        pq.appendChild(li);
      });
    });
  }
  $('sk-class').onchange = loadSkills;
  $('sk-open').onclick = function () {
    api('/api/teacher/classwork-open', { 'class': $('sk-class').value, label: $('sk-label').value, n: $('sk-n').value }).then(function (j) {
      $('sk-msg').textContent = j.ok ? j.label + ' · ' + j.n : (j.error || ''); loadSkills();
    });
  };
  $('sk-export').onclick = function () {
    api('/api/teacher/classwork-export', { 'class': $('sk-class').value }).then(function (j) { $('sk-msg').textContent = j.ok ? j.path : (j.error || ''); });
  };
  var ES = [];
  function esDraw() {
    var tb = $('es-rows'); tb.innerHTML = '';
    var mx = $('es-max').value;
    ES.forEach(function (s) {
      var tr = el('tr'); tr.appendChild(el('td', {}, s.display_name));
      var td = el('td');
      if (mx) {
        var sc = el('input', { type: 'number', min: '0', max: mx, step: '0.5', value: s.score == null ? '' : String(s.score), 'class': 'pts', 'aria-label': 'score' });
        sc.onchange = function () { s.score = sc.value; }; td.appendChild(sc);
      } else ['o', 'p', 'x'].forEach(function (m) {
        var b = btn(MK[m], s.mark === m ? 'primary' : 'ghost');
        b.onclick = function () { s.mark = s.mark === m ? '' : m; esDraw(); }; td.appendChild(b);
      });
      tr.appendChild(td);
      var nt = el('input', { type: 'text', maxlength: '80', value: s.note || '', placeholder: SK.note, 'aria-label': SK.note });
      nt.onchange = function () { s.note = nt.value; };
      var tn = el('td'); tn.appendChild(nt); tr.appendChild(tn);
      tb.appendChild(tr);
    });
    $('es-save').hidden = !ES.length;
  }
  $('es-load').onclick = function () {
    api('/api/teacher/essay?class=' + encodeURIComponent($('sk-class').value) + '&day=' + $('es-day').value + '&label=' + encodeURIComponent($('es-label').value)).then(function (j) {
      if (!j.ok) { $('es-msg').textContent = j.error || ''; return; }
      ES = j.students; if (j.max && !$('es-max').value) $('es-max').value = j.max;
      Array.prototype.forEach.call($('es-tags').options, function (o) { o.selected = j.tags.indexOf(o.value) >= 0 || o.selected; });
      esDraw();
    });
  };
  $('es-max').onchange = esDraw;
  $('es-save').onclick = function () {
    api('/api/teacher/essay', { 'class': $('sk-class').value, day: $('es-day').value, label: $('es-label').value, max: $('es-max').value,
      tags: Array.prototype.map.call($('es-tags').selectedOptions, function (o) { return o.value; }),
      rows: ES.map(function (s) { return { student_id: s.id, mark: s.mark, score: s.score, note: s.note }; }) }).then(function (j) {
      $('es-msg').textContent = j.ok ? fmt(SK.saved, { n: j.changed }) : (j.error || ''); if (j.ok) loadSkills();
    });
  };

  /* ------------------------------------------------------------ F19.4: 리모컨 (the setup QR is shown on screen only) */
  var rmLink = '';
  function rmShow(rotate) {
    if (rotate && !window.confirm(S.vc.rm_rotate_confirm || '새 키를 만들면 지금 연결된 휴대폰은 QR을 다시 열어야 해요.')) return;
    api('/api/teacher/remote', { rotate: !!rotate }).then(function (j) {
      if (!j.ok) { $('rm-msg').textContent = j.error || S.vc.rm_need; return; }
      rmLink = j.link; $('rm-qr').innerHTML = j.qr; $('rm-qr').hidden = false; $('rm-copy').hidden = false;
      $('rm-msg').textContent = j.message || '';
    });
  }
  $('rm-show').onclick = function () { rmShow(false); };
  $('rm-rotate').onclick = function () { rmShow(true); };
  $('rm-copy').onclick = function () { copy(rmLink, $('rm-copy')); };

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
