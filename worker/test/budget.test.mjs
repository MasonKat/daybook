// H1: one full class day on the free plan must stay under 30 % of every daily limit.
// 20 students, a 3-hour board polling every 60 s, 3 moments with 20 reactions each, every student opening the page 5x.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { seoulDay } from '../src/index.js';
import { ORIGIN, TEACHER, mkEnv } from './helpers.mjs';

// Cloudflare free plan, per day (Workers / KV / D1 pricing pages; re-check them if the plan changes)
export const FREE = { requests: 100000, kvRead: 100000, kvWrite: 1000, kvList: 1000, kvDelete: 1000, d1RowsRead: 5000000, d1RowsWritten: 100000 };
const JPEG = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(30000, 3)]).toString('base64');

test('a full class day stays under 30 % of every free daily limit', async () => {
  const env = mkEnv();
  let requests = 0;
  const go = async (path, init = {}) => { requests++; const r = await worker.fetch(new Request('https://w.example' + path, init), env); return r.headers.get('content-type')?.includes('json') ? r.json() : r.arrayBuffer(); };
  const teacher = (path, method = 'GET', body) => go(path, { method, headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' }, body: body && JSON.stringify(body) });
  const student = (path, form) => go(path, form ? { method: 'POST', body: form, headers: { Origin: ORIGIN } } : { headers: { Origin: ORIGIN } });
  const day = seoulDay(Date.now());
  const toks = Array.from({ length: 20 }, (_, i) => 'abcdefghijklmnopqrstuvwxy'.slice(0, 24) + 'abcdefghijklmnopqrst'[i] + 'z');
  const tokmap = Object.fromEntries(toks.map((t) => [t, 'c1']));

  await teacher('/tokmap', 'PUT', { tokmap });                                  // publish
  await teacher('/session', 'PUT', { class: 'c1', date: day, code: '1234', start: '18:00', tokmap,
    open_from: new Date(Date.now() - 30 * 60e3).toISOString(), open_until: new Date(Date.now() + 60 * 60e3).toISOString() });
  for (let n = 1; n <= 3; n++) await teacher('/moment-put', 'PUT', { class: 'c1', date: day, n, text: `장면 ${n}`, image: JPEG });
  for (const t of toks) {
    for (let i = 0; i < 5; i++) { await student(`/status?t=${t}`); await student(`/moments?t=${t}`); }   // 5 page opens
    const wrong = new FormData(); wrong.append('t', t); wrong.append('code', '0000');
    if (t.endsWith('az') || t.endsWith('bz')) await student('/checkin', wrong);
    const fd = new FormData(); fd.append('t', t); fd.append('code', '1234');
    await student('/checkin', fd);
    for (let n = 1; n <= 3; n++) {
      await student(`/moment?t=${t}&k=c1:${day}:${n}`);
      const rf = new FormData(); rf.append('t', t); rf.append('k', `c1:${day}:${n}`); rf.append('r', ['fire', 'clap', 'mind'][n - 1]);
      await student('/react', rf);
    }
    for (let up = 0; up < (t.endsWith('cz') ? 2 : 1); up++) {
      const sf = new FormData(); sf.append('t', t); sf.append('line', '오늘 한 줄'); sf.append('chip', 'ok');
      sf.append('photo', new File([new Uint8Array(200000)], 'p.jpg', { type: 'image/jpeg' }));
      const r = await student('/submit', sf);
      assert.equal(r.ok, true);
    }
  }
  for (let poll = 0; poll < 180; poll++) {                                       // the board: 3 h at 60 s
    await teacher('/submissions?since=2026-01-01T00:00:00.000Z');
    await teacher('/checkins?since=2026-01-01T00:00:00.000Z');
    await teacher('/moments?class=c1');
  }
  await teacher('/submissions'); await teacher('/checkins');                    // Saturday-style pull

  const kv = env.SUBMISSIONS.ops, d1 = env.DB.usage;
  const used = { requests, kvRead: kv.get, kvWrite: kv.put, kvList: kv.list, kvDelete: 0, d1RowsRead: d1.rowsRead, d1RowsWritten: d1.rowsWritten };
  const table = Object.keys(FREE).map((k) => `${k.padEnd(14)} ${String(used[k]).padStart(7)} / ${String(FREE[k]).padStart(9)}  ${(100 * used[k] / FREE[k]).toFixed(2)} %`);
  console.log('\n# class-day budget (free plan)\n# ' + table.join('\n# '));
  for (const k of Object.keys(FREE)) assert.ok(used[k] < 0.3 * FREE[k], `${k}: ${used[k]} is not < 30 % of ${FREE[k]}`);
  assert.equal(kv.list, 0, 'no KV list operations at all');
});
