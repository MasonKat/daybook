// F14: the weekly-test report (주간 테스트). Every validation rule, the window, another class, edits, the teacher pull.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ALL, B, ORIGIN, TOK, allow, call, mkEnv, studentGet, teacherGet, teacherPut } from './helpers.mjs';

const RID = '0123456789abcdef';
const iso = (ms) => new Date(Date.now() + ms).toISOString();
const ROUND = { rid: RID, classes: ['c1'], n: 20, open_from: iso(-3600e3), open_until: iso(2 * 86400e3), label: '11/6 주간 테스트' };

async function ready(round = ROUND) {
  const env = mkEnv();
  await allow(env, ALL);
  assert.equal((await teacherPut(env, '/rounds', { rounds: [round] })).body.n, 1);
  return env;
}
function rep(fields = {}, t = TOK) {
  const fd = new FormData();
  fd.append('t', t);
  const f = { round: RID, incorrect: '3,7', missed: '12', hardest: '7,12', reasons: '계산실수,시간부족', wishlist: '풀이 영상 더', metaphor: '마라톤', ...fields };
  for (const [k, v] of Object.entries(f)) if (v !== undefined) fd.append(k, v);
  return new Request('https://w.example/report', { method: 'POST', body: fd, headers: { Origin: ORIGIN } });
}
const stored = (env, t = TOK) => env.DB.db.prepare('SELECT * FROM reports WHERE rid = ? AND token = ?').get(RID, t);

test('F14: a valid report is stored once per round and student; an edit replaces it', async () => {
  const env = await ready();
  const a = await call(env, rep());
  assert.equal(a.status, 200); assert.equal(a.body.ok, true); assert.equal(a.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  let r = stored(env);
  assert.deepEqual([r.incorrect, r.missed, r.hardest, r.reasons, r.wishlist, r.metaphor, r.class, r.n],
    ['3,7', '12', '7,12', '계산실수,시간부족', '풀이 영상 더', '마라톤', 'c1', 1]);
  const first = r.ts;
  const b = await call(env, rep({ incorrect: '9, 2', missed: '', hardest: '', reasons: '개념탈락', wishlist: '', metaphor: '' }));
  assert.equal(b.body.ok, true);
  r = stored(env);
  assert.deepEqual([r.incorrect, r.missed, r.hardest, r.reasons, r.wishlist, r.n, r.ts], ['2,9', '', '', '개념탈락', '', 2, first]);
  assert.equal(env.DB.db.prepare('SELECT COUNT(*) AS n FROM reports').get().n, 1);
  // all correct: no reason needed
  assert.equal((await call(env, rep({ incorrect: '', missed: '', reasons: '' }))).body.ok, true);
});

test('F14: every validation rule', async () => {
  const env = await ready();
  const err = async (f, t) => (await call(env, rep(f, t))).body.error;
  assert.equal((await call(env, rep({}, 'NOTATOKEN'))).status, 400);
  assert.equal(await err({ round: 'Adaptive_Screeners_1S_Nov06_G10' }), 'bad_round');      // the raw id is never accepted
  assert.equal(await err({ round: 'ffffffffffffffff' }), 'unknown_round');
  assert.equal(await err({ incorrect: '0' }), 'bad_numbers');                                // 1..N
  assert.equal(await err({ incorrect: '21' }), 'bad_numbers');
  assert.equal(await err({ incorrect: '3,3' }), 'bad_numbers');                              // no repeats
  assert.equal(await err({ incorrect: '2.5' }), 'bad_numbers');
  assert.equal(await err({ incorrect: '3', missed: '3' }), 'bad_numbers');                   // ✕ and – exclude each other
  assert.equal(await err({ hardest: '1,2,3,4' }), 'bad_numbers');                            // at most 3
  assert.equal(await err({ reasons: '모름' }), 'bad_reason');                                 // the known set only
  assert.equal(await err({ reasons: '시간부족,시간부족' }), 'bad_reason');
  assert.equal(await err({ reasons: '' }), 'need_reason');                                   // ✕ or – needs a reason
  assert.equal(await err({ wishlist: '가'.repeat(101) }), 'too_long');
  assert.equal(await err({ metaphor: '가'.repeat(31) }), 'too_long');
  assert.equal((await call(env, rep({ wishlist: '한 줄\n두 줄\u0007', metaphor: '가'.repeat(30) }))).body.ok, true);
  assert.equal(stored(env).wishlist, '한 줄 두 줄');                                           // control characters stripped
  assert.equal((await call(env, new Request('https://w.example/report', { method: 'POST', body: new FormData(),
    headers: { Origin: 'https://evil.example' } }))).status, 403);                           // CORS: the Pages origin only
});

test('F14: another class is refused, and so is an unpublished token', async () => {
  const env = await ready();
  assert.equal((await call(env, rep({}, B))).body.error, 'not_your_round');                  // B is in c2
  assert.equal((await call(env, rep({}, 'zzzzzzzzzzzzzzzzzzzzzzzzzz'))).body.error, 'unknown_token');
  assert.equal(env.DB.db.prepare('SELECT COUNT(*) AS n FROM reports').get().n, 0);
});

test('F14: the window opens and closes', async () => {
  const later = await ready({ ...ROUND, open_from: iso(3600e3), open_until: iso(7200e3) });
  assert.equal((await call(later, rep())).body.error, 'closed');
  assert.deepEqual((await studentGet(later, `/status?t=${TOK}`)).body.rounds, []);         // not open yet: no card
  const over = await ready({ ...ROUND, open_from: iso(-7200e3), open_until: iso(-60e3) });
  assert.equal((await call(over, rep())).body.error, 'closed');
  const env = await ready();
  const st = (await studentGet(env, `/status?t=${TOK}`)).body.rounds;
  assert.deepEqual(st.map((r) => [r.r, r.n, r.label, r.mine]), [[RID, 20, '11/6 주간 테스트', null]]);
  assert.deepEqual((await studentGet(env, `/status?t=${B}`)).body.rounds, []);              // another class sees nothing
  await call(env, rep());
  const mine = (await studentGet(env, `/status?t=${TOK}`)).body.rounds[0].mine;
  assert.deepEqual([mine.incorrect, mine.missed, mine.hardest, mine.reasons], [[3, 7], [12], [7, 12], ['계산실수', '시간부족']]);
  // close now (the console pushes the round again with an earlier end)
  await teacherPut(env, '/rounds', { rounds: [{ ...ROUND, open_until: iso(-1000) }] });
  assert.equal((await call(env, rep())).body.error, 'closed');
});

test('F14: the teacher pulls reports; /rounds and /reports need the teacher token', async () => {
  const env = await ready();
  await call(env, rep());
  assert.equal((await teacherPut(env, '/rounds', { rounds: [] }, 'wrong-token')).status, 401);
  const r = await teacherGet(env, '/reports?since=2020-01-01T00:00:00.000Z');
  assert.equal(r.body.items.length, 1);
  assert.deepEqual(Object.keys(r.body.items[0]).sort(),
    ['hardest', 'incorrect', 'metaphor', 'missed', 'n', 'reasons', 'rid', 'token', 'ts', 'updated', 'wishlist']);
  const bad = await teacherPut(env, '/rounds', { rounds: [{ ...ROUND, rid: 'Adaptive_X' }, { ...ROUND, n: 0 }, { ...ROUND, classes: [] }] });
  assert.equal(bad.body.n, 0);                                                                // invalid rounds are dropped
});
