// live-v2: phone mode. Joined phones of the room's class see the current stem + choices (+ a PNG once per question);
// never the answer key; everything is wiped at close.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ALL, B, ORIGIN, TEACHER, TOK, allow, call, mkEnv } from './helpers.mjs';

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(500, 1)]).toString('base64');
const tpost = (env, path, body) => call(env, new Request('https://w.example' + path, { method: 'POST',
  headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
const sget = (env, path) => call(env, new Request('https://w.example' + path, { headers: { Origin: ORIGIN } }));
const join = (env, t, room) => { const fd = new FormData(); fd.append('t', t); fd.append('room', room);
  return call(env, new Request('https://w.example/live/join', { method: 'POST', body: fd, headers: { Origin: ORIGIN } })); };
const QS = [{ kind: 'choice', answer: '3', q: '$x^2-5x+6=0$의 두 근의 합은?', choices: ['$2$', '$3$', '$5$', '$6$', '$-5$'], img: PNG },
  { kind: 'num', answer: '12', q: '$3 \\times 4$는?' }];

test('live-v2 phone mode: stems + choices to joined phones of the class, the picture once, no key, wiped at close', async () => {
  const env = mkEnv(); await allow(env, ALL);
  assert.equal((await tpost(env, '/live/open', { class: 'c1', phone: true, questions: [{ ...QS[0], choices: ['1', '2'] }] })).status, 400);
  assert.equal((await tpost(env, '/live/open', { class: 'c1', phone: true, questions: [{ ...QS[0], img: 'R0lGODlh' }] })).status, 400);   // PNG only
  const o = (await tpost(env, '/live/open', { class: 'c1', phone: true, questions: QS })).body;
  assert.ok(o.ok);
  const j = (await join(env, TOK, o.room)).body;
  assert.equal(j.q, 0); assert.equal(j.stem, undefined);                                  // nothing before 다음 문제
  await tpost(env, '/live/next', { room: o.room, q: 1 });
  const s = (await sget(env, `/live/state?t=${TOK}&room=${o.room}`)).body;
  assert.equal(s.stem, QS[0].q); assert.deepEqual(s.choices, QS[0].choices); assert.equal(s.img, true);
  assert.ok(!JSON.stringify(s).includes('"answer"'));                                     // never the key
  assert.equal((await sget(env, `/live/img?t=${TOK}&room=${o.room}`)).body.img, PNG);
  assert.equal((await sget(env, `/live/state?t=${B}&room=${o.room}`)).body.open, false);  // another class: no room, no text
  assert.equal((await sget(env, `/live/img?t=${B}&room=${o.room}`)).body.open, false);
  await tpost(env, '/live/next', { room: o.room, q: 2 });
  const s2 = (await sget(env, `/live/state?t=${TOK}&room=${o.room}`)).body;
  assert.equal(s2.kind, 'num'); assert.equal(s2.choices, null); assert.equal(s2.img, false);
  await tpost(env, '/live/close', { room: o.room });
  assert.equal((await join(env, TOK, o.room)).body.open, false);
  const proj = (await tpost(env, '/live/open', { class: 'c1', questions: [{ kind: 'choice', answer: '1' }] })).body;   // projector mode
  await join(env, TOK, proj.room); await tpost(env, '/live/next', { room: proj.room, q: 1 });
  assert.equal((await sget(env, `/live/state?t=${TOK}&room=${proj.room}`)).body.stem, undefined);
});
