// DAYBOOK Worker: receives one photo per student per day. Never sees names; tokens only.
// Bindings: KV SUBMISSIONS (required), R2 PHOTOS (only when PHOTO_STORE = "r2"). Secret: TEACHER_TOKEN.
const TOKEN_RE = /^[a-z2-7]{26}$/;
const TYPES = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/heic': 'heic', 'image/heif': 'heic' };
const MAX_BYTES = 8 * 1024 * 1024;
const DAY = 86400;

export const seoulDay = (ms) => new Date(ms + 9 * 3600e3).toISOString().slice(0, 10);
const addDays = (day, n) => seoulDay(Date.parse(day + 'T00:00:00+09:00') + n * DAY * 1000);
const num = (v, d) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : d);

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

async function recentDays(env, token, today) {
  const days = Array.from({ length: 8 }, (_, i) => addDays(today, -i));
  const recs = await Promise.all(days.map((d) => env.SUBMISSIONS.get(`sub:${d}:${token}`)));
  return days.filter((_, i) => recs[i] !== null).sort();
}

async function submit(req, env, h) {
  const now = Date.now(), day = seoulDay(now), kv = env.SUBMISSIONS;
  let form;
  try { form = await req.formData(); } catch { return json({ ok: false, error: 'bad_form' }, 400, h); }
  const token = String(form.get('t') || '').toLowerCase();
  if (!TOKEN_RE.test(token)) return json({ ok: false, error: 'bad_token' }, 400, h);
  const photo = form.get('photo');
  if (!photo || typeof photo === 'string') return json({ ok: false, error: 'no_photo' }, 400, h);
  const ext = TYPES[(photo.type || '').toLowerCase()];
  if (!ext) return json({ ok: false, error: 'bad_type' }, 415, h);
  if (photo.size > MAX_BYTES) return json({ ok: false, error: 'too_big' }, 413, h);

  const rlKey = `rl:${token}:${Math.floor(now / 3600e3)}`;
  const used = Number(await kv.get(rlKey)) || 0;
  if (used >= num(env.RATE_PER_HOUR, 10)) return json({ ok: false, error: 'rate_limited' }, 429, h);
  await kv.put(rlKey, String(used + 1), { expirationTtl: 3700 });

  const recKey = `sub:${day}:${token}`;
  const prev = await kv.get(recKey, 'json');
  let key;
  if (env.PHOTO_STORE === 'r2') {
    key = `${day}/${token}.${ext}`;
    if (prev && prev.key && prev.key !== key) await env.PHOTOS.delete(prev.key);
    await env.PHOTOS.put(key, photo.stream(), { httpMetadata: { contentType: photo.type } });
  } else {
    key = `photo:${day}:${token}`;
    await kv.put(key, await photo.arrayBuffer(), {
      expirationTtl: num(env.PHOTO_TTL_DAYS, 7) * DAY, metadata: { type: photo.type },
    });
  }
  const set = String(form.get('set') || '').replace(/[^\w-]/g, '').slice(0, 80);
  const iso = new Date(now).toISOString();
  const rec = { token, set: set || (prev && prev.set) || '', day, ts: (prev && prev.ts) || iso, updated: iso, key, n: ((prev && prev.n) || 0) + 1 };
  await kv.put(recKey, JSON.stringify(rec), { expirationTtl: num(env.RECORD_TTL_DAYS, 14) * DAY, metadata: rec });
  const days = await recentDays(env, token, day);
  return json({ ok: true, ts: rec.ts, day, already: !!prev, days }, 200, h);
}

// ---- 출석 (F2): the console PUTs today's session (code + window) and the token -> class map; students check in.
const CLASS_RE = /^[a-z0-9]{1,12}$/;
const CODE_RE = /^\d{4}$/;

async function classOf(env, token) {
  const map = await env.SUBMISSIONS.get('tokmap', 'json');
  return (map && map[token]) || null;
}

async function putSession(req, env) {
  let b;
  try { b = await req.json(); } catch { return json({ ok: false, error: 'bad_json' }, 400, {}); }
  const { class: cls, date, code, open_from: from, open_until: until, start } = b || {};
  if (!CLASS_RE.test(cls || '') || !/^\d{4}-\d{2}-\d{2}$/.test(date || '') || !CODE_RE.test(code || '') ||
      Number.isNaN(Date.parse(from)) || Number.isNaN(Date.parse(until))) return json({ ok: false, error: 'bad_session' }, 400, {});
  if (b.tokmap && typeof b.tokmap === 'object') {
    const clean = {};
    for (const [t, c] of Object.entries(b.tokmap)) if (TOKEN_RE.test(t) && CLASS_RE.test(String(c))) clean[t] = String(c);
    await env.SUBMISSIONS.put('tokmap', JSON.stringify(clean));
  }
  const rec = { class: cls, date, code, open_from: new Date(from).toISOString(), open_until: new Date(until).toISOString(), start: String(start || '').slice(0, 5) };
  await env.SUBMISSIONS.put(`session:${cls}:${date}`, JSON.stringify(rec), { expirationTtl: 2 * DAY });
  return json({ ok: true }, 200, {});
}

async function checkin(req, env, h) {
  const now = Date.now(), day = seoulDay(now), kv = env.SUBMISSIONS;
  let form;
  try { form = await req.formData(); } catch { return json({ ok: false, error: 'bad_form' }, 400, h); }
  const token = String(form.get('t') || '').toLowerCase(), code = String(form.get('code') || '');
  if (!TOKEN_RE.test(token)) return json({ ok: false, error: 'bad_token' }, 400, h);
  const done = await kv.get(`checkin:${day}:${token}`, 'json');
  if (done) return json({ ok: true, already: true, ts: done.ts, day }, 200, h);
  const failKey = `ckfail:${day}:${token}`, fails = Number(await kv.get(failKey)) || 0;
  // refusals are answers, not faults: 200 + ok:false (a 4xx would show as an error in the student's browser console)
  if (fails >= num(env.CHECKIN_FAILS_PER_DAY, 5)) return json({ ok: false, error: 'rate_limited' }, 200, h);
  const cls = await classOf(env, token);
  const sess = cls ? await kv.get(`session:${cls}:${day}`, 'json') : null;
  if (!sess) return json({ ok: false, error: 'no_class_today' }, 200, h);
  if (now < Date.parse(sess.open_from) || now > Date.parse(sess.open_until)) return json({ ok: false, error: 'closed' }, 200, h);
  if (!CODE_RE.test(code) || code !== sess.code) {
    await kv.put(failKey, String(fails + 1), { expirationTtl: 2 * DAY });
    return json({ ok: false, error: 'bad_code', left: Math.max(0, num(env.CHECKIN_FAILS_PER_DAY, 5) - fails - 1) }, 200, h);
  }
  const rec = { token, class: cls, day, ts: new Date(now).toISOString() };
  await kv.put(`checkin:${day}:${token}`, JSON.stringify(rec), { expirationTtl: num(env.RECORD_TTL_DAYS, 14) * DAY, metadata: rec });
  return json({ ok: true, already: false, ts: rec.ts, day }, 200, h);
}

async function listByPrefix(url, env, prefix) {
  const since = url.searchParams.get('since') || '';
  const items = [];
  let cursor;
  do {
    const page = await env.SUBMISSIONS.list({ prefix, cursor });
    for (const k of page.keys) if (k.metadata && (!since || (k.metadata.updated || k.metadata.ts) > since)) items.push(k.metadata);
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return items;
}

async function listSubmissions(url, env, h) {
  const since = url.searchParams.get('since') || '';
  const items = [];
  let cursor;
  do {
    const page = await env.SUBMISSIONS.list({ prefix: 'sub:', cursor });
    for (const k of page.keys) if (k.metadata && (!since || k.metadata.updated > since)) items.push(k.metadata);
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  items.sort((a, b) => (a.updated < b.updated ? -1 : 1));
  return json({ now: new Date().toISOString(), items }, 200, h);
}

async function getPhoto(url, env, h) {
  const key = url.searchParams.get('key') || '';
  if (!/^(photo:\d{4}-\d{2}-\d{2}:[a-z2-7]{26}|\d{4}-\d{2}-\d{2}\/[a-z2-7]{26}\.(jpg|png|heic))$/.test(key)) return json({ ok: false, error: 'bad_key' }, 400, h);
  if (key.startsWith('photo:')) {
    const { value, metadata } = await env.SUBMISSIONS.getWithMetadata(key, 'arrayBuffer');
    if (!value) return json({ ok: false, error: 'not_found' }, 404, h);
    return new Response(value, { headers: { ...h, 'Content-Type': (metadata && metadata.type) || 'image/jpeg', 'Cache-Control': 'no-store' } });
  }
  const obj = env.PHOTOS ? await env.PHOTOS.get(key) : null;
  if (!obj) return json({ ok: false, error: 'not_found' }, 404, h);
  return new Response(obj.body, { headers: { ...h, 'Content-Type': obj.httpMetadata?.contentType || 'image/jpeg', 'Cache-Control': 'no-store' } });
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const h = corsHeaders(req, env);
    const origin = req.headers.get('Origin');
    const isStudent = url.pathname === '/submit' || url.pathname === '/status' || url.pathname === '/checkin';
    // CORS: browsers may only call the student endpoints from the Pages origin.
    if (isStudent && origin && origin !== env.ALLOWED_ORIGIN) return json({ ok: false, error: 'origin' }, 403, h);
    if (req.method === 'OPTIONS') return new Response(null, { status: isStudent ? 204 : 404, headers: h });

    if (url.pathname === '/submit' && req.method === 'POST') return submit(req, env, h);
    if (url.pathname === '/status' && req.method === 'GET') {
      const token = (url.searchParams.get('t') || '').toLowerCase();
      if (!TOKEN_RE.test(token)) return json({ ok: false, error: 'bad_token' }, 400, h);
      const day = seoulDay(Date.now());
      const [days, ck, cls] = await Promise.all([recentDays(env, token, day), env.SUBMISSIONS.get(`checkin:${day}:${token}`, 'json'), classOf(env, token)]);
      const sess = cls ? await env.SUBMISSIONS.get(`session:${cls}:${day}`, 'json') : null;
      return json({ ok: true, day, days, checkin_ts: ck ? ck.ts : null, class_today: !!sess, start: sess ? sess.start : null }, 200, h);
    }
    if (url.pathname === '/checkin' && req.method === 'POST') return checkin(req, env, h);
    if (url.pathname === '/session' || url.pathname === '/checkins') {
      if (!teacherOk(req, env)) return json({ ok: false, error: 'unauthorized' }, 401, {});
      if (url.pathname === '/session' && req.method === 'PUT') return putSession(req, env);
      if (url.pathname === '/checkins' && req.method === 'GET') {
        const items = (await listByPrefix(url, env, 'checkin:')).sort((a, b) => (a.ts < b.ts ? -1 : 1));
        return json({ now: new Date().toISOString(), items }, 200, {});
      }
      return json({ ok: false, error: 'method' }, 405, {});
    }
    if (url.pathname === '/submissions' || url.pathname === '/photo') {
      if (!teacherOk(req, env)) return json({ ok: false, error: 'unauthorized' }, 401, {});
      if (req.method !== 'GET') return json({ ok: false, error: 'method' }, 405, {});
      return url.pathname === '/photo' ? getPhoto(url, env, {}) : listSubmissions(url, env, {});
    }
    if (url.pathname === '/' || url.pathname === '/health') return json({ ok: true }, 200, h);
    return json({ ok: false, error: 'not_found' }, 404, h);
  },
};
