// games-v1 수학 용어 찾기: a Live room in game mode; every find is checked by the room; everyone sees n/N; no names.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { checkGame } from '../src/index.js';
import { ALL, B, ORIGIN, TEACHER, TOK, A, allow, call, mkEnv } from './helpers.mjs';

const GRID = ['명제가나다라마바', '대아자차카타파하', '우가나다라마바사', '가나다라마바사아', '나다라마바사아자', '다라마바사아자차', '라마바사아자차카', '마바사아자차카타'];
const GAME = { grid: GRID, minutes: 8, words: [{ w: '명제', cells: [[0, 0], [0, 1]] }, { w: '대우', cells: [[1, 0], [2, 0]] }] };
const tpost = (env, path, body) => call(env, new Request('https://w.example' + path, { method: 'POST',
  headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
const spost = (env, path, f) => { const fd = new FormData(); for (const [k, v] of Object.entries(f)) fd.append(k, v);
  return call(env, new Request('https://w.example' + path, { method: 'POST', body: fd, headers: { Origin: ORIGIN } })); };

test('games-v1: open, join, finds light up for everyone, wrong finds and other classes refused, close keeps nothing', async () => {
  const env = mkEnv(); await allow(env, ALL);
  assert.equal(checkGame({ ...GAME, words: [{ w: '명제', cells: [[0, 0], [0, 2]] }] }), null);            // cells must spell the word
  assert.equal(checkGame({ ...GAME, grid: ['abc'] }), null);
  assert.equal((await tpost(env, '/live/open', { class: 'c1', game: { ...GAME, minutes: 60 } })).status, 400);
  const o = (await tpost(env, '/live/open', { class: 'c1', game: GAME })).body;
  assert.ok(o.ok && /^\d{4}$/.test(o.room));
  const j = (await spost(env, '/live/join', { t: TOK, room: o.room })).body;
  assert.ok(j.game && j.grid.length === 8 && j.words.join() === '명제,대우' && j.found.length === 0 && !JSON.stringify(j).includes('cells":[[1'));
  assert.equal((await spost(env, '/live/find', { t: TOK, room: o.room, a: '0,0', b: '0,2' })).body.hit, -1);  // wrong end
  const f = (await spost(env, '/live/find', { t: A, room: o.room, a: '2,0', b: '1,0' })).body;            // backwards is fine
  assert.equal(f.hit, 1);
  const seen = (await call(env, new Request(`https://w.example/live/state?t=${TOK}&room=${o.room}`, { headers: { Origin: ORIGIN } }))).body;
  assert.deepEqual(seen.found, [{ i: 1, cells: [[1, 0], [2, 0]] }]);                                         // another phone sees it
  assert.equal((await spost(env, '/live/find', { t: B, room: o.room, a: '0,0', b: '0,1' })).body.error, 'no_room');  // other class
  assert.equal((await spost(env, '/live/answer', { t: TOK, room: o.room, q: '1', a: '1' })).body.error, 'op');
  await spost(env, '/live/find', { t: TOK, room: o.room, a: '0,1', b: '0,0' });
  const t = (await call(env, new Request(`https://w.example/live/state?room=${o.room}`, { headers: { 'X-Teacher-Token': TEACHER } }))).body;
  assert.ok(t.done && t.found.length === 2 && !JSON.stringify(t).includes(TOK));                         // no tokens, no names
  assert.equal((await tpost(env, '/live/close', { room: o.room })).body.closed, true);
  assert.equal((await spost(env, '/live/join', { t: TOK, room: o.room })).body.open, false);
});
