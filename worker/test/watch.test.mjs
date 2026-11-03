// F15.1: watch signals. One record per (token, video, day), the max kept; validated; class-scoped; teacher pull.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ALL, ORIGIN, TOK, allow, call, mkEnv, teacherGet } from './helpers.mjs';

const ready = async () => { const e = mkEnv(); await allow(e, ALL); return e; };
function w(fields, t = TOK, origin = ORIGIN) {
  const fd = new FormData(); fd.append('t', t);
  for (const [k, v] of Object.entries({ v: 'AMPshort001', sec: '40', pct: '55', ...fields })) fd.append(k, v);
  return new Request('https://w.example/watch', { method: 'POST', body: fd, headers: origin ? { Origin: origin } : {} });
}
const rows = (env) => env.DB.db.prepare('SELECT * FROM watches ORDER BY v').all();

test('F15.1: one record per token, video and day; the larger value wins', async () => {
  const env = await ready();
  assert.equal((await call(env, w())).body.ok, true);
  await call(env, w({ sec: '12', pct: '20' }));                  // a shorter second viewing never lowers it
  await call(env, w({ sec: '90', pct: '30' }));                  // more seconds, lower percent: each max is kept
  const r = rows(env);
  assert.equal(r.length, 1); assert.deepEqual([r[0].sec, r[0].pct, r[0].class], [90, 55, 'c1']);
  await call(env, w({ v: 'AMPdemo0001' }));
  assert.equal(rows(env).length, 2);
  const pulled = await teacherGet(env, '/watches?since=2020-01-01T00:00:00.000Z');
  assert.deepEqual(Object.keys(pulled.body.items[0]).sort(), ['day', 'pct', 'sec', 'token', 'ts', 'updated', 'v']);
});

test('F15.1: validation, unknown tokens, CORS and the rate limit', async () => {
  const env = await ready();
  for (const bad of [{ v: 'x' }, { v: '<script>' }, { sec: '-1' }, { sec: '1.5' }, { sec: '36001' }, { pct: '101' }, { pct: 'a' }]) {
    assert.equal((await call(env, w(bad))).body.error, 'bad_watch', JSON.stringify(bad));
  }
  assert.equal((await call(env, w({}, 'zzzzzzzzzzzzzzzzzzzzzzzzzz'))).body.error, 'unknown_token');
  assert.equal((await call(env, w({}, TOK, 'https://evil.example'))).status, 403);
  assert.equal(rows(env).length, 0);
  const lim = mkEnv({ WATCHES_PER_HOUR: '2' }); await allow(lim, ALL);
  await call(lim, w()); await call(lim, w());
  assert.equal((await call(lim, w())).body.error, 'rate_limited');
});
