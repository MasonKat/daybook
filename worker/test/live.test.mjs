import { test } from 'node:test';
import assert from 'node:assert/strict';
import { A, A2, B, ORIGIN, TEACHER, call, mkEnv, teacherPut } from './helpers.mjs';

const QS = [{ kind: 'choice', answer: '3', text: '이 문장은 Worker에 가면 안 된다' }, { kind: 'num', answer: '12' }];
const tpost = (e, path, body, auth = TEACHER) => call(e, new Request('https://w.example' + path, {
  method: 'POST', headers: { 'X-Teacher-Token': auth, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
const tget = (e, path) => call(e, new Request('https://w.example' + path, { headers: { 'X-Teacher-Token': TEACHER } }));
function spost(e, path, fields) {
  const fd = new FormData(); for (const [k, v] of Object.entries(fields)) fd.append(k, String(v));
  return call(e, new Request('https://w.example' + path, { method: 'POST', body: fd, headers: { Origin: ORIGIN } }));
}
const sget = (e, t, room) => call(e, new Request(`https://w.example/live/state?t=${t}&room=${room}`, { headers: { Origin: ORIGIN } }));

async function openRoom() {
  const e = mkEnv();
  await teacherPut(e, '/tokmap', { tokmap: { [A]: 'c1', [A2]: 'c1', [B]: 'c2' } });
  const r = await tpost(e, '/live/open', { class: 'c1', questions: QS });
  return { e, room: r.body.room, open: r.body };
}

test('open: teacher only, valid questions only; the room code is 4 digits; no question text is stored', async () => {
  const e = mkEnv();
  assert.equal((await tpost(e, '/live/open', { class: 'c1', questions: QS }, 'x'.repeat(TEACHER.length))).status, 401);
  assert.equal((await tpost(e, '/live/open', { class: 'c1', questions: [{ kind: 'choice', answer: '7' }] })).status, 400);
  const { e: e2, room, open } = await openRoom();
  assert.match(room, /^\d{4}$/); assert.equal(open.size, 2);
  const stored = JSON.stringify([...e2.LIVE.objs.values()].map((o) => o.ctx.storage.sql.db.prepare('SELECT v FROM meta').all()));
  assert.ok(!stored.includes('Worker에') && !stored.includes('text'));
});

test('join: only a student of the room\'s class; state before the first question', async () => {
  const { e, room } = await openRoom();
  const j = await spost(e, '/live/join', { t: A, room });
  assert.equal(j.body.open, true); assert.equal(j.body.joined, 1); assert.equal(j.body.mine.joined, true); assert.equal(j.body.q, 0);
  assert.equal((await spost(e, '/live/join', { t: B, room })).body.error, 'no_room');       // another class
  assert.equal((await spost(e, '/live/join', { t: A, room: '0000' === room ? '0001' : '0000' })).body.error, 'no_room');
});

test('answer once per question; correctness only after reveal; the class bar counts', async () => {
  const { e, room } = await openRoom();
  await spost(e, '/live/join', { t: A, room }); await spost(e, '/live/join', { t: A2, room });
  assert.equal((await spost(e, '/live/answer', { t: A, room, q: 1, a: '3' })).body.error, 'closed');   // not started
  await tpost(e, '/live/next', { room, q: 1 });
  let s = await spost(e, '/live/answer', { t: A, room, q: 1, a: '3' });
  assert.equal(s.body.mine.answered, true); assert.equal(s.body.mine.correct, null); assert.equal(s.body.correct, null);
  await spost(e, '/live/answer', { t: A, room, q: 1, a: '1' });                                 // a second answer is ignored
  await spost(e, '/live/answer', { t: A2, room, q: 1, a: '2' });
  const t = await tget(e, `/live/state?room=${room}`);
  assert.equal(t.body.answered, 2); assert.equal(t.body.joined, 2); assert.equal(t.body.correct, null);
  await tpost(e, '/live/reveal', { room });
  assert.equal((await sget(e, A, room)).body.mine.correct, true);
  assert.equal((await sget(e, A2, room)).body.mine.correct, false);
  assert.equal((await tget(e, `/live/state?room=${room}`)).body.correct, 1);
  assert.equal((await spost(e, '/live/answer', { t: A2, room, q: 1, a: '3' })).body.error, 'closed');   // after reveal
  await tpost(e, '/live/next', { room });
  await spost(e, '/live/answer', { t: A2, room, q: 2, a: '+12' });
  await tpost(e, '/live/reveal', { room });
  assert.equal((await sget(e, A2, room)).body.mine.correct, true);
  assert.ok(!JSON.stringify((await sget(e, A, room)).body).includes(A2));                     // no one else's token
});

test('close keeps only per-student counts (D1) and wipes the answers; results for pull', async () => {
  const { e, room } = await openRoom();
  await spost(e, '/live/join', { t: A, room }); await spost(e, '/live/join', { t: A2, room });
  await tpost(e, '/live/next', { room, q: 1 });
  await spost(e, '/live/answer', { t: A, room, q: 1, a: '3' });
  const c = await tpost(e, '/live/close', { room });
  assert.equal(c.body.results, 2);
  const res = await tget(e, '/live/results?since=2020-01-01T00:00:00Z');
  const byTok = Object.fromEntries(res.body.items.map((r) => [r.token, r]));
  assert.deepEqual([byTok[A].answered, byTok[A].correct, byTok[A2].answered, byTok[A2].correct], [1, 1, 0, 0]);
  const obj = [...e.LIVE.objs.values()][0];
  assert.deepEqual(obj.ctx.storage.sql.db.prepare('SELECT * FROM answers').all(), []);
  assert.equal((await spost(e, '/live/join', { t: A, room })).body.error, 'no_room');
});

test('the room closes itself 90 minutes after opening (alarm)', async () => {
  const { e, room } = await openRoom();
  const obj = [...e.LIVE.objs.values()][0];
  const mins = (obj.ctx.alarmAt - Date.now()) / 60e3;
  assert.ok(mins > 89 && mins <= 90, `alarm in ${mins} min`);
  await spost(e, '/live/join', { t: A, room });
  await e.LIVE.fireAlarm(`room:${room}`);
  assert.equal((await tget(e, '/live/results')).body.items.length, 1);
  assert.equal((await tget(e, `/live/state?room=${room}`)).body.open, false);
});
