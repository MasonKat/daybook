// F21.2 수업 문제: opened by the teacher (label + N only), marked ○ ✕ – by each student until the end of the class day.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ALL, B, ORIGIN, TEACHER, TOK, allow, call, mkEnv, studentGet } from './helpers.mjs';

const open = (env, body, path = '/classwork-open') => call(env, new Request('https://w.example' + path, { method: 'POST',
  headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
const mark = (env, s, marks, t = TOK) => { const fd = new FormData(); fd.append('t', t); fd.append('s', s); fd.append('marks', marks);
  return call(env, new Request('https://w.example/classwork-mark', { method: 'POST', body: fd, headers: { Origin: ORIGIN } })); };

test('F21.2 open (board / remote), mark ○✕– (– is its own mark), class scope, the window, pull', async () => {
  const env = mkEnv(); await allow(env, ALL);
  const a = (await open(env, { class: 'c1', label: '쎈 이차방정식 A', n: 6 })).body;
  assert.ok(a.ok && /^[a-f0-9]{16}$/.test(a.sid) && a.until.endsWith('Z'));
  assert.equal((await open(env, { class: 'zz', label: 'x', n: 6 })).status, 400);
  assert.equal((await open(env, { class: 'c1', label: '', n: 6 })).status, 400);
  const r = (await open(env, { class: 'c1', label: '수업 문제', n: 3 }, '/t/remote/classwork')).body;   // from the phone remote
  assert.ok(r.ok);
  const st = (await studentGet(env, `/status?t=${TOK}`)).body.classwork;
  assert.deepEqual(st.map((x) => [x.label, x.n, x.mine]), [['쎈 이차방정식 A', 6, null], ['수업 문제', 3, null]]);
  assert.deepEqual((await studentGet(env, `/status?t=${B}`)).body.classwork, []);                 // another class sees nothing
  assert.equal((await mark(env, a.sid, 'oxmoox')).body.ok, true);
  assert.equal((await mark(env, a.sid, 'ooo')).body.error, 'bad_marks');                        // N marks exactly
  assert.equal((await mark(env, a.sid, 'oxzoox')).body.error, 'bad_marks');
  assert.equal((await mark(env, a.sid, 'oxmoox', B)).body.error, 'unknown_set');                // not your class
  assert.equal((await mark(env, a.sid, 'ooooox')).body.ok, true);                               // an edit replaces it
  assert.equal((await studentGet(env, `/status?t=${TOK}`)).body.classwork[0].mine, 'ooooox');
  env.DB.db.prepare('UPDATE cwsets SET open_until = ? WHERE sid = ?').run(new Date(Date.now() - 1000).toISOString(), a.sid);
  assert.equal((await mark(env, a.sid, 'oooooo')).body.error, 'closed');                        // after the class day
  const pulled = (await call(env, new Request('https://w.example/classwork-pull?since=2020-01-01', { headers: { 'X-Teacher-Token': TEACHER } }))).body;
  assert.equal(pulled.sets.length, 2); assert.deepEqual(pulled.marks.map((x) => x.marks), ['ooooox']);
  assert.equal((await call(env, new Request('https://w.example/classwork-pull', { headers: { 'X-Teacher-Token': TOK } }))).status, 401);
});
