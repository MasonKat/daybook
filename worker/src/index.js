// DAYBOOK Worker. Never sees names: tokens, opaque class keys, dates.
import { send as pushSend } from './webpush.js';
// Bindings: D1 DB (records: submissions, check-ins, sessions, token map, moments, reactions, rate counters),
//           KV SUBMISSIONS (photos and moment JPEGs only, with TTLs), R2 PHOTOS (only when PHOTO_STORE = "r2").
// Secret: TEACHER_TOKEN. No KV `list` anywhere: the free plan allows ~1,000 lists a day (see DECISIONS, H1).
const TOKEN_RE = /^[a-z2-7]{26}$/;
const CLASS_RE = /^[a-z0-9]{1,12}$/;
const CODE_RE = /^\d{4}$/;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const MKEY_RE = /^[a-z0-9]{1,12}:\d{4}-\d{2}-\d{2}:[1-3]$/;
const TYPES = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/heic': 'heic', 'image/heif': 'heic' };
const CHIPS = new Set(['ok', 'confused', 'rewatch']);
const RID_RE = /^[a-f0-9]{16}$/;
// G1: bumped with every feature the console relies on (doctor warns when the deployed Worker is older):
// 1 base, 2 출석, 3 장면 + Live, 4 푼 문제 수, 5 주간 테스트 reports, 6 /health version + /tokmap-info, 7 /watch,
// 8 Web Push
export const WORKER_VERSION = 8;
const PUSH_KINDS = ['video', 'message', 'report', 'test'];
const DEFAULT_KINDS = { s: 'video,message,report,test', p: 'report' };
const VID_RE = /^[A-Za-z0-9_-]{6,20}$/;
const REASONS = ['개념탈락', '계산실수', '정보이해부족', '시간부족'];   // F14: the teacher's Notion "Reasoning" options
const REACTIONS = ['fire', 'clap', 'mind'];
const MAX_BYTES = 8 * 1024 * 1024;
const DAY = 86400;

export const seoulDay = (ms) => new Date(ms + 9 * 3600e3).toISOString().slice(0, 10);
const addDays = (day, n) => seoulDay(Date.parse(day + 'T00:00:00+09:00') + n * DAY * 1000);
const num = (v, d) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : d);

export const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS subs (token TEXT NOT NULL, day TEXT NOT NULL, set_id TEXT, ts TEXT NOT NULL, updated TEXT NOT NULL,
     key TEXT, n INTEGER NOT NULL DEFAULT 1, line TEXT, chip TEXT, solved INTEGER, PRIMARY KEY (token, day))`,
  'CREATE INDEX IF NOT EXISTS subs_updated ON subs (updated)',
  `CREATE TABLE IF NOT EXISTS checkins (token TEXT NOT NULL, day TEXT NOT NULL, class TEXT NOT NULL, ts TEXT NOT NULL,
     PRIMARY KEY (token, day))`,
  'CREATE INDEX IF NOT EXISTS checkins_ts ON checkins (ts)',
  `CREATE TABLE IF NOT EXISTS sessions (class TEXT NOT NULL, date TEXT NOT NULL, code TEXT NOT NULL, open_from TEXT NOT NULL,
     open_until TEXT NOT NULL, start TEXT, PRIMARY KEY (class, date))`,
  'CREATE TABLE IF NOT EXISTS tokmap (token TEXT PRIMARY KEY, class TEXT NOT NULL)',
  `CREATE TABLE IF NOT EXISTS moments (k TEXT PRIMARY KEY, class TEXT NOT NULL, date TEXT NOT NULL, n INTEGER NOT NULL,
     text TEXT, ts TEXT NOT NULL)`,
  'CREATE INDEX IF NOT EXISTS moments_class ON moments (class, date)',
  `CREATE TABLE IF NOT EXISTS reactions (k TEXT NOT NULL, token TEXT NOT NULL, r TEXT NOT NULL, ts TEXT NOT NULL,
     PRIMARY KEY (k, token))`,
  'CREATE TABLE IF NOT EXISTS counters (key TEXT PRIMARY KEY, n INTEGER NOT NULL, day TEXT NOT NULL)',
  // F7 AMP Live: open rooms (code -> class) and, after a room closes, only per-student counts
  `CREATE TABLE IF NOT EXISTS live_rooms (code TEXT PRIMARY KEY, class TEXT NOT NULL, date TEXT NOT NULL,
     status TEXT NOT NULL, closes_at TEXT NOT NULL)`,
  `CREATE TABLE IF NOT EXISTS live_results (room TEXT NOT NULL, date TEXT NOT NULL, class TEXT NOT NULL, token TEXT NOT NULL,
     answered INTEGER NOT NULL, correct INTEGER NOT NULL, ts TEXT NOT NULL, PRIMARY KEY (room, date, token))`,
  'CREATE INDEX IF NOT EXISTS live_results_ts ON live_results (ts)',
  // F14 weekly-test rounds: a hash of the teacher's round id (the id itself never leaves the laptop), the class keys
  // (comma-separated), the number of quizzes, the open window and the student-facing label
  `CREATE TABLE IF NOT EXISTS rounds (rid TEXT PRIMARY KEY, classes TEXT NOT NULL, n INTEGER NOT NULL,
     open_from TEXT NOT NULL, open_until TEXT NOT NULL, label TEXT)`,
  `CREATE TABLE IF NOT EXISTS reports (rid TEXT NOT NULL, token TEXT NOT NULL, class TEXT NOT NULL, incorrect TEXT, missed TEXT,
     hardest TEXT, reasons TEXT, wishlist TEXT, metaphor TEXT, ts TEXT NOT NULL, updated TEXT NOT NULL,
     n INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (rid, token))`,
  'CREATE INDEX IF NOT EXISTS reports_updated ON reports (updated)',
  // F15.1 watch signals: one row per (token, video, day), the largest seconds / percent seen (never a heartbeat stream)
  `CREATE TABLE IF NOT EXISTS watches (token TEXT NOT NULL, v TEXT NOT NULL, day TEXT NOT NULL, class TEXT NOT NULL,
     sec INTEGER NOT NULL, pct INTEGER NOT NULL, ts TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY (token, v, day))`,
  'CREATE INDEX IF NOT EXISTS watches_updated ON watches (updated)',
  // F15.3 Web Push (opt-in): one row per browser subscription; parents' tokens get their own map (they never upload)
  'CREATE TABLE IF NOT EXISTS ptokmap (token TEXT PRIMARY KEY, class TEXT NOT NULL)',
  `CREATE TABLE IF NOT EXISTS pushsubs (endpoint TEXT PRIMARY KEY, token TEXT NOT NULL, role TEXT NOT NULL, class TEXT NOT NULL,
     p256dh TEXT NOT NULL, auth TEXT NOT NULL, kinds TEXT NOT NULL, created TEXT NOT NULL, sent_day TEXT, sent_n INTEGER NOT NULL DEFAULT 0)`,
  'CREATE INDEX IF NOT EXISTS pushsubs_class ON pushsubs (class)',
];
const ready = new WeakMap();   // per database binding: create the tables once (no-op afterwards)
function init(env) {
  let p = ready.get(env.DB);
  if (!p) {
    p = env.DB.batch(SCHEMA.map((s) => env.DB.prepare(s)))
      // F13.5: a database created before `solved` existed gets the column; on a new one this fails harmlessly
      .then(() => env.DB.prepare('ALTER TABLE subs ADD COLUMN solved INTEGER').run().catch(() => null))
      .catch((e) => { ready.delete(env.DB); throw e; });
    ready.set(env.DB, p);
  }
  return p;
}

function json(body, status, headers) {
  return new Response(JSON.stringify(body), {
    status, headers: { ...headers, 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

function corsHeaders(req, env) {
  const origin = req.headers.get('Origin');
  const h = { Vary: 'Origin' };
  if (origin && origin === env.ALLOWED_ORIGIN) {
    Object.assign(h, { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Max-Age': '86400' });
  }
  return h;
}

function teacherOk(req, env) {
  const got = req.headers.get('X-Teacher-Token') || '', want = env.TEACHER_TOKEN || '';
  if (!want || got.length !== want.length) return false;
  let diff = 0;
  for (let i = 0; i < want.length; i++) diff |= got.charCodeAt(i) ^ want.charCodeAt(i);
  return diff === 0;
}

const one = (env, sql, ...args) => env.DB.prepare(sql).bind(...args).first();
const all = async (env, sql, ...args) => (await env.DB.prepare(sql).bind(...args).all()).results || [];
const run = (env, sql, ...args) => env.DB.prepare(sql).bind(...args).run();

// Atomic counter (rate limits): one upsert, no read-modify-write.
async function bump(env, key, day) {
  const r = await one(env, `INSERT INTO counters (key, n, day) VALUES (?, 1, ?)
    ON CONFLICT (key) DO UPDATE SET n = n + 1 RETURNING n`, key, day);
  return r ? r.n : 1;
}
async function peek(env, key) { const r = await one(env, 'SELECT n FROM counters WHERE key = ?', key); return r ? r.n : 0; }

async function classOf(env, token) {
  const r = await one(env, 'SELECT class FROM tokmap WHERE token = ?', token);
  return r ? r.class : null;
}

async function recentDays(env, token, today) {
  const rows = await all(env, 'SELECT day FROM subs WHERE token = ? AND day BETWEEN ? AND ? ORDER BY day', token, addDays(today, -7), today);
  return rows.map((r) => r.day);
}

// ---------------------------------------------------------------- /submit
async function submit(req, env, h) {
  const now = Date.now(), day = seoulDay(now), iso = new Date(now).toISOString();
  let form;
  try { form = await req.formData(); } catch { return json({ ok: false, error: 'bad_form' }, 400, h); }
  const token = String(form.get('t') || '').toLowerCase();
  if (!TOKEN_RE.test(token)) return json({ ok: false, error: 'bad_token' }, 400, h);
  // H3: only tokens the console published (the token map) may upload
  if (!(await one(env, 'SELECT 1 AS x FROM tokmap LIMIT 1'))) return json({ ok: false, error: 'not_ready' }, 200, h);
  if (!(await classOf(env, token))) return json({ ok: false, error: 'unknown_token' }, 200, h);
  const photo = form.get('photo');
  if (!photo || typeof photo === 'string') return json({ ok: false, error: 'no_photo' }, 400, h);
  const ext = TYPES[(photo.type || '').toLowerCase()];
  if (!ext) return json({ ok: false, error: 'bad_type' }, 415, h);
  if (photo.size > MAX_BYTES) return json({ ok: false, error: 'too_big' }, 413, h);
  const rl = `rl:${token}:${Math.floor(now / 3600e3)}`;
  if ((await peek(env, rl)) >= num(env.RATE_PER_HOUR, 10)) return json({ ok: false, error: 'rate_limited' }, 429, h);
  await bump(env, rl, day);

  const prev = await one(env, 'SELECT * FROM subs WHERE token = ? AND day = ?', token, day);
  let key;
  if (env.PHOTO_STORE === 'r2') {
    key = `${day}/${token}.${ext}`;
    if (prev && prev.key && prev.key !== key) await env.PHOTOS.delete(prev.key);
    await env.PHOTOS.put(key, photo.stream(), { httpMetadata: { contentType: photo.type } });
  } else {
    key = `photo:${day}:${token}`;
    await env.SUBMISSIONS.put(key, await photo.arrayBuffer(), { expirationTtl: num(env.PHOTO_TTL_DAYS, 7) * DAY, metadata: { type: photo.type } });
  }
  const set = String(form.get('set') || '').replace(/[^\w-]/g, '').slice(0, 80);
  const rawLine = form.get('line');
  const line = rawLine === null ? null : String(rawLine).replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 40);
  const chipIn = form.get('chip');
  const chip = chipIn === null ? null : (CHIPS.has(String(chipIn)) ? String(chipIn) : '');
  // F13.5: 오늘 푼 문제 수 (optional, self-reported, a whole number 0-60; anything else is ignored)
  const cntIn = String(form.get('count') ?? '').trim();
  const solved = /^\d{1,2}$/.test(cntIn) && Number(cntIn) <= 60 ? Number(cntIn) : null;
  // one row per token per day; a replacement keeps the first ts (and the earlier line/chip/count when none is sent)
  await run(env, `INSERT INTO subs (token, day, set_id, ts, updated, key, n, line, chip, solved) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
    ON CONFLICT (token, day) DO UPDATE SET updated = excluded.updated, key = excluded.key, n = subs.n + 1,
      set_id = COALESCE(NULLIF(excluded.set_id, ''), subs.set_id),
      line = CASE WHEN ? IS NULL THEN subs.line ELSE excluded.line END,
      chip = CASE WHEN ? IS NULL THEN subs.chip ELSE excluded.chip END,
      solved = COALESCE(excluded.solved, subs.solved)`,
  token, day, set, iso, iso, key, line ?? '', chip ?? '', solved, line, chip);
  const days = await recentDays(env, token, day);
  return json({ ok: true, ts: prev ? prev.ts : iso, day, already: !!prev, days }, 200, h);
}

// ---------------------------------------------------------------- 주간 테스트 report (F14)
const clean = (v) => String(v ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim();
// "3,7,12" -> [3, 7, 12]: whole numbers 1..n, no repeats; null when anything is off
function numList(v, n) {
  const s = clean(v);
  if (!s) return [];
  const out = [];
  for (const part of s.split(',')) {
    const p = part.trim();
    if (!/^\d{1,3}$/.test(p)) return null;
    const k = Number(p);
    if (k < 1 || k > n || out.includes(k)) return null;
    out.push(k);
  }
  return out.sort((a, b) => a - b);
}

async function report(req, env, h) {
  const now = Date.now(), day = seoulDay(now), iso = new Date(now).toISOString();
  let form;
  try { form = await req.formData(); } catch { return json({ ok: false, error: 'bad_form' }, 400, h); }
  const token = String(form.get('t') || '').toLowerCase(), rid = String(form.get('round') || '').toLowerCase();
  if (!TOKEN_RE.test(token)) return json({ ok: false, error: 'bad_token' }, 400, h);
  if (!RID_RE.test(rid)) return json({ ok: false, error: 'bad_round' }, 200, h);
  const rl = `rr:${token}:${Math.floor(now / 3600e3)}`;
  if ((await peek(env, rl)) >= num(env.REPORTS_PER_HOUR, 30)) return json({ ok: false, error: 'rate_limited' }, 200, h);
  await bump(env, rl, day);
  const round = await one(env, 'SELECT * FROM rounds WHERE rid = ?', rid);
  if (!round) return json({ ok: false, error: 'unknown_round' }, 200, h);
  const cls = await classOf(env, token);
  if (!cls) return json({ ok: false, error: 'unknown_token' }, 200, h);
  if (!round.classes.split(',').includes(cls)) return json({ ok: false, error: 'not_your_round' }, 200, h);
  if (now < Date.parse(round.open_from) || now > Date.parse(round.open_until)) return json({ ok: false, error: 'closed' }, 200, h);
  const incorrect = numList(form.get('incorrect'), round.n), missed = numList(form.get('missed'), round.n);
  const hardest = numList(form.get('hardest'), round.n);
  if (!incorrect || !missed || !hardest || hardest.length > 3 || incorrect.some((k) => missed.includes(k))) {
    return json({ ok: false, error: 'bad_numbers' }, 200, h);
  }
  const rs = clean(form.get('reasons'));
  const reasons = rs ? rs.split(',').map((x) => x.trim()) : [];
  if (reasons.some((r) => !REASONS.includes(r)) || new Set(reasons).size !== reasons.length) return json({ ok: false, error: 'bad_reason' }, 200, h);
  if ((incorrect.length || missed.length) && !reasons.length) return json({ ok: false, error: 'need_reason' }, 200, h);
  const wishlist = clean(form.get('wishlist')), metaphor = clean(form.get('metaphor'));
  if ([...wishlist].length > 100 || [...metaphor].length > 30) return json({ ok: false, error: 'too_long' }, 200, h);
  // one row per round and student: an edit replaces the earlier report (the first time is kept)
  await run(env, `INSERT INTO reports (rid, token, class, incorrect, missed, hardest, reasons, wishlist, metaphor, ts, updated, n)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
    ON CONFLICT (rid, token) DO UPDATE SET incorrect = excluded.incorrect, missed = excluded.missed, hardest = excluded.hardest,
      reasons = excluded.reasons, wishlist = excluded.wishlist, metaphor = excluded.metaphor, updated = excluded.updated,
      n = reports.n + 1, class = excluded.class`,
  rid, token, cls, incorrect.join(','), missed.join(','), hardest.join(','), reasons.join(','), wishlist, metaphor, iso, iso);
  return json({ ok: true, updated: iso, until: round.open_until }, 200, h);
}

async function putRounds(req, env) {
  let b;
  try { b = await req.json(); } catch { return json({ ok: false, error: 'bad_json' }, 400, {}); }
  if (!b || !Array.isArray(b.rounds)) return json({ ok: false, error: 'bad_rounds' }, 400, {});
  const rows = b.rounds.filter((r) => r && RID_RE.test(r.rid || '') && Array.isArray(r.classes) && r.classes.length
    && r.classes.every((c) => CLASS_RE.test(String(c))) && Number.isInteger(r.n) && r.n >= 1 && r.n <= 60
    && !Number.isNaN(Date.parse(r.open_from)) && !Number.isNaN(Date.parse(r.open_until)));
  const stmts = [env.DB.prepare('DELETE FROM rounds')];
  for (const r of rows) {
    stmts.push(env.DB.prepare('INSERT INTO rounds (rid, classes, n, open_from, open_until, label) VALUES (?, ?, ?, ?, ?, ?)')
      .bind(r.rid, r.classes.join(','), r.n, new Date(r.open_from).toISOString(), new Date(r.open_until).toISOString(), clean(r.label).slice(0, 40)));
  }
  await env.DB.batch(stmts);
  return json({ ok: true, n: rows.length }, 200, {});
}

// the open rounds of this token's class, with this student's own report (to edit it) - never anyone else's
async function openRounds(env, token, now) {
  const iso = new Date(now).toISOString();
  const rows = await all(env, `SELECT r.rid, r.n, r.label, r.open_until, p.incorrect, p.missed, p.hardest, p.reasons, p.wishlist,
      p.metaphor, p.updated FROM rounds r JOIN tokmap t ON t.token = ? AND instr(',' || r.classes || ',', ',' || t.class || ',') > 0
    LEFT JOIN reports p ON p.rid = r.rid AND p.token = t.token
    WHERE r.open_from <= ? AND r.open_until >= ? ORDER BY r.open_until`, token, iso, iso);
  const split = (v) => (v ? String(v).split(',').filter(Boolean) : []);
  return rows.map((r) => ({ r: r.rid, n: r.n, label: r.label || '', until: r.open_until,
    mine: r.updated ? { incorrect: split(r.incorrect).map(Number), missed: split(r.missed).map(Number), hardest: split(r.hardest).map(Number),
      reasons: split(r.reasons), wishlist: r.wishlist || '', metaphor: r.metaphor || '', updated: r.updated } : null }));
}

// ---------------------------------------------------------------- F15.1 watch signals
async function watch(req, env, h) {
  const now = Date.now(), day = seoulDay(now), iso = new Date(now).toISOString();
  let form;
  try { form = await req.formData(); } catch { return json({ ok: false, error: 'bad_form' }, 400, h); }
  const token = String(form.get('t') || '').toLowerCase(), v = String(form.get('v') || '');
  if (!TOKEN_RE.test(token)) return json({ ok: false, error: 'bad_token' }, 400, h);
  const sec = Number(form.get('sec')), pct = Number(form.get('pct'));
  if (!VID_RE.test(v) || !Number.isInteger(sec) || sec < 0 || sec > 36000 || !Number.isInteger(pct) || pct < 0 || pct > 100) {
    return json({ ok: false, error: 'bad_watch' }, 200, h);
  }
  const rl = `rw:${token}:${Math.floor(now / 3600e3)}`;
  if ((await peek(env, rl)) >= num(env.WATCHES_PER_HOUR, 60)) return json({ ok: false, error: 'rate_limited' }, 200, h);
  const cls = await classOf(env, token);
  if (!cls) return json({ ok: false, error: 'unknown_token' }, 200, h);
  await bump(env, rl, day);
  // the largest value wins: a second viewing the same day never adds up, a short one never lowers it
  await run(env, `INSERT INTO watches (token, v, day, class, sec, pct, ts, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (token, v, day) DO UPDATE SET sec = MAX(watches.sec, excluded.sec), pct = MAX(watches.pct, excluded.pct),
      updated = excluded.updated, class = excluded.class`, token, v, day, cls, sec, pct, iso, iso);
  return json({ ok: true }, 200, h);
}

// ---------------------------------------------------------------- F15.3 Web Push (opt-in)
async function pushSubscribe(req, env, h) {
  let form;
  try { form = await req.formData(); } catch { return json({ ok: false, error: 'bad_form' }, 400, h); }
  const token = String(form.get('t') || '').toLowerCase(), role = String(form.get('role') || 's');
  if (!TOKEN_RE.test(token)) return json({ ok: false, error: 'bad_token' }, 400, h);
  const endpoint = String(form.get('endpoint') || ''), p256dh = String(form.get('p256dh') || ''), auth = String(form.get('auth') || '');
  let ok = /^https:\/\/[^\s]{8,600}$/.test(endpoint) && (role === 's' || role === 'p');
  try { ok = ok && unb64(p256dh).length === 65 && unb64(p256dh)[0] === 4 && unb64(auth).length === 16; } catch { ok = false; }
  if (!ok) return json({ ok: false, error: 'bad_subscription' }, 200, h);
  const row = await one(env, `SELECT class FROM ${role === 's' ? 'tokmap' : 'ptokmap'} WHERE token = ?`, token);
  if (!row) return json({ ok: false, error: 'unknown_token' }, 200, h);
  const now = new Date().toISOString();
  await run(env, `INSERT INTO pushsubs (endpoint, token, role, class, p256dh, auth, kinds, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (endpoint) DO UPDATE SET token = excluded.token, role = excluded.role, class = excluded.class,
      p256dh = excluded.p256dh, auth = excluded.auth, kinds = excluded.kinds`, endpoint, token, role, row.class, p256dh, auth, DEFAULT_KINDS[role], now);
  // at most 5 browsers per link: the oldest go
  await run(env, `DELETE FROM pushsubs WHERE token = ? AND endpoint NOT IN
    (SELECT endpoint FROM pushsubs WHERE token = ? ORDER BY created DESC LIMIT 5)`, token, token);
  return json({ ok: true }, 200, h);
}

async function pushUnsubscribe(req, env, h) {
  let form;
  try { form = await req.formData(); } catch { return json({ ok: false, error: 'bad_form' }, 400, h); }
  const token = String(form.get('t') || '').toLowerCase();
  if (!TOKEN_RE.test(token)) return json({ ok: false, error: 'bad_token' }, 400, h);
  await run(env, 'DELETE FROM pushsubs WHERE token = ? AND endpoint = ?', token, String(form.get('endpoint') || ''));
  return json({ ok: true }, 200, h);
}

const unb64 = (s) => Uint8Array.from(atob(String(s).replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((String(s).length + 3) % 4)), (c) => c.charCodeAt(0));

function quietNow(env, now) {
  // KST quiet hours, "22-8" by default: no push is sent then (the console logs the skip)
  const [from, to] = String(env.PUSH_QUIET || '22-8').split('-').map(Number);
  const hour = new Date(now + 9 * 3600e3).getUTCHours();
  return from > to ? (hour >= from || hour < to) : (hour >= from && hour < to);
}

async function pushSendAll(req, env) {
  let b;
  try { b = await req.json(); } catch { return json({ ok: false, error: 'bad_json' }, 400, {}); }
  const kind = String((b && b.kind) || '');
  if (!PUSH_KINDS.includes(kind)) return json({ ok: false, error: 'bad_kind' }, 400, {});
  if (!env.VAPID_PUBLIC || !env.VAPID_PRIVATE || !env.VAPID_SUBJECT) return json({ ok: false, error: 'no_vapid' }, 200, {});
  const now = Date.now(), day = seoulDay(now);
  if (quietNow(env, now)) return json({ ok: true, quiet: true, sent: 0, more: false }, 200, {});
  const classes = Array.isArray(b.classes) ? b.classes.filter((c) => CLASS_RE.test(String(c))) : [];
  const cap = num(env.PUSH_PER_DAY, 1), batch = Math.min(num(env.PUSH_BATCH, 10), 40);   // free plan: few subrequests/CPU
  const where = `kinds LIKE ? ${classes.length ? `AND class IN (${classes.map(() => '?').join(',')})` : ''}`;
  const args = [`%${kind}%`, ...classes];
  const capped = (await one(env, `SELECT COUNT(*) AS n FROM pushsubs WHERE ${where} AND sent_day = ? AND sent_n >= ?`, ...args, day, cap)).n;
  const due = await all(env, `SELECT * FROM pushsubs WHERE ${where} AND (sent_day IS NULL OR sent_day != ? OR sent_n < ?)
    ORDER BY created LIMIT ?`, ...args, day, cap, batch + 1);
  const title = String(b.title || 'AMP 학습 기록').replace(/[\u0000-\u001f]/g, ' ').slice(0, 40);
  const body = String(b.body || '').replace(/[\u0000-\u001f]/g, ' ').slice(0, 120);
  const vapid = { publicKey: env.VAPID_PUBLIC, privateKey: env.VAPID_PRIVATE, subject: env.VAPID_SUBJECT };
  const fetchFn = env.PUSH_FETCH || fetch;
  let sent = 0, removed = 0, failed = 0;
  for (const sub of due.slice(0, batch)) {
    // tapping opens the subscriber's own page (the payload is end-to-end encrypted to that browser)
    const url = `${env.ALLOWED_ORIGIN}/${sub.role === 'p' ? 'p' : 's'}/${sub.token}/`;
    const status = await pushSend(sub, { title, body, url, kind }, vapid, fetchFn);
    if (status >= 200 && status < 300) {
      sent++;
      await run(env, `UPDATE pushsubs SET sent_n = CASE WHEN sent_day = ? THEN sent_n + 1 ELSE 1 END, sent_day = ? WHERE endpoint = ?`, day, day, sub.endpoint);
    } else if (status === 404 || status === 410) {
      removed++;                                     // the browser dropped it: forget it
      await run(env, 'DELETE FROM pushsubs WHERE endpoint = ?', sub.endpoint);
    } else failed++;
  }
  return json({ ok: true, quiet: false, sent, removed, failed, capped, more: due.length > batch }, 200, {});
}

// ---------------------------------------------------------------- 출석 (F2)
async function putSession(req, env) {
  let b;
  try { b = await req.json(); } catch { return json({ ok: false, error: 'bad_json' }, 400, {}); }
  const { class: cls, date, code, open_from: from, open_until: until, start } = b || {};
  if (!CLASS_RE.test(cls || '') || !DAY_RE.test(date || '') || !CODE_RE.test(code || '') ||
      Number.isNaN(Date.parse(from)) || Number.isNaN(Date.parse(until))) return json({ ok: false, error: 'bad_session' }, 400, {});
  if (b.tokmap && typeof b.tokmap === 'object') await replaceTokmap(env, b.tokmap, b.parents);
  await run(env, `INSERT INTO sessions (class, date, code, open_from, open_until, start) VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT (class, date) DO UPDATE SET code = excluded.code, open_from = excluded.open_from,
      open_until = excluded.open_until, start = excluded.start`,
  cls, date, code, new Date(from).toISOString(), new Date(until).toISOString(), String(start || '').slice(0, 5));
  return json({ ok: true }, 200, {});
}

async function replaceTokmap(env, map, parents) {
  const rows = Object.entries(map).filter(([t, c]) => TOKEN_RE.test(t) && CLASS_RE.test(String(c)));
  const stmts = [env.DB.prepare('DELETE FROM tokmap')];
  for (const [t, c] of rows) stmts.push(env.DB.prepare('INSERT INTO tokmap (token, class) VALUES (?, ?)').bind(t, String(c)));
  if (parents && typeof parents === 'object') {          // F15.3: parent links (push opt-in only; they never upload)
    stmts.push(env.DB.prepare('DELETE FROM ptokmap'));
    for (const [t, c] of Object.entries(parents).filter(([t, c]) => TOKEN_RE.test(t) && CLASS_RE.test(String(c)))) {
      stmts.push(env.DB.prepare('INSERT INTO ptokmap (token, class) VALUES (?, ?)').bind(t, String(c)));
    }
  }
  // a retired or rotated link stops getting pushes; a moved student's subscription follows the new class
  stmts.push(env.DB.prepare("DELETE FROM pushsubs WHERE role = 's' AND token NOT IN (SELECT token FROM tokmap)"));
  stmts.push(env.DB.prepare("UPDATE pushsubs SET class = (SELECT class FROM tokmap WHERE tokmap.token = pushsubs.token) WHERE role = 's'"));
  if (parents && typeof parents === 'object') {
    stmts.push(env.DB.prepare("DELETE FROM pushsubs WHERE role = 'p' AND token NOT IN (SELECT token FROM ptokmap)"));
    stmts.push(env.DB.prepare("UPDATE pushsubs SET class = (SELECT class FROM ptokmap WHERE ptokmap.token = pushsubs.token) WHERE role = 'p'"));
  }
  await env.DB.batch(stmts);
  return rows.length;
}

async function checkin(req, env, h) {
  const now = Date.now(), day = seoulDay(now);
  let form;
  try { form = await req.formData(); } catch { return json({ ok: false, error: 'bad_form' }, 400, h); }
  const token = String(form.get('t') || '').toLowerCase(), code = String(form.get('code') || '');
  if (!TOKEN_RE.test(token)) return json({ ok: false, error: 'bad_token' }, 400, h);
  const done = await one(env, 'SELECT ts FROM checkins WHERE token = ? AND day = ?', token, day);
  if (done) return json({ ok: true, already: true, ts: done.ts, day }, 200, h);
  // refusals are answers, not faults: 200 + ok:false (a 4xx would show as an error in the student's console)
  const failKey = `ckfail:${day}:${token}`, max = num(env.CHECKIN_FAILS_PER_DAY, 5);
  const fails = await peek(env, failKey);
  if (fails >= max) return json({ ok: false, error: 'rate_limited' }, 200, h);
  const cls = await classOf(env, token);
  const sess = cls ? await one(env, 'SELECT * FROM sessions WHERE class = ? AND date = ?', cls, day) : null;
  if (!sess) return json({ ok: false, error: 'no_class_today' }, 200, h);
  if (now < Date.parse(sess.open_from) || now > Date.parse(sess.open_until)) return json({ ok: false, error: 'closed' }, 200, h);
  if (!CODE_RE.test(code) || code !== sess.code) {
    const n = await bump(env, failKey, day);
    return json({ ok: false, error: 'bad_code', left: Math.max(0, max - n) }, 200, h);
  }
  const ts = new Date(now).toISOString();
  await run(env, 'INSERT OR IGNORE INTO checkins (token, day, class, ts) VALUES (?, ?, ?, ?)', token, day, cls, ts);
  const row = await one(env, 'SELECT ts FROM checkins WHERE token = ? AND day = ?', token, day);
  return json({ ok: true, already: row.ts !== ts, ts: row.ts, day }, 200, h);
}

// ---------------------------------------------------------------- 오늘의 장면 (F4)
async function putMoment(req, env) {
  let b;
  try { b = await req.json(); } catch { return json({ ok: false, error: 'bad_json' }, 400, {}); }
  const { class: cls, date, n, text, image } = b || {};
  const k = `${cls}:${date}:${n}`;
  if (!MKEY_RE.test(k) || typeof image !== 'string' || image.length > 300000) return json({ ok: false, error: 'bad_moment' }, 400, {});
  let bytes;
  try { bytes = Uint8Array.from(atob(image), (c) => c.charCodeAt(0)); } catch { return json({ ok: false, error: 'bad_image' }, 400, {}); }
  if (bytes.length > 200 * 1024 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return json({ ok: false, error: 'bad_image' }, 400, {});
  await env.SUBMISSIONS.put(`moment:${k}`, bytes.buffer, { expirationTtl: 14 * DAY });
  await run(env, `INSERT INTO moments (k, class, date, n, text, ts) VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT (k) DO UPDATE SET text = excluded.text, ts = excluded.ts`, k, cls, date, Number(n), String(text || '').slice(0, 60), new Date().toISOString());
  return json({ ok: true, k }, 200, {});
}

async function listMoments(env, cls, token) {
  const since = addDays(seoulDay(Date.now()), -13);
  const ms = await all(env, 'SELECT k, date, n, text, ts FROM moments WHERE class = ? AND date >= ? ORDER BY date, n', cls, since);
  if (!ms.length) return [];
  const counts = await all(env, `SELECT r.k, r.r, COUNT(*) AS c FROM reactions r JOIN moments m ON m.k = r.k
    WHERE m.class = ? AND m.date >= ? GROUP BY r.k, r.r`, cls, since);
  const mine = token ? await all(env, `SELECT r.k, r.r FROM reactions r JOIN moments m ON m.k = r.k
    WHERE m.class = ? AND m.date >= ? AND r.token = ?`, cls, since, token) : [];
  return ms.map((m) => {
    const c = { fire: 0, clap: 0, mind: 0 };
    for (const x of counts) if (x.k === m.k) c[x.r] = x.c;
    const my = mine.find((x) => x.k === m.k);
    return { ...m, counts: c, ...(token ? { mine: my ? my.r : null } : {}) };
  });
}

async function react(req, env, h) {
  let form;
  try { form = await req.formData(); } catch { return json({ ok: false, error: 'bad_form' }, 400, h); }
  const token = String(form.get('t') || '').toLowerCase(), k = String(form.get('k') || ''), r = String(form.get('r') || '');
  if (!TOKEN_RE.test(token) || !MKEY_RE.test(k) || !REACTIONS.includes(r)) return json({ ok: false, error: 'bad_request' }, 400, h);
  const cls = await classOf(env, token);
  if (!cls || !k.startsWith(cls + ':') || !(await one(env, 'SELECT 1 AS x FROM moments WHERE k = ?', k))) return json({ ok: false, error: 'not_found' }, 404, h);
  // H2: one row per student per moment (INSERT OR IGNORE), totals by COUNT: no shared counter to fight over
  const ins = await run(env, 'INSERT OR IGNORE INTO reactions (k, token, r, ts) VALUES (?, ?, ?, ?)', k, token, r, new Date().toISOString());
  const mine = (await one(env, 'SELECT r FROM reactions WHERE k = ? AND token = ?', k, token)).r;
  const c = { fire: 0, clap: 0, mind: 0 };
  for (const x of await all(env, 'SELECT r, COUNT(*) AS c FROM reactions WHERE k = ? GROUP BY r', k)) c[x.r] = x.c;
  return json({ ok: true, mine, already: !(ins.meta && ins.meta.changes), counts: c }, 200, h);
}


// ---------------------------------------------------------------- AMP Live (F7)
// One Durable Object per room (SQLite storage, free plan). Phones and the projector poll every 2 s, only while a room is
// open. No question text ever reaches the Worker: the room knows only, per question, its kind and the answer key.
const ROOM_RE = /^\d{4}$/;
const LIVE_MINUTES = 90;

function roomStub(env, code) { return env.LIVE.get(env.LIVE.idFromName(`room:${code}`)); }
async function roomCall(env, code, op, body) {
  const r = await roomStub(env, code).fetch(new Request(`https://room/${op}`, { method: 'POST', body: JSON.stringify(body || {}) }));
  return r.json();
}

async function liveOpen(req, env) {
  let b;
  try { b = await req.json(); } catch { return json({ ok: false, error: 'bad_json' }, 400, {}); }
  const qs = Array.isArray(b && b.questions) ? b.questions : [];
  if (!CLASS_RE.test((b && b.class) || '') || !qs.length || qs.length > 30 ||
      !qs.every((q) => (q.kind === 'choice' && /^[1-5]$/.test(String(q.answer))) || (q.kind === 'num' && /^-?\d{1,6}$/.test(String(q.answer))))) {
    return json({ ok: false, error: 'bad_room' }, 400, {});
  }
  const size = (await one(env, 'SELECT COUNT(*) AS n FROM tokmap WHERE class = ?', b.class)).n;
  const now = Date.now(), day = seoulDay(now), closes = new Date(now + LIVE_MINUTES * 60e3).toISOString();
  let code = null;
  for (let i = 0; i < 20 && !code; i++) {
    const c = String(Math.floor(Math.random() * 10000)).padStart(4, '0');
    const r = await run(env, `INSERT INTO live_rooms (code, class, date, status, closes_at) VALUES (?, ?, ?, 'open', ?)
      ON CONFLICT (code) DO UPDATE SET class = excluded.class, date = excluded.date, status = 'open', closes_at = excluded.closes_at
      WHERE live_rooms.status != 'open' OR live_rooms.closes_at < ?`, c, b.class, day, closes, new Date(now).toISOString());
    if (r.meta && r.meta.changes) code = c;
  }
  if (!code) return json({ ok: false, error: 'busy' }, 503, {});
  await roomCall(env, code, 'open', { code, class: b.class, date: day, size,
    questions: qs.map((q) => ({ kind: q.kind, answer: String(q.answer) })), closes_at: closes });
  return json({ ok: true, room: code, size, closes_at: closes }, 200, {});
}

async function liveStudent(req, env, url, h, op) {
  const form = op === 'state' ? url.searchParams : await req.formData().catch(() => null);
  if (!form) return json({ ok: false, error: 'bad_form' }, 400, h);
  const token = String(form.get('t') || '').toLowerCase(), code = String(form.get('room') || '');
  if (!TOKEN_RE.test(token) || !ROOM_RE.test(code)) return json({ ok: false, error: 'bad_request' }, 400, h);
  const cls = await classOf(env, token);
  const room = await one(env, "SELECT class, closes_at FROM live_rooms WHERE code = ? AND status = 'open'", code);
  if (!cls || !room || room.class !== cls || Date.parse(room.closes_at) < Date.now()) return json({ ok: false, error: 'no_room', open: false }, 200, h);
  const body = { token };
  if (op === 'answer') { body.q = Number(form.get('q')); body.a = String(form.get('a') || ''); }
  return json(await roomCall(env, code, op, body), 200, h);
}

async function liveTeacher(req, env, url, op) {
  const code = op === 'state' ? url.searchParams.get('room') : (await req.clone().json().catch(() => ({}))).room;
  if (!ROOM_RE.test(String(code || ''))) return json({ ok: false, error: 'bad_room' }, 400, {});
  const body = op === 'state' ? {} : await req.json().catch(() => ({}));
  const r = await roomCall(env, code, op === 'state' ? 'teacher' : op, body);
  if (op === 'close') await run(env, "UPDATE live_rooms SET status = 'closed' WHERE code = ?", code);
  return json(r, 200, {});
}

export class LiveRoom {
  constructor(ctx, env) {
    this.ctx = ctx; this.env = env; this.sql = ctx.storage.sql;
    this.schema();
  }
  schema() {
    this.sql.exec('CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)');
    this.sql.exec('CREATE TABLE IF NOT EXISTS members (token TEXT PRIMARY KEY, ts TEXT NOT NULL)');
    this.sql.exec(`CREATE TABLE IF NOT EXISTS answers (token TEXT NOT NULL, q INTEGER NOT NULL, a TEXT NOT NULL,
      correct INTEGER NOT NULL, ts TEXT NOT NULL, PRIMARY KEY (token, q))`);
  }
  get(k) { const r = this.sql.exec('SELECT v FROM meta WHERE k = ?', k).toArray()[0]; return r ? JSON.parse(r.v) : null; }
  set(k, v) { this.sql.exec('INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v', k, JSON.stringify(v)); }
  counts(q) {
    const r = this.sql.exec('SELECT COUNT(*) AS answered, COALESCE(SUM(correct), 0) AS correct FROM answers WHERE q = ?', q).toArray()[0];
    return { answered: r.answered, correct: r.correct };
  }
  view(token) {
    const st = this.get('state');
    if (!st || st.closed) return { ok: true, open: false };
    const qs = this.get('questions'), q = st.q, joined = this.sql.exec('SELECT COUNT(*) AS n FROM members').toArray()[0].n;
    const c = q ? this.counts(q) : { answered: 0, correct: 0 };
    const out = { ok: true, open: true, q, n: qs.length, kind: q ? qs[q - 1].kind : null, accepting: st.accepting,
      revealed: st.revealed, size: st.size, joined, answered: c.answered, correct: st.revealed ? c.correct : null };
    if (token) {
      const mine = q ? this.sql.exec('SELECT correct FROM answers WHERE token = ? AND q = ?', token, q).toArray()[0] : null;
      out.mine = { joined: !!this.sql.exec('SELECT 1 AS x FROM members WHERE token = ?', token).toArray()[0], answered: !!mine,
        correct: mine && st.revealed ? !!mine.correct : null };
    }
    return out;
  }
  async fetch(req) {
    const op = new URL(req.url).pathname.slice(1), b = await req.json().catch(() => ({}));
    const st = this.get('state');
    if (op === 'open') {
      this.sql.exec('DELETE FROM members'); this.sql.exec('DELETE FROM answers');
      this.set('questions', b.questions);
      this.set('state', { code: b.code, class: b.class, date: b.date, size: b.size, q: 0, accepting: false, revealed: false, closed: false });
      await this.ctx.storage.setAlarm(Date.parse(b.closes_at));
      return Response.json({ ok: true });
    }
    if (!st || st.closed) return Response.json({ ok: false, error: 'no_room', open: false });
    if (op === 'join') {
      this.sql.exec('INSERT OR IGNORE INTO members (token, ts) VALUES (?, ?)', b.token, new Date().toISOString());
      return Response.json(this.view(b.token));
    }
    if (op === 'state') return Response.json(this.view(b.token));
    if (op === 'teacher') return Response.json(this.view(null));
    if (op === 'answer') {
      const qs = this.get('questions');
      if (!st.accepting || b.q !== st.q) return Response.json({ ...this.view(b.token), ok: false, error: 'closed' });
      this.sql.exec('INSERT OR IGNORE INTO members (token, ts) VALUES (?, ?)', b.token, new Date().toISOString());
      const right = String(b.a).trim().replace(/^\+/, '') === qs[st.q - 1].answer ? 1 : 0;
      this.sql.exec('INSERT OR IGNORE INTO answers (token, q, a, correct, ts) VALUES (?, ?, ?, ?, ?)',
        b.token, st.q, String(b.a).slice(0, 8), right, new Date().toISOString());   // once per question: the first answer stays
      return Response.json(this.view(b.token));
    }
    if (op === 'next') {
      const qs = this.get('questions'), q = Number(b.q) || st.q + 1;
      if (q < 1 || q > qs.length) return Response.json({ ok: false, error: 'no_question' });
      this.set('state', { ...st, q, accepting: true, revealed: false });
      return Response.json(this.view(null));
    }
    if (op === 'reveal') { this.set('state', { ...st, accepting: false, revealed: true }); return Response.json(this.view(null)); }
    if (op === 'close') return Response.json(await this.close());
    return Response.json({ ok: false, error: 'op' });
  }
  async close() {
    const st = this.get('state');
    if (!st || st.closed) return { ok: true, closed: true, results: 0 };
    // keep nothing but per-student counts (in D1, for `daybook pull` -> effort, source 'live')
    const rows = this.sql.exec(`SELECT m.token, COUNT(a.q) AS answered, COALESCE(SUM(a.correct), 0) AS correct
      FROM members m LEFT JOIN answers a ON a.token = m.token GROUP BY m.token`).toArray();
    const ts = new Date().toISOString();
    if (rows.length) {
      await this.env.DB.batch(rows.map((r) => this.env.DB.prepare(`INSERT INTO live_results (room, date, class, token, answered, correct, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (room, date, token) DO UPDATE SET answered = excluded.answered,
        correct = excluded.correct, ts = excluded.ts`).bind(st.code, st.date, st.class, r.token, r.answered, r.correct, ts)));
    }
    await this.env.DB.prepare("UPDATE live_rooms SET status = 'closed' WHERE code = ?").bind(st.code).run();
    await this.ctx.storage.deleteAll();     // the answers are gone; only the counts above remain (in D1)
    this.schema();
    this.set('state', { closed: true });
    return { ok: true, closed: true, results: rows.length };
  }
  async alarm() { await this.close(); }   // 90 minutes after opening
}

// ---------------------------------------------------------------- teacher reads
async function getPhoto(url, env) {
  const key = url.searchParams.get('key') || '';
  if (!/^(photo:\d{4}-\d{2}-\d{2}:[a-z2-7]{26}|\d{4}-\d{2}-\d{2}\/[a-z2-7]{26}\.(jpg|png|heic))$/.test(key)) return json({ ok: false, error: 'bad_key' }, 400, {});
  if (key.startsWith('photo:')) {
    const { value, metadata } = await env.SUBMISSIONS.getWithMetadata(key, 'arrayBuffer');
    if (!value) return json({ ok: false, error: 'not_found' }, 404, {});
    return new Response(value, { headers: { 'Content-Type': (metadata && metadata.type) || 'image/jpeg', 'Cache-Control': 'no-store' } });
  }
  const obj = env.PHOTOS ? await env.PHOTOS.get(key) : null;
  if (!obj) return json({ ok: false, error: 'not_found' }, 404, {});
  return new Response(obj.body, { headers: { 'Content-Type': obj.httpMetadata?.contentType || 'image/jpeg', 'Cache-Control': 'no-store' } });
}

const SUB_COLS = 'token, day, set_id AS "set", ts, updated, key, n, line, chip, solved';

async function route(req, env, url, h) {
  const p = url.pathname;
  if (p === '/submit' && req.method === 'POST') return submit(req, env, h);
  if (p === '/checkin' && req.method === 'POST') return checkin(req, env, h);
  if (p === '/react' && req.method === 'POST') return react(req, env, h);
  if (p === '/report' && req.method === 'POST') return report(req, env, h);
  if (p === '/watch' && req.method === 'POST') return watch(req, env, h);
  if (p === '/push/subscribe' && req.method === 'POST') return pushSubscribe(req, env, h);
  if (p === '/push/unsubscribe' && req.method === 'POST') return pushUnsubscribe(req, env, h);
  if (p === '/live/join' && req.method === 'POST') return liveStudent(req, env, url, h, 'join');
  if (p === '/live/answer' && req.method === 'POST') return liveStudent(req, env, url, h, 'answer');
  if (p === '/live/state' && req.method === 'GET' && url.searchParams.get('t')) return liveStudent(req, env, url, h, 'state');
  if (p === '/status' && req.method === 'GET') {
    const token = (url.searchParams.get('t') || '').toLowerCase();
    if (!TOKEN_RE.test(token)) return json({ ok: false, error: 'bad_token' }, 400, h);
    const day = seoulDay(Date.now());
    const [days, ck, sess, rounds] = await Promise.all([
      recentDays(env, token, day),
      one(env, 'SELECT ts FROM checkins WHERE token = ? AND day = ?', token, day),
      one(env, 'SELECT s.start FROM sessions s JOIN tokmap t ON t.class = s.class WHERE t.token = ? AND s.date = ?', token, day),
      openRounds(env, token, Date.now()),
    ]);
    return json({ ok: true, day, days, checkin_ts: ck ? ck.ts : null, class_today: !!sess, start: sess ? sess.start : null, rounds }, 200, h);
  }
  if ((p === '/moments' || p === '/moment') && req.method === 'GET') {
    const token = (url.searchParams.get('t') || '').toLowerCase();
    let cls = null;
    if (TOKEN_RE.test(token)) {
      cls = await classOf(env, token);
      if (p === '/moments') return json({ ok: true, items: cls ? await listMoments(env, cls, token) : [] }, 200, h);
    } else if (teacherOk(req, env) && CLASS_RE.test(url.searchParams.get('class') || '')) {
      cls = url.searchParams.get('class');
      if (p === '/moments') return json({ ok: true, items: await listMoments(env, cls, null) }, 200, {});
    }
    const k = url.searchParams.get('k') || '';
    if (!cls || !MKEY_RE.test(k) || !k.startsWith(cls + ':')) return json({ ok: false, error: 'not_found' }, 404, h);
    const img = await env.SUBMISSIONS.get(`moment:${k}`, 'arrayBuffer');
    if (!img) return json({ ok: false, error: 'not_found' }, 404, h);
    return new Response(img, { headers: { ...h, 'Content-Type': 'image/jpeg', 'Cache-Control': 'private, max-age=600' } });
  }
  const teacherPaths = ['/submissions', '/photo', '/checkins', '/session', '/moment-put', '/tokmap',
    '/live/open', '/live/next', '/live/reveal', '/live/close', '/live/state', '/live/results', '/rounds', '/reports', '/tokmap-info', '/watches', '/push/send', '/push/count'];
  if (teacherPaths.includes(p)) {
    if (!teacherOk(req, env)) return json({ ok: false, error: 'unauthorized' }, 401, {});
    const since = url.searchParams.get('since') || '';
    if (p === '/submissions' && req.method === 'GET') {
      const items = await all(env, `SELECT ${SUB_COLS} FROM subs WHERE updated > ? ORDER BY updated`, since);
      return json({ now: new Date().toISOString(), items }, 200, {});
    }
    if (p === '/checkins' && req.method === 'GET') {
      const items = await all(env, 'SELECT token, class, day, ts FROM checkins WHERE ts > ? ORDER BY ts', since);
      return json({ now: new Date().toISOString(), items }, 200, {});
    }
    if (p === '/photo' && req.method === 'GET') return getPhoto(url, env);
    if (p === '/live/open' && req.method === 'POST') return liveOpen(req, env);
    if (p === '/live/state' && req.method === 'GET') return liveTeacher(req, env, url, 'state');
    if (['/live/next', '/live/reveal', '/live/close'].includes(p) && req.method === 'POST') return liveTeacher(req, env, url, p.slice(6));
    if (p === '/live/results' && req.method === 'GET') {
      const items = await all(env, 'SELECT room, date, class, token, answered, correct, ts FROM live_results WHERE ts > ? ORDER BY ts', since);
      return json({ now: new Date().toISOString(), items }, 200, {});
    }
    if (p === '/reports' && req.method === 'GET') {
      const items = await all(env, `SELECT rid, token, incorrect, missed, hardest, reasons, wishlist, metaphor, ts, updated, n
        FROM reports WHERE updated > ? ORDER BY updated`, since);
      return json({ now: new Date().toISOString(), items }, 200, {});
    }
    if (p === '/push/send' && req.method === 'POST') return pushSendAll(req, env);
    if (p === '/push/count' && req.method === 'GET') {
      // how many browsers are subscribed, per class key and role: never who
      const rows = await all(env, 'SELECT class, role, COUNT(*) AS n FROM pushsubs GROUP BY class, role');
      const by = {};
      for (const x of rows) { by[x.class] = by[x.class] || { s: 0, p: 0 }; by[x.class][x.role] = x.n; }
      return json({ ok: true, students: rows.filter((x) => x.role === 's').reduce((n, x) => n + x.n, 0),
        parents: rows.filter((x) => x.role === 'p').reduce((n, x) => n + x.n, 0), by_class: by,
        vapid: !!(env.VAPID_PUBLIC && env.VAPID_PRIVATE && env.VAPID_SUBJECT) }, 200, {});
    }
    if (p === '/watches' && req.method === 'GET') {
      const items = await all(env, 'SELECT token, v, day, sec, pct, ts, updated FROM watches WHERE updated > ? ORDER BY updated', since);
      return json({ now: new Date().toISOString(), items }, 200, {});
    }
    if (p === '/rounds' && req.method === 'PUT') return putRounds(req, env);
    if (p === '/tokmap-info' && req.method === 'GET') {
      // G1: the count and a hash of the token map, so the console can compare it with the roster (never the tokens)
      const rows = await all(env, 'SELECT token, class FROM tokmap ORDER BY token');
      const text = rows.map((r) => `${r.token}:${r.class}`).join('\n');
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
      const hash = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 16);
      return json({ ok: true, n: rows.length, hash }, 200, {});
    }
    if (p === '/session' && req.method === 'PUT') return putSession(req, env);
    if (p === '/moment-put' && req.method === 'PUT') return putMoment(req, env);
    if (p === '/tokmap' && req.method === 'PUT') {
      let b;
      try { b = await req.json(); } catch { return json({ ok: false, error: 'bad_json' }, 400, {}); }
      if (!b || typeof b.tokmap !== 'object') return json({ ok: false, error: 'bad_tokmap' }, 400, {});
      return json({ ok: true, n: await replaceTokmap(env, b.tokmap, b.parents) }, 200, {});
    }
    return json({ ok: false, error: 'method' }, 405, {});
  }
  if (p === '/' || p === '/health') {
    // G1 doctor: the version and a cheap D1 read (never any record)
    let d1 = false;
    try { d1 = !!(await one(env, 'SELECT 1 AS x')); } catch { d1 = false; }
    return json({ ok: true, version: WORKER_VERSION, d1 }, 200, h);
  }
  return json({ ok: false, error: 'not_found' }, 404, h);
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const h = corsHeaders(req, env);
    const origin = req.headers.get('Origin');
    const isStudent = ['/submit', '/status', '/checkin', '/moments', '/moment', '/react', '/report', '/watch', '/push/subscribe', '/push/unsubscribe', '/live/join', '/live/answer'].includes(url.pathname)
      || (url.pathname === '/live/state' && url.searchParams.has('t'));
    // CORS: browsers may only call the student endpoints from the Pages origin.
    if (isStudent && origin && origin !== env.ALLOWED_ORIGIN) return json({ ok: false, error: 'origin' }, 403, h);
    if (req.method === 'OPTIONS') return new Response(null, { status: isStudent ? 204 : 404, headers: h });
    try {
      await init(env);
      return await route(req, env, url, h);
    } catch (e) {
      // storage trouble (limits, a busy row): an answer, never a 500 for a student
      return json({ ok: false, error: 'storage' }, isStudent ? 200 : 503, isStudent ? h : {});
    }
  },

  // daily cleanup (wrangler.toml [triggers]): records live RECORD_TTL_DAYS, counters 2 days
  async scheduled(_event, env) {
    await init(env);
    const day = seoulDay(Date.now()), keep = addDays(day, -num(env.RECORD_TTL_DAYS, 14));
    await env.DB.batch([
      env.DB.prepare('DELETE FROM subs WHERE day < ?').bind(keep),
      env.DB.prepare('DELETE FROM checkins WHERE day < ?').bind(keep),
      env.DB.prepare('DELETE FROM sessions WHERE date < ?').bind(keep),
      env.DB.prepare('DELETE FROM reactions WHERE k IN (SELECT k FROM moments WHERE date < ?)').bind(keep),
      env.DB.prepare('DELETE FROM moments WHERE date < ?').bind(keep),
      env.DB.prepare('DELETE FROM counters WHERE day < ?').bind(addDays(day, -2)),
      env.DB.prepare('DELETE FROM live_rooms WHERE date < ?').bind(keep),
      env.DB.prepare('DELETE FROM live_results WHERE date < ?').bind(keep),
      env.DB.prepare('DELETE FROM reports WHERE updated < ?').bind(keep + 'T00:00:00.000Z'),
      env.DB.prepare('DELETE FROM watches WHERE day < ?').bind(keep),
    ]);
  },
};
