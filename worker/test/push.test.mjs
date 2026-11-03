// F15.3 Web Push: RFC 8291 test vectors, the VAPID JWT, subscribe / unsubscribe, a fake push service, quiet hours, the
// daily cap, 410s, and that a retired link stops getting pushes.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import nodeCrypto from 'node:crypto';
import { encrypt, vapidHeader, b64u, unb64u } from '../src/webpush.js';
import { ALL, B, ORIGIN, TEACHER, TOK, allow, call, mkEnv } from './helpers.mjs';

// RFC 8291 Appendix A (checked against an independent node:crypto computation of every intermediate value)
const V = {
  plaintext: 'V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24',
  asPub: 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8',
  asPriv: 'yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw',
  uaPub: 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4',
  uaPriv: 'q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94',
  auth: 'BTBZMqHH6r4Tts7J_aSIgg',
  salt: 'DGv6ra1nlYgDCS1FRnbzlw',
  body: 'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN',
};
const VAPID = { VAPID_PUBLIC: V.asPub, VAPID_PRIVATE: V.asPriv, VAPID_SUBJECT: 'mailto:teacher@example.com' };

// the browser side, for the tests: decrypt an aes128gcm body with the subscription's private key
function decrypt(body, uaPriv, auth) {
  const salt = body.subarray(0, 16), idlen = body[20], keyid = body.subarray(21, 21 + idlen), ct = body.subarray(21 + idlen);
  const ecdh = nodeCrypto.createECDH('prime256v1'); ecdh.setPrivateKey(uaPriv);
  const uaPub = ecdh.getPublicKey(), shared = ecdh.computeSecret(keyid);
  const h = (k, d) => nodeCrypto.createHmac('sha256', k).update(d).digest();
  const ikm = h(h(auth, shared), Buffer.concat([Buffer.from('WebPush: info\0'), uaPub, keyid, Buffer.from([1])]));
  const prk = h(salt, ikm);
  const cek = h(prk, Buffer.from('Content-Encoding: aes128gcm\0\x01', 'binary')).subarray(0, 16);
  const nonce = h(prk, Buffer.from('Content-Encoding: nonce\0\x01', 'binary')).subarray(0, 12);
  const d = nodeCrypto.createDecipheriv('aes-128-gcm', cek, nonce);
  d.setAuthTag(ct.subarray(ct.length - 16));
  const pt = Buffer.concat([d.update(ct.subarray(0, ct.length - 16)), d.final()]);
  assert.equal(pt[pt.length - 1], 2);                          // the last-record delimiter
  return JSON.parse(pt.subarray(0, pt.length - 1).toString('utf-8'));
}
function browser() {
  const e = nodeCrypto.createECDH('prime256v1'); e.generateKeys();
  return { priv: e.getPrivateKey(), p256dh: e.getPublicKey().toString('base64url'), auth: nodeCrypto.randomBytes(16) };
}
function subReq(t, br, { role = 's', endpoint = `https://push.example.com/send/${Math.random().toString(36).slice(2)}` } = {}) {
  const fd = new FormData();
  for (const [k, v] of Object.entries({ t, role, endpoint, p256dh: br.p256dh, auth: br.auth.toString('base64url') })) fd.append(k, v);
  return new Request('https://w.example/push/subscribe', { method: 'POST', body: fd, headers: { Origin: ORIGIN } });
}
const teacherPost = (env, path, body) => call(env, new Request('https://w.example' + path, {
  method: 'POST', headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
async function ready(extra = {}) {
  const pushes = [];
  const env = mkEnv({ ...VAPID, PUSH_QUIET: '0-0', ...extra });
  env.PUSH_FETCH = async (url, init) => { pushes.push({ url, init }); return { status: env.PUSH_STATUS || 201 }; };
  await call(env, new Request('https://w.example/tokmap', { method: 'PUT', headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' },
    body: JSON.stringify({ tokmap: ALL, parents: { pppppppppppppppppppppppppp: 'c1' } }) }));
  return { env, pushes };
}

test('F15.3: aes128gcm encryption matches the RFC 8291 test vector', async () => {
  const out = await encrypt(unb64u(V.uaPub), unb64u(V.auth), unb64u(V.plaintext),
    { as: { d: unb64u(V.asPriv), pub: unb64u(V.asPub) }, salt: unb64u(V.salt) });
  assert.equal(b64u(out), V.body);
  assert.deepEqual(decrypt(Buffer.from(await encrypt(unb64u(V.uaPub), unb64u(V.auth), new TextEncoder().encode('{"a":1}'))),
    Buffer.from(V.uaPriv, 'base64url'), Buffer.from(V.auth, 'base64url')), { a: 1 });   // fresh keys: round trip
});

test('F15.3: the VAPID header is an ES256 JWT for the push service origin, verifiable with the public key', async () => {
  const h = await vapidHeader('https://fcm.example.com/send/abc', { publicKey: V.asPub, privateKey: V.asPriv, subject: 'mailto:t@example.com' }, 1000);
  const [, jwt, k] = h.match(/^vapid t=([^,]+), k=(.+)$/);
  assert.equal(k, V.asPub);
  const [hd, cl, sig] = jwt.split('.');
  assert.deepEqual(JSON.parse(Buffer.from(cl, 'base64url')), { aud: 'https://fcm.example.com', exp: 1000 + 43200, sub: 'mailto:t@example.com' });
  const pt = unb64u(V.asPub);
  const pub = nodeCrypto.createPublicKey({ key: { kty: 'EC', crv: 'P-256', x: b64u(pt.slice(1, 33)), y: b64u(pt.slice(33)) }, format: 'jwk' });
  assert.ok(nodeCrypto.verify('sha256', Buffer.from(`${hd}.${cl}`), { key: pub, dsaEncoding: 'ieee-p1363' }, Buffer.from(sig, 'base64url')));
});

test('F15.3: subscribe (validated, students and parents), count without names, unsubscribe', async () => {
  const { env } = await ready();
  const br = browser();
  assert.equal((await call(env, subReq(TOK, br))).body.ok, true);
  assert.equal((await call(env, subReq('pppppppppppppppppppppppppp', browser(), { role: 'p' }))).body.ok, true);
  assert.equal((await call(env, subReq(TOK, browser(), { role: 'p' }))).body.error, 'unknown_token');     // a student link is not a parent link
  assert.equal((await call(env, subReq('zzzzzzzzzzzzzzzzzzzzzzzzzz', browser()))).body.error, 'unknown_token');
  assert.equal((await call(env, subReq(TOK, { ...br, p256dh: 'AAAA' }))).body.error, 'bad_subscription');
  assert.equal((await call(env, subReq(TOK, br, { endpoint: 'http://insecure.example/x' }))).body.error, 'bad_subscription');
  const c = await call(env, new Request('https://w.example/push/count', { headers: { 'X-Teacher-Token': TEACHER } }));
  assert.deepEqual([c.body.students, c.body.parents, c.body.by_class.c1, c.body.vapid], [1, 1, { s: 1, p: 1 }, true]);
  assert.ok(!JSON.stringify(c.body).includes(TOK));
  const ep = env.DB.db.prepare('SELECT endpoint FROM pushsubs WHERE token = ?').get(TOK).endpoint;
  const fd = new FormData(); fd.append('t', TOK); fd.append('endpoint', ep);
  await call(env, new Request('https://w.example/push/unsubscribe', { method: 'POST', body: fd, headers: { Origin: ORIGIN } }));
  assert.equal(env.DB.db.prepare('SELECT COUNT(*) AS n FROM pushsubs WHERE token = ?').get(TOK).n, 0);
  assert.equal((await call(env, new Request('https://w.example/push/count'))).status, 401);
});

test('F15.3: send: encrypted generic payload to the own page, kinds per role, daily cap, 410 removes, batches', async () => {
  const { env, pushes } = await ready({ PUSH_BATCH: '2' });
  const brs = [browser(), browser(), browser()];
  await call(env, subReq(TOK, brs[0]));
  await call(env, subReq('aaaaaaaaaaaaaaaaaaaaaaaaaa', brs[1]));
  await call(env, subReq('pppppppppppppppppppppppppp', brs[2], { role: 'p' }));
  await call(env, subReq(B, browser()));                                         // class c2
  let r = await teacherPost(env, '/push/send', { classes: ['c1'], kind: 'video', body: '새 영상 2개가 올라왔어요' });
  assert.deepEqual([r.body.sent, r.body.more], [2, false]);                       // parents only get "report" by default
  const p0 = pushes.find((p) => p.url === env.DB.db.prepare('SELECT endpoint FROM pushsubs WHERE token = ?').get(TOK).endpoint);
  assert.equal(p0.init.headers['Content-Encoding'], 'aes128gcm');
  assert.match(p0.init.headers.Authorization, /^vapid t=[\w-]+\.[\w-]+\.[\w-]+, k=/);
  const msg = decrypt(Buffer.from(p0.init.body), brs[0].priv, brs[0].auth);
  assert.deepEqual(msg, { title: 'AMP 학습 기록', body: '새 영상 2개가 올라왔어요', url: `${ORIGIN}/s/${TOK}/`, kind: 'video' });
  r = await teacherPost(env, '/push/send', { classes: ['c1'], kind: 'report', body: '이번 주 기록이 업데이트됐어요' });
  assert.deepEqual([r.body.sent, r.body.capped], [1, 2]);                         // the parent; the students had theirs today
  const pmsg = decrypt(Buffer.from(pushes[pushes.length - 1].init.body), brs[2].priv, brs[2].auth);
  assert.equal(pmsg.url, `${ORIGIN}/p/pppppppppppppppppppppppppp/`);
  // a push service that answers 410 forgets the subscription; batches report "more"
  env.DB.db.exec('UPDATE pushsubs SET sent_day = NULL, sent_n = 0');
  env.PUSH_STATUS = 410;
  r = await teacherPost(env, '/push/send', { classes: [], kind: 'video', body: 'x' });
  assert.deepEqual([r.body.sent, r.body.removed, r.body.more], [0, 2, true]);
  assert.equal(env.DB.db.prepare("SELECT COUNT(*) AS n FROM pushsubs WHERE role = 's'").get().n, 1);
  assert.equal((await teacherPost(env, '/push/send', { kind: 'spam', body: 'x' })).status, 400);
});

test('F15.3: quiet hours (22:00-08:00 KST) send nothing; no VAPID keys, no push', async () => {
  const { env, pushes } = await ready({ PUSH_QUIET: '22-8' });
  await call(env, subReq(TOK, browser()));
  const real = Date.now;
  try {
    Date.now = () => Date.parse('2026-11-06T14:30:00Z');                         // 23:30 KST
    let r = await teacherPost(env, '/push/send', { kind: 'message', body: 'x' });
    assert.deepEqual([r.body.quiet, r.body.sent, pushes.length], [true, 0, 0]);
    Date.now = () => Date.parse('2026-11-06T22:59:00Z');                         // 07:59 KST
    assert.equal((await teacherPost(env, '/push/send', { kind: 'message', body: 'x' })).body.quiet, true);
    Date.now = () => Date.parse('2026-11-06T23:00:00Z');                         // 08:00 KST
    r = await teacherPost(env, '/push/send', { kind: 'message', body: 'x' });
    assert.deepEqual([r.body.quiet, r.body.sent], [false, 1]);
  } finally { Date.now = real; }
  const bare = mkEnv({ PUSH_QUIET: '0-0' }); await allow(bare, ALL);
  assert.equal((await teacherPost(bare, '/push/send', { kind: 'message', body: 'x' })).body.error, 'no_vapid');
});

test('F15.3: a retired or rotated link stops getting pushes; a moved student follows the class', async () => {
  const { env } = await ready();
  await call(env, subReq(TOK, browser()));
  await call(env, subReq('aaaaaaaaaaaaaaaaaaaaaaaaaa', browser()));
  const { [TOK]: _gone, ...rest } = ALL;
  await call(env, new Request('https://w.example/tokmap', { method: 'PUT', headers: { 'X-Teacher-Token': TEACHER, 'Content-Type': 'application/json' },
    body: JSON.stringify({ tokmap: { ...rest, aaaaaaaaaaaaaaaaaaaaaaaaaa: 'c2' } }) }));
  const rows = env.DB.db.prepare("SELECT token, class FROM pushsubs WHERE role = 's'").all();
  assert.deepEqual(rows.map((r) => [r.token, r.class]), [['aaaaaaaaaaaaaaaaaaaaaaaaaa', 'c2']]);
});
