import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { seoulDay } from '../src/index.js';
import { FakeKV } from './fake_kv.mjs';

const ORIGIN = 'https://daybook.pages.dev', TEACHER = 'teacher-secret-0123456789';
const A = 'aaaaaaaaaaaaaaaaaaaaaaaaaa', B = 'bbbbbbbbbbbbbbbbbbbbbbbbbb', C = 'cccccccccccccccccccccccccc';
const env = () => ({ SUBMISSIONS: new FakeKV(), ALLOWED_ORIGIN: ORIGIN, TEACHER_TOKEN: TEACHER, RECORD_TTL_DAYS: '14' });
const call = async (e, req) => { const r = await worker.fetch(req, e); return { status: r.status, body: await r.json().catch(() => null) }; };
const iso = (ms) => new Date(Date.now() + ms).toISOString();

function putSession(e, { cls = 'c1', code = '4821', from = -30 * 60e3, until = 60 * 60e3, tokmap = { [A]: 'c1', [B]: 'c2' }, auth = TEACHER } = {}) {
  return call(e, new Request('https://w.example/session', {
    method: 'PUT', headers: { 'X-Teacher-Token': auth, 'Content-Type': 'application/json' },
    body: JSON.stringify({ class: cls, date: seoulDay(Date.now()), code, open_from: iso(from), open_until: iso(until), start: '18:00', tokmap }),
  }));
}
function checkin(e, t, code, origin = ORIGIN) {
  const fd = new FormData(); fd.append('t', t); fd.append('code', code);
  return call(e, new Request('https://w.example/checkin', { method: 'POST', body: fd, headers: { Origin: origin } }));
}

test('PUT /session needs the teacher token and a valid body', async () => {
  const e = env();
  assert.equal((await putSession(e, { auth: 'x'.repeat(TEACHER.length) })).status, 401);
  assert.equal((await putSession(e, { code: '12a4' })).status, 400);
  assert.equal((await putSession(e)).status, 200);
  assert.deepEqual(JSON.parse(e.SUBMISSIONS.m.get('tokmap').v), { [A]: 'c1', [B]: 'c2' });
});

test('right code inside the window checks in; a second check-in is idempotent', async () => {
  const e = env();
  await putSession(e);
  const a = await checkin(e, A, '4821');
  assert.equal(a.status, 200); assert.equal(a.body.already, false);
  const b = await checkin(e, A, '0000');           // even a wrong code after success: idempotent, keeps the first ts
  assert.equal(b.status, 200); assert.equal(b.body.already, true); assert.equal(b.body.ts, a.body.ts);
  const st = await call(e, new Request(`https://w.example/status?t=${A}`, { headers: { Origin: ORIGIN } }));
  assert.equal(st.body.checkin_ts, a.body.ts); assert.equal(st.body.class_today, true); assert.equal(st.body.start, '18:00');
});

test('a wrong code is rejected', async () => {
  const e = env();
  await putSession(e);
  const r = await checkin(e, A, '1111');
  assert.equal(r.body.ok, false); assert.equal(r.body.error, 'bad_code'); assert.equal(r.body.left, 4);
  assert.equal(e.SUBMISSIONS.m.has(`checkin:${seoulDay(Date.now())}:${A}`), false);
});

test('a closed window is rejected (before and after)', async () => {
  const e = env();
  await putSession(e, { from: 10 * 60e3, until: 70 * 60e3 });
  assert.equal((await checkin(e, A, '4821')).body.error, 'closed');
  await putSession(e, { from: -120 * 60e3, until: -60e3 });
  assert.equal((await checkin(e, A, '4821')).body.error, 'closed');
});

test('a token whose class has no session today is rejected; unknown tokens too', async () => {
  const e = env();
  await putSession(e);                               // session for c1 only; B is in c2
  assert.equal((await checkin(e, B, '4821')).body.error, 'no_class_today');
  assert.equal((await checkin(e, C, '4821')).body.error, 'no_class_today');
  assert.equal((await checkin(e, 'short', '4821')).status, 400);
});

test('rate limit: 5 wrong codes per token per day, then even the right code waits', async () => {
  const e = env();
  await putSession(e);
  for (let i = 0; i < 5; i++) assert.equal((await checkin(e, A, '0000')).body.error, 'bad_code');
  const r = await checkin(e, A, '4821');
  assert.equal(r.body.ok, false); assert.equal(r.body.error, 'rate_limited');
});

test('CORS: /checkin from another origin is refused', async () => {
  const e = env();
  await putSession(e);
  assert.equal((await checkin(e, A, '4821', 'https://evil.example')).status, 403);
});

test("GET /checkins lists today's check-ins for the teacher only", async () => {
  const e = env();
  await putSession(e);
  await checkin(e, A, '4821');
  assert.equal((await call(e, new Request('https://w.example/checkins'))).status, 401);
  const r = await call(e, new Request('https://w.example/checkins', { headers: { 'X-Teacher-Token': TEACHER } }));
  assert.equal(r.body.items.length, 1); assert.equal(r.body.items[0].token, A); assert.equal(r.body.items[0].class, 'c1');
});
