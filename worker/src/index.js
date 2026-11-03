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
    const isStudent = url.pathname === '/submit' || url.pathname === '/status';
    // CORS: browsers may only call the student endpoints from the Pages origin.
    if (isStudent && origin && origin !== env.ALLOWED_ORIGIN) return json({ ok: false, error: 'origin' }, 403, h);
    if (req.method === 'OPTIONS') return new Response(null, { status: isStudent ? 204 : 404, headers: h });

    if (url.pathname === '/submit' && req.method === 'POST') return submit(req, env, h);
    if (url.pathname === '/status' && req.method === 'GET') {
      const token = (url.searchParams.get('t') || '').toLowerCase();
      if (!TOKEN_RE.test(token)) return json({ ok: false, error: 'bad_token' }, 400, h);
      const day = seoulDay(Date.now());
      return json({ ok: true, day, days: await recentDays(env, token, day) }, 200, h);
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
