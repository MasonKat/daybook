// F15.3 Web Push without any third-party service or library: RFC 8291 message encryption (aes128gcm, RFC 8188) and
// RFC 8292 VAPID (an ES256 JWT), with WebCrypto only. Small on purpose; tested against the RFC 8291 test vectors.
const enc = new TextEncoder();

export function b64u(bytes) {
  let s = '';
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
export function unb64u(str) {
  const s = String(str).replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(s + '==='.slice((s.length + 3) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}
const concat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
};
async function hmac(key, data) {
  const k = await crypto.subtle.importKey('raw', key, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', k, data));
}
// a P-256 private key from its raw scalar d and its uncompressed public point (0x04 || x || y)
function jwk(d, pub) {
  return { kty: 'EC', crv: 'P-256', d: b64u(d), x: b64u(pub.slice(1, 33)), y: b64u(pub.slice(33, 65)), ext: true };
}

// RFC 8291: encrypt `payload` for one subscription. `as` (an application-server key pair {d, pub}) and `salt` are
// only given by the tests (the RFC's fixed values); in real use both are fresh random values for every message.
export async function encrypt(uaPublic, authSecret, payload, { as, salt } = {}) {
  salt = salt || crypto.getRandomValues(new Uint8Array(16));
  let asPriv, asPub;
  if (as) {
    asPriv = await crypto.subtle.importKey('jwk', jwk(as.d, as.pub), { name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits']);
    asPub = as.pub;
  } else {
    const kp = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
    asPriv = kp.privateKey;
    asPub = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
  }
  const uaKey = await crypto.subtle.importKey('raw', uaPublic, { name: 'ECDH', namedCurve: 'P-256' }, false, []);
  const shared = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: uaKey }, asPriv, 256));
  const prkKey = await hmac(authSecret, shared);
  const ikm = await hmac(prkKey, concat(enc.encode('WebPush: info\0'), uaPublic, asPub, new Uint8Array([1])));
  const prk = await hmac(salt, ikm);
  const cek = (await hmac(prk, concat(enc.encode('Content-Encoding: aes128gcm\0'), new Uint8Array([1])))).slice(0, 16);
  const nonce = (await hmac(prk, concat(enc.encode('Content-Encoding: nonce\0'), new Uint8Array([1])))).slice(0, 12);
  const key = await crypto.subtle.importKey('raw', cek, 'AES-GCM', false, ['encrypt']);
  // one record: the payload, then the 0x02 "last record" delimiter, no padding
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce }, key, concat(payload, new Uint8Array([2]))));
  const header = concat(salt, new Uint8Array([0, 0, 0x10, 0]), new Uint8Array([asPub.length]), asPub);   // rs = 4096
  return concat(header, ct);
}

// RFC 8292: "vapid t=<ES256 JWT>, k=<public key>" for the push service at `endpoint`
export async function vapidHeader(endpoint, vapid, nowSec = Math.floor(Date.now() / 1000)) {
  const pub = unb64u(vapid.publicKey), d = unb64u(vapid.privateKey);
  const head = b64u(enc.encode(JSON.stringify({ typ: 'JWT', alg: 'ES256' })));
  const claims = b64u(enc.encode(JSON.stringify({ aud: new URL(endpoint).origin, exp: nowSec + 12 * 3600, sub: vapid.subject })));
  const key = await crypto.subtle.importKey('jwk', jwk(d, pub), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  const sig = new Uint8Array(await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key, enc.encode(`${head}.${claims}`)));
  return `vapid t=${head}.${claims}.${b64u(sig)}, k=${vapid.publicKey}`;
}

// POST one encrypted message; returns the push service's HTTP status (0 when it cannot be reached)
export async function send(sub, payloadObj, vapid, fetchFn = fetch, ttl = 86400) {
  const body = await encrypt(unb64u(sub.p256dh), unb64u(sub.auth), enc.encode(JSON.stringify(payloadObj)));
  try {
    const r = await fetchFn(sub.endpoint, {
      method: 'POST', body,
      headers: { 'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream', TTL: String(ttl), Urgency: 'normal',
        Authorization: await vapidHeader(sub.endpoint, vapid) },
    });
    return r.status;
  } catch { return 0; }
}
