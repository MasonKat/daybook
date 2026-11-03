import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { seoulDay } from '../src/index.js';
import { FakeKV } from './fake_kv.mjs';

const ORIGIN = 'https://daybook.pages.dev', TEACHER = 'teacher-secret-0123456789';
const A = 'aaaaaaaaaaaaaaaaaaaaaaaaaa', A2 = 'dddddddddddddddddddddddddd', B = 'bbbbbbbbbbbbbbbbbbbbbbbbbb';
const DAY = seoulDay(Date.now());
const JPEG = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(500, 1)]).toString('base64');
const env = () => ({ SUBMISSIONS: new FakeKV(), ALLOWED_ORIGIN: ORIGIN, TEACHER_TOKEN: TEACHER });
const call = async (e, req) => { const r = await worker.fetch(req, e); return { status: r.status, body: r.headers.get('content-type')?.includes('json') ? await r.json() : await r.arrayBuffer() }; };
const teacherPut = (e, path, body, auth = TEACHER) => call(e, new Request('https://w.example' + path, {
  method: 'PUT', headers: { 'X-Teacher-Token': auth, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));

async function setup() {
  const e = env();
  await teacherPut(e, '/session', { class: 'c1', date: DAY, code: '1234', open_from: new Date(Date.now() - 1e6).toISOString(),
    open_until: new Date(Date.now() + 1e6).toISOString(), tokmap: { [A]: 'c1', [A2]: 'c1', [B]: 'c2' } });
  await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 1, text: '칠판 위의 멋진 풀이', image: JPEG });
  await teacherPut(e, '/moment-put', { class: 'c2', date: DAY, n: 1, text: '다른 반 장면', image: JPEG });
  return e;
}
const get = (e, path) => call(e, new Request('https://w.example' + path, { headers: { Origin: ORIGIN } }));
function reactReq(e, t, k, r) {
  const fd = new FormData(); fd.append('t', t); fd.append('k', k); fd.append('r', r);
  return call(e, new Request('https://w.example/react', { method: 'POST', body: fd, headers: { Origin: ORIGIN } }));
}

test('PUT /moment-put: teacher only, JPEG only, <= 200 KB, n in 1..3', async () => {
  const e = env();
  assert.equal((await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 1, image: JPEG }, 'x'.repeat(TEACHER.length))).status, 401);
  assert.equal((await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 4, image: JPEG })).status, 400);
  assert.equal((await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 1, image: Buffer.from('GIF89a').toString('base64') })).status, 400);
  const big = Buffer.concat([Buffer.from([0xff, 0xd8]), Buffer.alloc(201 * 1024)]).toString('base64');
  assert.equal((await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 1, image: big })).status, 400);
  const ok = await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 1, text: 'x'.repeat(80), image: JPEG });
  assert.equal(ok.status, 200);
  assert.equal(e.SUBMISSIONS.m.get(`moment:c1:${DAY}:1`).meta.text.length, 60);
  assert.equal(e.SUBMISSIONS.m.get(`moment:c1:${DAY}:1`).ttl, 14 * 86400);
});

test('a token sees only its own class\'s moments and images', async () => {
  const e = await setup();
  const a = await get(e, `/moments?t=${A}`);
  assert.deepEqual(a.body.items.map((m) => m.k), [`c1:${DAY}:1`]);
  assert.equal(a.body.items[0].text, '칠판 위의 멋진 풀이');
  const b = await get(e, `/moments?t=${B}`);
  assert.deepEqual(b.body.items.map((m) => m.k), [`c2:${DAY}:1`]);
  assert.equal((await get(e, `/moment?t=${A}&k=c1:${DAY}:1`)).status, 200);
  assert.equal((await get(e, `/moment?t=${A}&k=c2:${DAY}:1`)).status, 404);           // other class: not found
  const unknown = await get(e, `/moments?t=${'z'.repeat(26)}`);                      // unknown token: an empty list
  assert.equal(unknown.status, 200); assert.deepEqual(unknown.body.items, []);
});

test('reactions: anonymous counters, one per token per moment, own class only', async () => {
  const e = await setup();
  const k = `c1:${DAY}:1`;
  let r = await reactReq(e, A, k, 'fire');
  assert.deepEqual(r.body.counts, { fire: 1, clap: 0, mind: 0 }); assert.equal(r.body.mine, 'fire');
  r = await reactReq(e, A, k, 'clap');                                                  // a second tap does not count
  assert.deepEqual(r.body.counts, { fire: 1, clap: 0, mind: 0 }); assert.equal(r.body.already, true);
  r = await reactReq(e, A2, k, 'mind');
  assert.deepEqual(r.body.counts, { fire: 1, clap: 0, mind: 1 });
  assert.equal((await reactReq(e, B, k, 'fire')).status, 404);                          // another class cannot react
  assert.equal((await reactReq(e, A, k, 'heart')).status, 400);
  const list = await get(e, `/moments?t=${A}`);
  assert.equal(list.body.items[0].mine, 'fire'); assert.deepEqual(list.body.items[0].counts, { fire: 1, clap: 0, mind: 1 });
  assert.ok(!JSON.stringify(list.body).includes(A2));                                   // no tokens, no names
});
