import { test } from 'node:test';
import assert from 'node:assert/strict';
import { seoulDay } from '../src/index.js';
import { A, A2, B, ORIGIN, TEACHER, call, mkEnv, studentGet, teacherPut } from './helpers.mjs';

const DAY = seoulDay(Date.now());
const JPEG = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(500, 1)]).toString('base64');
const tok = (i) => String.fromCharCode(97 + (i % 26)).repeat(25) + 'abcdefghijklmnopqrstuvwxyz'[Math.floor(i / 26)];

async function setup(extraTokens = {}) {
  const e = mkEnv();
  await teacherPut(e, '/tokmap', { tokmap: { [A]: 'c1', [A2]: 'c1', [B]: 'c2', ...extraTokens } });
  await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 1, text: '칠판 위의 멋진 풀이', image: JPEG });
  await teacherPut(e, '/moment-put', { class: 'c2', date: DAY, n: 1, text: '다른 반 장면', image: JPEG });
  return e;
}
function reactReq(e, t, k, r) {
  const fd = new FormData(); fd.append('t', t); fd.append('k', k); fd.append('r', r);
  return call(e, new Request('https://w.example/react', { method: 'POST', body: fd, headers: { Origin: ORIGIN } }));
}

test('PUT /moment-put: teacher only, JPEG only, <= 200 KB, n in 1..3', async () => {
  const e = mkEnv();
  assert.equal((await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 1, image: JPEG }, 'x'.repeat(TEACHER.length))).status, 401);
  assert.equal((await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 4, image: JPEG })).status, 400);
  assert.equal((await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 1, image: Buffer.from('GIF89a').toString('base64') })).status, 400);
  const big = Buffer.concat([Buffer.from([0xff, 0xd8]), Buffer.alloc(201 * 1024)]).toString('base64');
  assert.equal((await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 1, image: big })).status, 400);
  assert.equal((await teacherPut(e, '/moment-put', { class: 'c1', date: DAY, n: 1, text: 'x'.repeat(80), image: JPEG })).status, 200);
  assert.equal(e.DB.db.prepare('SELECT text FROM moments').get().text.length, 60);
  assert.equal(e.SUBMISSIONS.m.get(`moment:c1:${DAY}:1`).ttl, 14 * 86400);
});

test("a token sees only its own class's moments and images", async () => {
  const e = await setup();
  const a = await studentGet(e, `/moments?t=${A}`);
  assert.deepEqual(a.body.items.map((m) => m.k), [`c1:${DAY}:1`]);
  assert.equal(a.body.items[0].text, '칠판 위의 멋진 풀이');
  assert.deepEqual((await studentGet(e, `/moments?t=${B}`)).body.items.map((m) => m.k), [`c2:${DAY}:1`]);
  assert.equal((await studentGet(e, `/moment?t=${A}&k=c1:${DAY}:1`)).status, 200);
  assert.equal((await studentGet(e, `/moment?t=${A}&k=c2:${DAY}:1`)).status, 404);
  const unknown = await studentGet(e, `/moments?t=${'z'.repeat(26)}`);
  assert.equal(unknown.status, 200); assert.deepEqual(unknown.body.items, []);
});

test('reactions: anonymous rows, one per token per moment, own class only', async () => {
  const e = await setup();
  const k = `c1:${DAY}:1`;
  let r = await reactReq(e, A, k, 'fire');
  assert.deepEqual(r.body.counts, { fire: 1, clap: 0, mind: 0 }); assert.equal(r.body.mine, 'fire');
  r = await reactReq(e, A, k, 'clap');
  assert.deepEqual(r.body.counts, { fire: 1, clap: 0, mind: 0 }); assert.equal(r.body.already, true);
  r = await reactReq(e, A2, k, 'mind');
  assert.deepEqual(r.body.counts, { fire: 1, clap: 0, mind: 1 });
  assert.equal((await reactReq(e, B, k, 'fire')).status, 404);
  assert.equal((await reactReq(e, A, k, 'heart')).status, 400);
  const list = await studentGet(e, `/moments?t=${A}`);
  assert.equal(list.body.items[0].mine, 'fire'); assert.deepEqual(list.body.items[0].counts, { fire: 1, clap: 0, mind: 1 });
  assert.ok(!JSON.stringify(list.body).includes(A2));
});

test('H2: 20 students tapping 🔥 at the same moment all count (no shared counter key)', async () => {
  const many = Object.fromEntries(Array.from({ length: 20 }, (_, i) => [tok(i), 'c1']));
  const e = await setup(many);
  const k = `c1:${DAY}:1`;
  const rs = await Promise.all(Object.keys(many).map((t) => reactReq(e, t, k, 'fire')));
  assert.ok(rs.every((r) => r.status === 200 && r.body.ok));
  assert.equal((await studentGet(e, `/moments?t=${A}`)).body.items[0].counts.fire, 20);
  assert.equal(e.SUBMISSIONS.ops.put, 2);                  // KV holds the two moment images only
});
