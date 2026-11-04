// F19.4 the teacher remote: the page, teacher-token-only data, the 3 s guard, replays, undo (2 min), the class target.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/index.js';
import { REMOTE_JS } from '../src/remote_client.js';
import { A, ALL, TEACHER, TOK, allow, call, mkEnv } from './helpers.mjs';

const R = (path, body, token = TEACHER) => new Request('https://w.example' + path, body
  ? { method: 'POST', headers: { 'X-Teacher-Token': token, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
  : { headers: { 'X-Teacher-Token': token } });
let n = 0;
const id = () => (++n).toString(16).padStart(16, '0');

test('F19.4 the page is public, its data needs the teacher token (student and parent tokens are refused)', async () => {
  const env = mkEnv(); await allow(env, ALL);
  const page = await worker.fetch(new Request('https://w.example/t/remote/'), env);
  const html = await page.text();
  assert.equal(page.status, 200); assert.match(html, /AMP 리모컨/); assert.match(page.headers.get('Content-Security-Policy'), /script-src 'self'/);
  assert.ok(!html.includes(TOK));                                           // the shell carries no record and no token
  assert.equal((await worker.fetch(new Request('https://w.example/t/remote'), env)).status, 302);
  assert.equal((await worker.fetch(new Request('https://w.example/t/remote/remote.js'), env)).status, 200);
  new Function(REMOTE_JS);                                                  // the client parses
  for (const bad of ['', TOK, 'p'.repeat(26), TEACHER + 'x']) {
    assert.equal((await call(env, R('/t/remote/roster', null, bad))).status, 401, bad);
    assert.equal((await call(env, R('/t/remote/shout', { id: id(), t: TOK, points: 1, text: 'x', at: Date.now() }, bad))).status, 401);
  }
  assert.equal((await call(env, new Request('https://w.example/remote-roster', { method: 'PUT', headers: { 'X-Teacher-Token': TOK, 'Content-Type': 'application/json' }, body: '{}' }))).status, 401);
});

test('F19.4 roster blob round trip; save, the 3 s double-tap guard, replay, undo within 2 minutes, today counts, pull', async () => {
  const env = mkEnv(); await allow(env, ALL);
  const blob = 'A'.repeat(80);
  assert.equal((await call(env, new Request('https://w.example/remote-roster', { method: 'PUT', headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' }, body: JSON.stringify({ blob }) }))).body.ok, true);
  assert.equal((await call(env, R('/t/remote/roster'))).body.blob, blob);
  const t0 = Date.now();
  const first = { id: id(), t: TOK, points: 2, text: '풀이 깔끔', at: t0 };
  assert.deepEqual((await call(env, R('/t/remote/shout', first))).body, { ok: true });
  assert.deepEqual((await call(env, R('/t/remote/shout', first))).body, { ok: true, dup: true });                 // offline replay
  assert.equal((await call(env, R('/t/remote/shout', { ...first, id: id(), at: t0 + 1500 }))).body.error, 'too_fast');   // a double tap
  assert.equal((await call(env, R('/t/remote/shout', { id: id(), t: A, points: 1, text: '', at: t0 + 1000 }))).body.ok, true);   // another student
  const second = { id: id(), t: TOK, points: 1, text: '질문 좋았어', at: t0 + 4000 };
  assert.equal((await call(env, R('/t/remote/shout', second))).body.ok, true);
  assert.equal((await call(env, R('/t/remote/shout', { id: id(), t: TOK, points: 9, text: 'x', at: t0 }))).status, 400);
  assert.equal((await call(env, R('/t/remote/shout', { id: id(), t: 'z'.repeat(26), points: 1, text: 'x', at: t0 }))).body.error, 'unknown_token');
  assert.deepEqual((await call(env, R('/t/remote/today?class=c1'))).body.counts, { [TOK]: 2, [A]: 1 });
  assert.equal((await call(env, R('/t/remote/undo', { id: second.id }))).body.ok, true);
  assert.equal((await call(env, R('/t/remote/undo', { id: second.id }))).body.error, 'not_found');
  env.DB.db.prepare('UPDATE shouts SET ts = ? WHERE id = ?').run(new Date(Date.now() - 121e3).toISOString(), first.id);
  assert.equal((await call(env, R('/t/remote/undo', { id: first.id }))).body.error, 'too_late');
  assert.deepEqual((await call(env, R('/t/remote/today?class=c1'))).body.counts, { [TOK]: 1, [A]: 1 });
  const pulled = (await call(env, new Request('https://w.example/shouts-pull?since=2020-01-01', { headers: { 'X-Teacher-Token': TEACHER } }))).body.items;
  assert.equal(pulled.length, 3); assert.equal(pulled.find((x) => x.id === second.id).deleted, 1);
});

test('F19.4 반 전체: a class shout-out (token *) for a known class key only', async () => {
  const env = mkEnv(); await allow(env, ALL);
  assert.equal((await call(env, R('/t/remote/shout', { id: id(), t: '*', class: 'c1', points: 2, text: '다 같이 맞힘', at: Date.now() }))).body.ok, true);
  assert.equal((await call(env, R('/t/remote/shout', { id: id(), t: '*', class: 'zz9', points: 2, text: 'x', at: Date.now() }))).body.error, 'unknown_token');
  assert.equal((await call(env, R('/t/remote/today?class=c1'))).body.counts['*'], 1);
});
