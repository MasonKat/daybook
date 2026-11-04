// F22.4 ⚙ 설정: per-token settings (reorder, never hide) and an own avatar picture that waits for the teacher.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PREF_SECTIONS, checkPrefs } from '../src/index.js';
import { ALL, B, ORIGIN, TEACHER, TOK, allow, call, mkEnv, studentGet } from './helpers.mjs';

const post = (env, path, fields) => { const fd = new FormData(); for (const [k, v] of Object.entries(fields)) fd.append(k, v);
  return call(env, new Request('https://w.example' + path, { method: 'POST', body: fd, headers: { Origin: ORIGIN } })); };
const tpost = (env, path, body) => call(env, new Request('https://w.example' + path, { method: 'POST',
  headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
const JPEG = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(3000, 5)]).toString('base64');

test('F22.4 settings: every id exactly once (no hiding), unknown ids refused, follows the token across devices', async () => {
  const env = mkEnv(); await allow(env, ALL);
  const rings = [...PREF_SECTIONS.rings].reverse();
  assert.equal((await post(env, '/prefs', { t: TOK, prefs: JSON.stringify({ rings, avatar: 'p:cat-2' }) })).body.ok, true);
  const got = (await studentGet(env, `/prefs?t=${TOK}`)).body;
  assert.deepEqual(got.prefs, { rings, avatar: 'p:cat-2' }); assert.equal(got.avatar, null);
  const bad = async (o) => (await post(env, '/prefs', { t: TOK, prefs: JSON.stringify(o) })).body.error;
  assert.equal(await bad({ rings: rings.slice(1) }), 'bad_prefs');                               // a hide attempt
  assert.equal(await bad({ rings: [...rings.slice(1), rings[1]] }), 'bad_prefs');                 // a duplicate
  assert.equal(await bad({ rings: [...rings.slice(1), 'ads'] }), 'bad_prefs');                     // an unknown id
  assert.equal(await bad({ rings, css: 'x' }), 'bad_prefs');
  assert.equal(await bad({ avatar: 'https://x' }), 'bad_prefs');
  assert.equal(await bad({ theme: 'hotpink' }), 'bad_prefs');                                      // F22.5: presets only
  assert.equal(await bad({ mode: 'sepia' }), 'bad_prefs');
  assert.equal(await bad({ bg: '../x.png' }), 'bad_prefs');
  assert.equal((await post(env, '/prefs', { t: TOK, prefs: JSON.stringify({ theme: 'sea', mode: 'dark', size: 'lg', bg: 'stars' }) })).body.ok, true);
  assert.equal((await post(env, '/prefs', { t: B.slice(0, 25) + 'q', prefs: '{}' })).body.error, 'unknown_token');
  assert.equal(checkPrefs('x'.repeat(1100)), null);
  const pulled = (await call(env, new Request('https://w.example/avatars-pull?since=2020-01-01', { headers: { 'X-Teacher-Token': TEACHER } }))).body;
  assert.deepEqual(pulled.prefs.map((x) => x.token), [TOK]);
});

test('F22.4 own picture: <= 60 KB JPEG/PNG, one a day, pending until the teacher decides, then gone from KV', async () => {
  const env = mkEnv(); await allow(env, ALL);
  assert.equal((await post(env, '/avatar', { t: TOK, image: Buffer.from('GIF89a....').toString('base64') })).body.error, 'bad_image');
  assert.equal((await post(env, '/avatar', { t: TOK, image: Buffer.concat([Buffer.from([0xff, 0xd8]), Buffer.alloc(70000)]).toString('base64') })).body.error, 'bad_image');
  assert.equal((await post(env, '/avatar', { t: TOK, image: JPEG })).body.status, 'pending');
  assert.equal((await post(env, '/avatar', { t: TOK, image: JPEG })).body.error, 'rate_limited');   // 1 per day per token
  assert.equal((await studentGet(env, `/prefs?t=${TOK}`)).body.avatar, 'pending');
  const pulled = (await call(env, new Request('https://w.example/avatars-pull', { headers: { 'X-Teacher-Token': TEACHER } }))).body;
  assert.equal(pulled.avatars.length, 1); assert.equal(pulled.avatars[0].image, JPEG);
  assert.equal((await call(env, new Request('https://w.example/avatars-pull', { headers: { 'X-Teacher-Token': TOK } }))).status, 401);
  assert.equal((await tpost(env, '/avatar-decide', { token: TOK, ok: true, ts: pulled.avatars[0].ts })).body.status, 'approved');
  assert.equal(env.SUBMISSIONS.m.has(`avatar:${TOK}`), false);
  assert.equal((await studentGet(env, `/prefs?t=${TOK}`)).body.avatar, 'approved');
  assert.equal((await call(env, new Request('https://w.example/avatars-pull', { headers: { 'X-Teacher-Token': TEACHER } }))).body.avatars.length, 0);
});
