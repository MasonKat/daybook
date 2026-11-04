// H1: one full class day on the free plan must stay under 30 % of every daily limit.
// 20 students, a 3-hour board polling every 60 s, 3 moments with 20 reactions each, every student opening the page 5x,
// one AMP Live round (20 students x 10 questions, everyone polling every 2 s for ~20 minutes), and (F14) an open
// weekly-test round: every student sends the report and edits it once; (F15) 3 watch records each, push opt-in and
// one push after 발행; (F18.3) the review wordlist + name hashes synced, every student writes 1 review, opens the review
// sheet on each of the 5 page opens, and votes once; the hourly vote cron runs 24 times; (F19.2) every student reacts once to
// every one of 12 videos (the worst case of one reaction per student per video per day) and loads the totals on every open;
// (F19.4) the teacher remote: 40 taps in class (each a save, half with the badge refresh), 3 undos, the roster twice;
// (F22.4) every student loads ⚙ 설정 on each open and saves it twice, 5 upload an own picture (the worst case of 1/day),
// the teacher pulls and decides them.
import fs from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { seoulDay } from '../src/index.js';
const addDays = (d, n) => new Date(Date.parse(d + 'T00:00:00Z') + n * 86400e3).toISOString().slice(0, 10);
import { ORIGIN, TEACHER, mkEnv } from './helpers.mjs';

// Cloudflare free plan, per day (Workers / KV / D1 pricing pages; re-check them if the plan changes)
export const FREE = { requests: 100000, kvRead: 100000, kvWrite: 1000, kvList: 1000, kvDelete: 1000, d1RowsRead: 5000000, d1RowsWritten: 100000,
  doRequests: 100000, doRowsRead: 5000000, doRowsWritten: 100000 };
const JPEG = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(30000, 3)]).toString('base64');

test('a full class day stays under 30 % of every free daily limit', async () => {
  // F15.3: VAPID keys (the RFC 8291 test keys), a fake push service, no quiet hours for the day
  const env = mkEnv({ VAPID_PUBLIC: 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8',
    VAPID_PRIVATE: 'yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw', VAPID_SUBJECT: 'mailto:t@example.com', PUSH_QUIET: '0-0' });
  let pushOut = 0;
  env.PUSH_FETCH = async () => { pushOut++; return { status: 201 }; };
  let requests = 0;
  const go = async (path, init = {}) => { requests++; const r = await worker.fetch(new Request('https://w.example' + path, init), env); return r.headers.get('content-type')?.includes('json') ? r.json() : r.arrayBuffer(); };
  const teacher = (path, method = 'GET', body) => go(path, { method, headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' }, body: body && JSON.stringify(body) });
  const student = (path, form) => go(path, form ? { method: 'POST', body: form, headers: { Origin: ORIGIN } } : { headers: { Origin: ORIGIN } });
  const day = seoulDay(Date.now());
  const toks = Array.from({ length: 20 }, (_, i) => 'abcdefghijklmnopqrstuvwxy'.slice(0, 24) + 'abcdefghijklmnopqrst'[i] + 'z');
  const tokmap = Object.fromEntries(toks.map((t) => [t, 'c1']));

  await teacher('/tokmap', 'PUT', { tokmap, pools: { c1: 'g10' } });             // publish (F19.2: the grade pool)
  await teacher('/rounds', 'PUT', { rounds: [{ rid: '0123456789abcdef', classes: ['c1'], n: 20, label: '주간 테스트',
    open_from: new Date(Date.now() - 3600e3).toISOString(), open_until: new Date(Date.now() + 86400e3).toISOString() }] });
  await teacher('/session', 'PUT', { class: 'c1', date: day, code: '1234', start: '18:00', tokmap,
    open_from: new Date(Date.now() - 30 * 60e3).toISOString(), open_until: new Date(Date.now() + 60 * 60e3).toISOString() });
  for (let n = 1; n <= 3; n++) await teacher('/moment-put', 'PUT', { class: 'c1', date: day, n, text: `장면 ${n}`, image: JPEG });
  const words = fs.readFileSync(new URL('../../filters/review_words.txt', import.meta.url), 'utf-8').split('\n');
  const rround = { id: 'c1-2026-11-02', class: 'c1', open_from: new Date(Date.now() - 600e3).toISOString(),
    open_until: new Date(Date.now() + 86000e3).toISOString(), period_from: addDays(day, -13), period_to: addDays(day, 13) };
  await teacher('/reviews-sync', 'PUT', { words, rounds: [rround],                // F18.3: 발행 sends the list, hashes, the round
    names: { c1: Array.from({ length: 20 }, (_, i) => (i.toString(16) + '0123456789abcdef').slice(0, 16)) } });
  for (const t of toks) {
    for (let i = 0; i < 5; i++) { await student(`/status?t=${t}`); await student(`/moments?t=${t}`); await student(`/reviews?t=${t}`); await student(`/vreacts?t=${t}`); await student(`/prefs?t=${t}`); }   // 5 page opens
    for (let k = 0; k < 12; k++) {                                           // F19.2: 12 videos, one reaction each
      const xf = new FormData(); xf.append('t', t); xf.append('v', `AMPvideo0${String(k).padStart(2, '0')}`); xf.append('r', ['fire', 'good', 'curious', 'hard', 'try'][k % 5]);
      assert.equal((await student('/vreact', xf)).ok, true);
    }
    for (let k = 0; k < 2; k++) {                                            // F22.4: ⚙ 설정 saved twice
      const pf = new FormData(); pf.append('t', t); pf.append('prefs', JSON.stringify({ avatar: k ? 'p:cat-1' : 'p:owl-3' }));
      assert.equal((await student('/prefs', pf)).ok, true);
    }
    if (toks.indexOf(t) < 5) { const af = new FormData(); af.append('t', t); af.append('image', JPEG.slice(0, 40000)); assert.equal((await student('/avatar', af)).ok, true); }
    { const rf = new FormData(); rf.append('t', t); rf.append('text', `오늘 수업 웃겼던 순간 ${t.slice(-2)}`); rf.append('hide', '0');
      assert.equal((await student('/review', rf)).ok, true); }                   // F18.3: one review a day
    const wrong = new FormData(); wrong.append('t', t); wrong.append('code', '0000');
    if (t.endsWith('az') || t.endsWith('bz')) await student('/checkin', wrong);
    const fd = new FormData(); fd.append('t', t); fd.append('code', '1234');
    await student('/checkin', fd);
    for (let n = 1; n <= 3; n++) {
      await student(`/moment?t=${t}&k=c1:${day}:${n}`);
      const rf = new FormData(); rf.append('t', t); rf.append('k', `c1:${day}:${n}`); rf.append('r', ['fire', 'clap', 'mind'][n - 1]);
      await student('/react', rf);
    }
    for (let edit = 0; edit < 2; edit++) {                                      // F14: the report + one edit
      const rf = new FormData(); rf.append('t', t); rf.append('round', '0123456789abcdef');
      rf.append('incorrect', edit ? '3,7,12' : '3,7'); rf.append('missed', '19,20'); rf.append('hardest', '7');
      rf.append('reasons', '계산실수,시간부족'); rf.append('wishlist', '풀이 영상'); rf.append('metaphor', '마라톤');
      assert.equal((await student('/report', rf)).ok, true);
    }
    {                                                                          // F15.3: the student opts in to push
      const pf = new FormData(); pf.append('t', t); pf.append('role', 's'); pf.append('endpoint', `https://push.example.com/${t}`);
      pf.append('p256dh', 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4'); pf.append('auth', 'BTBZMqHH6r4Tts7J_aSIgg');
      assert.equal((await student('/push/subscribe', pf)).ok, true);
    }
    for (let k = 0; k < 3; k++) {                                              // F15.1: 3 videos watched, one record each
      const wf = new FormData(); wf.append('t', t); wf.append('v', `AMPvideo00${k}`); wf.append('sec', '240'); wf.append('pct', '80');
      assert.equal((await student('/watch', wf)).ok, true);
    }
    for (let up = 0; up < (t.endsWith('cz') ? 2 : 1); up++) {
      const sf = new FormData(); sf.append('t', t); sf.append('line', '오늘 한 줄'); sf.append('chip', 'ok');
      sf.append('photo', new File([new Uint8Array(200000)], 'p.jpg', { type: 'image/jpeg' }));
      const r = await student('/submit', sf);
      assert.equal(r.ok, true);
    }
  }
  // F18.3: the teacher approves them all (one sync), everyone votes once, the hourly cron runs all day
  const pulled = await teacher('/reviews-pull?since=2020-01-01');
  await teacher('/reviews-sync', 'PUT', { reviews: pulled.reviews.map((r) => ({ id: r.id, status: 'approved', label: '' })), rounds: [rround] });
  for (const [i, t] of toks.entries()) {
    const vf = new FormData(); vf.append('t', t); vf.append('round', rround.id); vf.append('review', pulled.reviews[(i + 1) % toks.length].id);
    assert.equal((await student('/review/vote', vf)).ok, true);
    await student(`/reviews?t=${t}`);
  }
  for (let h = 0; h < 24; h++) await worker.scheduled({ cron: '7 * * * *' }, env);
  // F19.4: the teacher remote during class
  await teacher('/remote-roster', 'PUT', { blob: 'A'.repeat(4000) });
  const tr = (path, body) => go(path, body ? { method: 'POST', headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : { headers: { 'X-Teacher-Token': TEACHER } });
  for (let k = 0; k < 2; k++) { await go('/t/remote/'); await go('/t/remote/remote.js'); await tr('/t/remote/roster'); }
  const t0 = Date.now();
  for (let k = 0; k < 40; k++) {
    const sid = (k + 1).toString(16).padStart(16, '0');
    assert.equal((await tr('/t/remote/shout', { id: sid, t: toks[k % 20], points: 1 + (k % 3), text: '풀이 깔끔', at: t0 + k * 4000 })).ok, true);
    if (k % 2) await tr('/t/remote/today?class=c1');
    if (k % 13 === 0) await tr('/t/remote/undo', { id: sid });
  }
  for (let poll = 0; poll < 180; poll++) {                                       // the board: 3 h at 60 s
    await teacher('/submissions?since=2026-01-01T00:00:00.000Z');
    await teacher('/checkins?since=2026-01-01T00:00:00.000Z');
    await teacher('/moments?class=c1');
  }
  // F7: one AMP Live round, 20 students x 10 questions, ~2 min per question, everyone polling every 2 s
  const open = await go('/live/open', { method: 'POST', headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' },
    body: JSON.stringify({ class: 'c1', questions: Array.from({ length: 10 }, (_, i) => ({ kind: 'choice', answer: String(1 + (i % 5)) })) }) });
  const room = open.room;
  const tpost = (path, body) => go(path, { method: 'POST', headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  for (const t of toks) { const f = new FormData(); f.append('t', t); f.append('room', room); await student('/live/join', f); }
  for (let q = 1; q <= 10; q++) {
    await tpost('/live/next', { room, q });
    for (let tick = 0; tick < 60; tick++) {                                      // 120 s at 2 s
      await teacher(`/live/state?room=${room}`);                                 // the projector
      for (const t of toks) await student(`/live/state?t=${t}&room=${room}`);
    }
    for (const t of toks) { const f = new FormData(); f.append('t', t); f.append('room', room); f.append('q', q); f.append('a', '1'); await student('/live/answer', f); }
    await tpost('/live/reveal', { room });
  }
  await tpost('/live/close', { room });
  // F15.3: after 발행, one push to the class, in batches (the free plan's subrequest/CPU limits), then nothing more today
  for (let more = true; more;) more = (await teacher('/push/send', 'POST', { classes: ['c1'], kind: 'report', body: '이번 주 기록이 업데이트됐어요' })).more;
  assert.equal(pushOut, 20);                     // F18.3: the vote push reached all 20 and used today's cap: none after 발행
  assert.equal((await teacher('/push/send', 'POST', { classes: ['c1'], kind: 'video', body: 'x' })).sent, 0);   // daily cap
  await teacher('/push/count');
  await teacher('/submissions'); await teacher('/checkins'); await teacher('/live/results'); await teacher('/reports'); await teacher('/watches');
  await teacher('/reviews-pull?since=2020-01-01'); await teacher('/vreacts-pull?since=2020-01-01');
  await teacher('/shouts-pull?since=2020-01-01');                              // Saturday-style pull
  for (const a of (await teacher('/avatars-pull?since=2020-01-01')).avatars) await tpost('/avatar-decide', { token: a.token, ok: true, ts: a.ts });

  const kv = env.SUBMISSIONS.ops, d1 = env.DB.usage;
  const dO = env.LIVE.usage;
  const used = { requests, kvRead: kv.get, kvWrite: kv.put, kvList: kv.list, kvDelete: kv.delete, d1RowsRead: d1.rowsRead, d1RowsWritten: d1.rowsWritten,
    doRequests: dO.requests, doRowsRead: dO.rowsRead, doRowsWritten: dO.rowsWritten };
  const table = Object.keys(FREE).map((k) => `${k.padEnd(14)} ${String(used[k]).padStart(7)} / ${String(FREE[k]).padStart(9)}  ${(100 * used[k] / FREE[k]).toFixed(2)} %`);
  console.log('\n# class-day budget (free plan)\n# ' + table.join('\n# '));
  for (const k of Object.keys(FREE)) assert.ok(used[k] < 0.3 * FREE[k], `${k}: ${used[k]} is not < 30 % of ${FREE[k]}`);
  assert.equal(kv.list, 0, 'no KV list operations at all');
});
