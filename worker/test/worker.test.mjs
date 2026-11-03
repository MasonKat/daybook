import { test } from 'node:test';
import assert from 'node:assert/strict';
import { seoulDay } from '../src/index.js';
import { ALL, ORIGIN, OTHER, TEACHER, TOK, FakeR2, allow, call, mkEnv, studentGet, submitReq, teacherGet } from './helpers.mjs';

const ready = async (extra) => { const e = mkEnv(extra); await allow(e, ALL); return e; };
const row = (e, day, t = TOK) => e.DB.db.prepare('SELECT * FROM subs WHERE token = ? AND day = ?').get(t, day);

test('token format is validated', async () => {
  const env = await ready();
  for (const t of ['', 'short', 'ABCDEFGHIJKLMNOPQRSTUVWXY1', 'abcdefghijklmnopqrstuvwxy0', TOK + 'a', null]) {
    const r = await call(env, submitReq({ t }));
    assert.equal(r.status, 400, `token ${t}`);
    assert.equal(r.body.error, 'bad_token');
  }
  assert.equal((await call(env, submitReq({ t: TOK.toUpperCase() }))).status, 200, 'uppercase is normalised');
});

test('H3: only published tokens may upload; nothing is written for the others', async () => {
  const env = mkEnv();
  const r0 = await call(env, submitReq());
  assert.deepEqual([r0.status, r0.body.ok, r0.body.error], [200, false, 'not_ready']);    // no token map yet
  await allow(env, { [OTHER]: 'c1' });
  const r1 = await call(env, submitReq());                                               // a well-formed but unknown token
  assert.deepEqual([r1.status, r1.body.ok, r1.body.error], [200, false, 'unknown_token']);
  assert.equal(env.SUBMISSIONS.ops.put, 0);
  assert.equal(env.DB.db.prepare('SELECT COUNT(*) AS n FROM subs').get().n, 0);
  assert.equal(env.DB.db.prepare("SELECT COUNT(*) AS n FROM counters WHERE key LIKE 'rl:%'").get().n, 0);
});

test('photo type and size are validated', async () => {
  const env = await ready();
  assert.equal((await call(env, submitReq({ type: 'application/pdf' }))).status, 415);
  assert.equal((await call(env, submitReq({ bytes: 8 * 1024 * 1024 + 1 }))).status, 413);
  assert.equal((await call(env, submitReq({ bytes: null }))).status, 400);
  assert.equal((await call(env, submitReq({ type: 'image/heic' }))).status, 200);
});

test('first upload stores the record (D1) + the photo (KV, 7-day TTL) and answers with CORS', async () => {
  const env = await ready();
  const r = await call(env, submitReq());
  assert.equal(r.status, 200);
  assert.equal(r.body.ok, true);
  assert.equal(r.body.already, false);
  assert.equal(r.body.day, seoulDay(Date.now()));
  assert.deepEqual(r.body.days, [r.body.day]);
  assert.equal(r.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  const rec = row(env, r.body.day);
  assert.equal(rec.set_id, 'Adaptive_Screeners_3S_June19_G11');
  assert.equal(rec.key, `photo:${r.body.day}:${TOK}`);
  const photo = env.SUBMISSIONS.m.get(`photo:${r.body.day}:${TOK}`);
  assert.equal(photo.ttl, 7 * 86400);
  assert.equal(photo.v.byteLength, 1000);
});

test('second upload the same day replaces the photo and keeps the first ts', async () => {
  const env = await ready();
  const a = await call(env, submitReq({ bytes: 1000 }));
  await new Promise((res) => setTimeout(res, 5));
  const b = await call(env, submitReq({ bytes: 2000 }));
  assert.equal(b.body.already, true);
  assert.equal(b.body.ts, a.body.ts);
  const rec = row(env, a.body.day);
  assert.equal(rec.n, 2);
  assert.ok(rec.updated > rec.ts);
  assert.equal(env.SUBMISSIONS.m.get(`photo:${a.body.day}:${TOK}`).v.byteLength, 2000);
});

test('R2 store: replacement with another extension deletes the old object', async () => {
  const env = await ready({ PHOTO_STORE: 'r2', PHOTOS: new FakeR2() });
  const a = await call(env, submitReq({ type: 'image/png' }));
  const b = await call(env, submitReq({ type: 'image/jpeg', bytes: 3000 }));
  assert.equal(b.body.already, true);
  assert.deepEqual([...env.PHOTOS.m.keys()], [`${a.body.day}/${TOK}.jpg`]);
});

test('rate limit: 10 per token per hour, the 11th is refused (atomic counter)', async () => {
  const env = await ready();
  for (let i = 0; i < 10; i++) assert.equal((await call(env, submitReq())).status, 200, `upload ${i + 1}`);
  const r = await call(env, submitReq());
  assert.equal(r.status, 429);
  assert.equal(r.body.error, 'rate_limited');
  assert.equal((await call(env, submitReq({ t: OTHER }))).status, 200, 'other tokens unaffected');
});

test('CORS: another origin is refused, preflight answers only the Pages origin', async () => {
  const env = await ready();
  assert.equal((await call(env, submitReq({ origin: 'https://evil.example' }))).status, 403);
  const worker = (await import('../src/index.js')).default;
  const pre = await worker.fetch(new Request('https://w.example/submit', { method: 'OPTIONS', headers: { Origin: ORIGIN } }), env);
  assert.equal(pre.status, 204);
  assert.equal(pre.headers.get('Access-Control-Allow-Origin'), ORIGIN);
});

test('/submissions and /photo need the teacher token; since filters', async () => {
  const env = await ready();
  for (const t of ['aaaaaaaaaaaaaaaaaaaaaaaaab', 'bbbbbbbbbbbbbbbbbbbbbbbbbc', 'cccccccccccccccccccccccccd']) await call(env, submitReq({ t }));
  assert.equal((await call(env, new Request('https://w.example/submissions'))).status, 401);
  assert.equal((await call(env, new Request('https://w.example/submissions', { headers: { 'X-Teacher-Token': 'x'.repeat(TEACHER.length) } }))).status, 401);
  const ok = await teacherGet(env, '/submissions');
  assert.equal(ok.status, 200);
  assert.equal(ok.body.items.length, 3);
  assert.deepEqual(Object.keys(ok.body.items[0]).sort(), ['chip', 'day', 'key', 'line', 'n', 'set', 'token', 'ts', 'updated']);
  const later = await teacherGet(env, `/submissions?since=${encodeURIComponent(ok.body.now)}`);
  assert.equal(later.body.items.length, 0);
  const key = ok.body.items[0].key;
  const photo = await teacherGet(env, `/photo?key=${encodeURIComponent(key)}`);
  assert.equal(photo.status, 200);
  assert.equal(photo.body.byteLength, 1000);
  assert.equal((await call(env, new Request(`https://w.example/photo?key=${encodeURIComponent(key)}`))).status, 401);
  assert.equal((await teacherGet(env, '/photo?key=../etc')).status, 400);
});

test('/status returns the days this token submitted', async () => {
  const env = await ready();
  const a = await call(env, submitReq());
  const s = await studentGet(env, `/status?t=${TOK}`);
  assert.equal(s.status, 200);
  assert.deepEqual(s.body.days, [a.body.day]);
  assert.equal((await studentGet(env, '/status?t=nope')).status, 400);
});

test('seoulDay rolls over at 15:00 UTC', () => {
  assert.equal(seoulDay(Date.parse('2026-11-04T14:59:59Z')), '2026-11-04');
  assert.equal(seoulDay(Date.parse('2026-11-04T15:00:00Z')), '2026-11-05');
});

test('F3: one-line review (<= 40 chars) and a known chip ride along; a replacement without them keeps them', async () => {
  const env = await ready();
  const a = await call(env, submitReq({ line: '  분수 통분이 이제 이해됐어요!\n'.padEnd(60, '!'), chip: 'ok' }));
  const rec = row(env, a.body.day);
  assert.equal(rec.chip, 'ok'); assert.equal([...rec.line].length, 40); assert.ok(!rec.line.includes('\n'));
  await call(env, submitReq());                                   // no line/chip fields: keep the earlier ones
  const rec2 = row(env, a.body.day);
  assert.equal(rec2.chip, 'ok'); assert.equal(rec2.line, rec.line);
  await call(env, submitReq({ chip: 'hacker' }));
  assert.equal(row(env, a.body.day).chip, '');
});

test('H2: storage trouble is an answer (200, ok:false), never a 500 for a student', async () => {
  const env = await ready();
  env.DB.failNext = 1;
  const r = await call(env, submitReq());
  assert.equal(r.status, 200); assert.deepEqual(r.body, { ok: false, error: 'storage' });
  assert.equal(r.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  assert.equal((await call(env, submitReq())).body.ok, true);     // and the next request works again
});

test('KV is used for photos only: no list operation anywhere', async () => {
  const env = await ready();
  await call(env, submitReq());
  await teacherGet(env, '/submissions');
  await studentGet(env, `/status?t=${TOK}`);
  await studentGet(env, `/moments?t=${TOK}`);
  assert.equal(env.SUBMISSIONS.ops.list, 0);
});
