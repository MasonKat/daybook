import worker, { LiveRoom } from '../src/index.js';
import { FakeDONamespace } from './fake_do.mjs';
import { FakeKV, FakeR2 } from './fake_kv.mjs';
import { FakeD1 } from './fake_d1.mjs';

export const ORIGIN = 'https://daybook.pages.dev';
export const TEACHER = 'teacher-secret-0123456789';
export const TOK = 'abcdefghijklmnopqrstuvwxyz';
export const A = 'aaaaaaaaaaaaaaaaaaaaaaaaaa', A2 = 'dddddddddddddddddddddddddd', B = 'bbbbbbbbbbbbbbbbbbbbbbbbbb', C = 'cccccccccccccccccccccccccc';
export const OTHER = 'zyxwvutsrqponmlkjihgfedcba';

export function mkEnv(extra = {}) {
  const env = { SUBMISSIONS: new FakeKV(), DB: new FakeD1(), ALLOWED_ORIGIN: ORIGIN, TEACHER_TOKEN: TEACHER, PHOTO_STORE: 'kv',
    PHOTO_TTL_DAYS: '7', RECORD_TTL_DAYS: '14', RATE_PER_HOUR: '10', ...extra };
  env.LIVE = new FakeDONamespace(LiveRoom, env);
  return env;
}
export const call = async (env, req) => {
  const r = await worker.fetch(req, env);
  const ct = r.headers.get('content-type') || '';
  return { status: r.status, body: ct.includes('json') ? await r.json() : await r.arrayBuffer(), headers: r.headers };
};
export const teacherPut = (env, path, body, auth = TEACHER) => call(env, new Request('https://w.example' + path, {
  method: 'PUT', headers: { 'X-Teacher-Token': auth, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
export const teacherGet = (env, path) => call(env, new Request('https://w.example' + path, { headers: { 'X-Teacher-Token': TEACHER } }));
export const studentGet = (env, path) => call(env, new Request('https://w.example' + path, { headers: { Origin: ORIGIN } }));
export async function allow(env, map) { return teacherPut(env, '/tokmap', { tokmap: map }); }
export const ALL = { [TOK]: 'c1', [A]: 'c1', [A2]: 'c1', [B]: 'c2', [OTHER]: 'c1', aaaaaaaaaaaaaaaaaaaaaaaaab: 'c1',
  bbbbbbbbbbbbbbbbbbbbbbbbbc: 'c1', cccccccccccccccccccccccccd: 'c1' };

export function submitReq({ t = TOK, set = 'Adaptive_Screeners_3S_June19_G11', bytes = 1000, type = 'image/jpeg', origin = ORIGIN, line, chip, count } = {}) {
  const fd = new FormData();
  if (t !== null) fd.append('t', t);
  if (set) fd.append('set', set);
  if (line !== undefined) fd.append('line', line);
  if (chip !== undefined) fd.append('chip', chip);
  if (count !== undefined) fd.append('count', count);
  if (bytes !== null) fd.append('photo', new File([new Uint8Array(bytes).fill(7)], 'p.jpg', { type }));
  return new Request('https://w.example/submit', { method: 'POST', body: fd, headers: origin ? { Origin: origin } : {} });
}
export { FakeR2 };
