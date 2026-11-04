// F18.3 학생 리뷰: the filter, pre-moderation, one a day, class scope, the random-time vote (one per token, no self-vote,
// fewer than 3 candidates = no vote), the hourly vote push.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import nodeCrypto from 'node:crypto';
import { check, parseList } from '../src/review_filter.js';
import worker from '../src/index.js';
import { A, A2, ALL, B, ORIGIN, TEACHER, TOK, allow, call, mkEnv, studentGet } from './helpers.mjs';

const LIST = parseList(fs.readFileSync(new URL('../../filters/review_words.txt', import.meta.url), 'utf-8'));
const words = fs.readFileSync(new URL('../../filters/review_words.txt', import.meta.url), 'utf-8').split('\n');
const nameHash = (n) => nodeCrypto.createHmac('sha256', TEACHER).update(`name:${n}`).digest('hex').slice(0, 16);
const ready = async () => {
  const env = mkEnv(); await allow(env, ALL);
  await call(env, new Request('https://w.example/reviews-sync', { method: 'PUT', headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' },
    body: JSON.stringify({ words, names: { c1: ['가람', '한가람', '도담'].map(nameHash) } }) }));
  return env;
};
function write(t, text, hide = '0') {
  const fd = new FormData(); fd.append('t', t); fd.append('text', text); fd.append('hide', hide);
  return new Request('https://w.example/review', { method: 'POST', body: fd, headers: { Origin: ORIGIN } });
}
const sync = (env, body) => call(env, new Request('https://w.example/reviews-sync', { method: 'PUT',
  headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
const rows = (env) => env.DB.db.prepare('SELECT * FROM reviews ORDER BY ts').all();

test('F18.3 filter: 욕설 (plain, 초성, spaced, substituted), hate, phones, links; normal jokes pass', () => {
  for (const bad of ['시발 어려움', 'ㅅㅂ', 'ㅅ 1 ㅂ', '씨 . 발', '시1발', 'tlqkf', 'Tlqkf 문제', '병.신', 'ㅂㅅ같은 문제', 'f u c k', 'F*U*C*K',
    '전라디언 같은 소리', '김치녀', '짱깨', '애자 같다', '010-1234-5678로 연락', 'www.naver.com 봐']) {
    assert.equal(check(bad, LIST).ok, false, bad);
  }
  for (const ok of ['선생님 판서가 피카소급', '오늘 시발점 문제 진짜 웃겼다', '이번 시 발표 최고', '다섯 번 틀려서 웃겨 죽는 줄',
    '불이 꺼져서 다들 놀람', '1학년아 힘내', '수박 먹는 꿈', '18문제 다 맞음']) {
    assert.equal(check(ok, LIST).ok, true, ok);
  }
});

// the Windows spot-check (11-04): 17 must block, 10 must pass, 0 false blocks
const PEER_BLOCK = ['ㅆㅂ', '씨 발', 'ㅂㅅ같은', '병1신', 'qudtls', '개새끼', '김치녀', '전라디언', '존나', 'ㅈㄴ', 'ㄱㅅㄲ',
  '느금마', '틀딱', '한남', '장애인같이 풀었다', '흑형', '시이발'];
const PEER_PASS = ['피카소급', '개웃김ㅋㅋ', '시발점', '삑사리', '병아리', '존경합니다', '롤러코스터', '수학의 신', '18번', '미친 난이도'];
test('F18 fix: 패드립, age, gender, race, region, disability insults and elongation are blocked; ordinary words pass', () => {
  for (const bad of [...PEER_BLOCK, '씨이이발', '조오온나', '니애미', '급식충', '한남충', '페미충', '짱께', '쪽발이', '찐따', '장애인처럼 느림',
    '장애인 새끼', '븅신', '김치년']) {
    assert.equal(check(bad, LIST).ok, false, bad);
  }
  for (const ok of [...PEER_PASS, '장애인 차별 반대 포스터', '한 남자가 문제를 풀었다', '한남동 학원 앞', '한 녀석이 웃김', '홍어 먹어 봄',
    '살이 찐다 간식 그만', '기이한 문제', '오오 정답', '아아 아깝다', '좋아요', '8등신 그림', '흑인 수학자 이야기']) {
    assert.equal(check(ok, LIST).ok, true, ok);
  }
});

test('F18.3 write: pre-moderated, one a day, ≤ 60 characters, blocked text is never stored, a classmate\'s name is blocked', async () => {
  const env = await ready();
  assert.deepEqual((await call(env, write(TOK, '시발 문제'))).body, { ok: false, error: 'blocked' });
  assert.equal((await call(env, write(TOK, '가람이 바보'))).body.error, 'blocked');               // a classmate's display name
  assert.equal((await call(env, write(TOK, '한가람 판서 짱'))).body.error, 'blocked');              // a full name
  assert.equal(rows(env).length, 0);
  assert.equal((await call(env, write(TOK, '가'.repeat(61)))).body.error, 'length');
  const ok = await call(env, write(TOK, '선생님 판서가 피카소급', '1'));
  assert.equal(ok.body.ok, true); assert.equal(ok.body.status, 'pending');
  assert.equal((await call(env, write(TOK, '하루에 두 번째'))).body.error, 'one_a_day');
  const r = rows(env)[0];
  assert.deepEqual([r.status, r.hide, r.class], ['pending', 1, 'c1']);
  const list = await studentGet(env, `/reviews?t=${A}`);
  assert.deepEqual(list.body.approved, []);                                                        // pending: nobody sees it
  assert.equal((await studentGet(env, `/reviews?t=${TOK}`)).body.mine[0].status, 'pending');        // the writer sees 확인 중
});

test('F18.3 vote: < 3 approved = none; one vote per token; no self-vote; class scope', async () => {
  const env = await ready();
  for (const [t, txt] of [[TOK, '판서가 피카소급'], [A, '오늘 퀴즈 역대급'], [A2, '분수 대결 명승부'], [B, '다른 반 리뷰']]) await call(env, write(t, txt));
  const all = rows(env);
  const day = all[0].day;
  const round = { id: 'c1-2026-11-02', class: 'c1', open_from: new Date(Date.now() - 3600e3).toISOString(),
    open_until: new Date(Date.now() + 23 * 3600e3).toISOString(), period_from: day, period_to: day };
  await sync(env, { reviews: all.filter((r) => r.class === 'c1').slice(0, 2).map((r) => ({ id: r.id, status: 'approved', label: '' })), rounds: [round] });
  assert.equal((await studentGet(env, `/reviews?t=${TOK}`)).body.vote, null);                         // 2 approved: no vote
  await sync(env, { reviews: all.map((r) => ({ id: r.id, status: 'approved', label: r.token === A ? '도담' : '' })) });
  const v = (await studentGet(env, `/reviews?t=${TOK}`)).body;
  assert.equal(v.vote.candidates.length, 3);                                                         // c2's review is not a candidate
  assert.equal(v.approved.find((x) => x.text === '오늘 퀴즈 역대급').label, '도담');
  assert.ok(v.vote.candidates.find((c) => c.mine));
  const vote = (t, review) => { const fd = new FormData(); fd.append('t', t); fd.append('round', round.id); fd.append('review', review);
    return call(env, new Request('https://w.example/review/vote', { method: 'POST', body: fd, headers: { Origin: ORIGIN } })); };
  const own = all.find((r) => r.token === TOK).id, other = all.find((r) => r.token === A).id;
  assert.equal((await vote(TOK, own)).body.error, 'own_review');
  assert.equal((await vote(TOK, other)).body.ok, true);
  assert.equal((await vote(TOK, other)).body.error, 'already_voted');
  assert.equal((await vote(B, other)).body.error, 'closed');                                          // c2 has no open round
  assert.equal((await studentGet(env, `/reviews?t=${TOK}`)).body.vote.voted, other);
  const pulled = await call(env, new Request('https://w.example/reviews-pull?since=2020-01-01', { headers: { 'X-Teacher-Token': TEACHER } }));
  assert.equal(pulled.body.votes.length, 1); assert.equal(pulled.body.reviews.length, 4);
  // hidden later by the teacher: gone from classmates' lists
  await sync(env, { reviews: [{ id: other, status: 'hidden', label: '' }] });
  assert.ok(!(await studentGet(env, `/reviews?t=${TOK}`)).body.approved.find((x) => x.id === other));
});

test('F18.3 the hourly cron opens a scheduled vote with one push (not in quiet hours)', async () => {
  const env = await ready();
  Object.assign(env, { VAPID_PUBLIC: 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8',
    VAPID_PRIVATE: 'yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw', VAPID_SUBJECT: 'mailto:t@example.com', PUSH_QUIET: '0-0' });
  let pushes = 0;
  env.PUSH_FETCH = async () => { pushes++; return { status: 201 }; };
  for (const [t, txt] of [[TOK, '판서가 피카소급'], [A, '오늘 퀴즈 역대급'], [A2, '분수 대결 명승부']]) await call(env, write(t, txt));
  const day = rows(env)[0].day;
  const round = { id: 'c1-r1', class: 'c1', open_from: new Date(Date.now() - 600e3).toISOString(),
    open_until: new Date(Date.now() + 86000e3).toISOString(), period_from: day, period_to: day };
  await sync(env, { reviews: rows(env).map((r) => ({ id: r.id, status: 'approved', label: '' })),
    rounds: [round] });
  const fd = new FormData();
  for (const [k, v] of Object.entries({ t: TOK, role: 's', endpoint: 'https://push.example.com/x',
    p256dh: 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4', auth: 'BTBZMqHH6r4Tts7J_aSIgg' })) fd.append(k, v);
  await call(env, new Request('https://w.example/push/subscribe', { method: 'POST', body: fd, headers: { Origin: ORIGIN } }));
  await worker.scheduled({ cron: '7 * * * *' }, env);
  await sync(env, { rounds: [round] });                                                  // the next 발행 re-sends the round
  await worker.scheduled({ cron: '7 * * * *' }, env);                                    // pushed once, not twice
  assert.equal(pushes, 1);
});

test('F19.2 video reactions: one per token per video (changeable), totals pooled, shown only from 3 students, 어려워요 never shown', async () => {
  const env = mkEnv(); await allow(env, ALL);
  // c1 and c2 share the grade pool g10
  await call(env, new Request('https://w.example/tokmap', { method: 'PUT', headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' },
    body: JSON.stringify({ tokmap: ALL, pools: { c1: 'g10', c2: 'g10' } }) }));
  const react = (t, r, v = 'AMPvideo001') => { const fd = new FormData(); fd.append('t', t); fd.append('v', v); fd.append('r', r);
    return call(env, new Request('https://w.example/vreact', { method: 'POST', body: fd, headers: { Origin: ORIGIN } })); };
  let j = (await react(TOK, 'fire')).body;
  assert.deepEqual([j.ok, j.mine, j.totals], [true, 'fire', null]);                       // 1 student: no totals yet
  await react(TOK, 'good');                                                               // changed, still one row
  await react(A, 'hard');
  j = (await react(B, 'fire')).body;                                                      // another class, same grade pool
  assert.deepEqual(j.totals, { fire: 1, good: 1, curious: 0, try: 0 });                   // 3 students; 어려워요 not in it
  assert.equal((await react(A, 'nope')).status, 400);
  const list = (await studentGet(env, `/vreacts?t=${TOK}`)).body;
  assert.equal(list.mine.AMPvideo001, 'good'); assert.ok(!('hard' in list.totals.AMPvideo001));
  await react(TOK, '');                                                                   // tapping it again removes it
  assert.equal((await studentGet(env, `/vreacts?t=${TOK}`)).body.totals.AMPvideo001, undefined);   // back to 2 students
  const pulled = await call(env, new Request('https://w.example/vreacts-pull?since=2020-01-01', { headers: { 'X-Teacher-Token': TEACHER } }));
  assert.deepEqual(pulled.body.items.map((x) => x.r).sort(), ['', 'fire', 'hard']);       // 어려워요, and the removal
});
