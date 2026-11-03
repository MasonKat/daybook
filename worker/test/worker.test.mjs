import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { seoulDay } from '../src/index.js';
import { FakeKV, FakeR2 } from './fake_kv.mjs';

const ORIGIN = 'https://daybook.pages.dev';
const TOK = 'abcdefghijklmnopqrstuvwxyz'; // 26 chars, base32 alphabet
const TEACHER = 'teacher-secret-0123456789';

function mkEnv(extra = {}) {
  return { SUBMISSIONS: new FakeKV(), ALLOWED_ORIGIN: ORIGIN, TEACHER_TOKEN: TEACHER, PHOTO_STORE: 'kv', PHOTO_TTL_DAYS: '7', RECORD_TTL_DAYS: '14', RATE_PER_HOUR: '10', ...extra };
}
function submitReq({ t = TOK, set = 'Adaptive_Screeners_3S_June19_G11', bytes = 1000, type = 'image/jpeg', origin = ORIGIN } = {}) {
  const fd = new FormData();
  if (t !== null) fd.append('t', t);
  if (set) fd.append('set', set);
  if (bytes !== null) fd.append('photo', new File([new Uint8Array(bytes).fill(7)], 'p.jpg', { type }));
  const headers = origin ? { Origin: origin } : {};
  return new Request('https://w.example/submit', { method: 'POST', body: fd, headers });
}
const call = async (env, req) => { const r = await worker.fetch(req, env); return { status: r.status, body: await r.json().catch(() => null), headers: r.headers }; };

test('token format is validated', async () => {
  const env = mkEnv();
  for (const t of ['', 'short', 'ABCDEFGHIJKLMNOPQRSTUVWXY1', 'abcdefghijklmnopqrstuvwxy0', TOK + 'a', null]) {
    const r = await call(env, submitReq({ t }));
    assert.equal(r.status, 400, `token ${t}`);
    assert.equal(r.body.error, 'bad_token');
  }
  assert.equal((await call(env, submitReq({ t: TOK.toUpperCase() }))).status, 200, 'uppercase is normalised');
});

test('photo type and size are validated', async () => {
  const env = mkEnv();
  assert.equal((await call(env, submitReq({ type: 'application/pdf' }))).status, 415);
  assert.equal((await call(env, submitReq({ bytes: 8 * 1024 * 1024 + 1 }))).status, 413);
  assert.equal((await call(env, submitReq({ bytes: null }))).status, 400);
  assert.equal((await call(env, submitReq({ type: 'image/heic' }))).status, 200);
});

test('first upload stores record + photo with TTLs and CORS header', async () => {
  const env = mkEnv();
  const r = await call(env, submitReq());
  assert.equal(r.status, 200);
  assert.equal(r.body.ok, true);
  assert.equal(r.body.already, false);
  assert.equal(r.body.day, seoulDay(Date.now()));
  assert.deepEqual(r.body.days, [r.body.day]);
  assert.equal(r.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  const rec = env.SUBMISSIONS.m.get(`sub:${r.body.day}:${TOK}`);
  assert.equal(rec.ttl, 14 * 86400);
  assert.equal(rec.meta.set, 'Adaptive_Screeners_3S_June19_G11');
  const photo = env.SUBMISSIONS.m.get(`photo:${r.body.day}:${TOK}`);
  assert.equal(photo.ttl, 7 * 86400);
  assert.equal(photo.v.byteLength, 1000);
});

test('second upload the same day replaces the photo and keeps the first ts', async () => {
  const env = mkEnv();
  const a = await call(env, submitReq({ bytes: 1000 }));
  await new Promise((res) => setTimeout(res, 5));
  const b = await call(env, submitReq({ bytes: 2000 }));
  assert.equal(b.body.already, true);
  assert.equal(b.body.ts, a.body.ts);
  const rec = JSON.parse(env.SUBMISSIONS.m.get(`sub:${a.body.day}:${TOK}`).v);
  assert.equal(rec.n, 2);
  assert.ok(rec.updated > rec.ts);
  assert.equal(env.SUBMISSIONS.m.get(`photo:${a.body.day}:${TOK}`).v.byteLength, 2000);
});

test('R2 store: replacement with another extension deletes the old object', async () => {
  const env = mkEnv({ PHOTO_STORE: 'r2', PHOTOS: new FakeR2() });
  const a = await call(env, submitReq({ type: 'image/png' }));
  const b = await call(env, submitReq({ type: 'image/jpeg', bytes: 3000 }));
  assert.equal(b.body.already, true);
  const keys = [...env.PHOTOS.m.keys()];
  assert.deepEqual(keys, [`${a.body.day}/${TOK}.jpg`]);
});

test('rate limit: 10 per token per hour, the 11th is refused', async () => {
  const env = mkEnv();
  for (let i = 0; i < 10; i++) assert.equal((await call(env, submitReq())).status, 200, `upload ${i + 1}`);
  const r = await call(env, submitReq());
  assert.equal(r.status, 429);
  assert.equal(r.body.error, 'rate_limited');
  const other = 'zyxwvutsrqponmlkjihgfedcba';
  assert.equal((await call(env, submitReq({ t: other }))).status, 200, 'other tokens unaffected');
});

test('CORS: another origin is refused, preflight answers only the Pages origin', async () => {
  const env = mkEnv();
  assert.equal((await call(env, submitReq({ origin: 'https://evil.example' }))).status, 403);
  const pre = await worker.fetch(new Request('https://w.example/submit', { method: 'OPTIONS', headers: { Origin: ORIGIN } }), env);
  assert.equal(pre.status, 204);
  assert.equal(pre.headers.get('Access-Control-Allow-Origin'), ORIGIN);
});

test('/submissions and /photo need the teacher token; since filters', async () => {
  const env = mkEnv();
  const toks = ['aaaaaaaaaaaaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbbbbbbbbbbbb', 'cccccccccccccccccccccccccc'];
  for (const t of toks) await call(env, submitReq({ t }));
  const noAuth = await call(env, new Request('https://w.example/submissions'));
  assert.equal(noAuth.status, 401);
  const bad = await call(env, new Request('https://w.example/submissions', { headers: { 'X-Teacher-Token': 'x'.repeat(TEACHER.length) } }));
  assert.equal(bad.status, 401);
  const ok = await call(env, new Request('https://w.example/submissions', { headers: { 'X-Teacher-Token': TEACHER } }));
  assert.equal(ok.status, 200);
  assert.equal(ok.body.items.length, 3);
  assert.deepEqual(Object.keys(ok.body.items[0]).sort(), ['day', 'key', 'n', 'set', 'token', 'ts', 'updated']);
  const later = await call(env, new Request(`https://w.example/submissions?since=${encodeURIComponent(ok.body.now)}`, { headers: { 'X-Teacher-Token': TEACHER } }));
  assert.equal(later.body.items.length, 0);
  const key = ok.body.items[0].key;
  const photo = await worker.fetch(new Request(`https://w.example/photo?key=${encodeURIComponent(key)}`, { headers: { 'X-Teacher-Token': TEACHER } }), env);
  assert.equal(photo.status, 200);
  assert.equal((await photo.arrayBuffer()).byteLength, 1000);
  assert.equal((await call(env, new Request(`https://w.example/photo?key=${encodeURIComponent(key)}`))).status, 401);
  assert.equal((await call(env, new Request('https://w.example/photo?key=../etc', { headers: { 'X-Teacher-Token': TEACHER } }))).status, 400);
});

test('/status returns the days this token submitted', async () => {
  const env = mkEnv();
  const a = await call(env, submitReq());
  const s = await call(env, new Request(`https://w.example/status?t=${TOK}`, { headers: { Origin: ORIGIN } }));
  assert.equal(s.status, 200);
  assert.deepEqual(s.body.days, [a.body.day]);
  assert.equal((await call(env, new Request('https://w.example/status?t=nope'))).status, 400);
});

test('seoulDay rolls over at 15:00 UTC', () => {
  assert.equal(seoulDay(Date.parse('2026-11-04T14:59:59Z')), '2026-11-04');
  assert.equal(seoulDay(Date.parse('2026-11-04T15:00:00Z')), '2026-11-05');
});
