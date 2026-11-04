// In-memory stand-in for a Workers KV namespace (get/put/list/delete/getWithMetadata + expirationTtl).
export class FakeKV {
  constructor(clock = () => Date.now()) { this.m = new Map(); this.clock = clock; this.ops = { list: 0, put: 0, get: 0, delete: 0 }; }
  _live(k) {
    const e = this.m.get(k);
    if (!e) return null;
    if (e.exp && e.exp <= this.clock()) { this.m.delete(k); return null; }
    return e;
  }
  _conv(v, type) {
    if (v == null) return null;
    if (type === 'json') return JSON.parse(typeof v === 'string' ? v : new TextDecoder().decode(v));
    if (type === 'arrayBuffer') return typeof v === 'string' ? new TextEncoder().encode(v).buffer : v;
    return typeof v === 'string' ? v : new TextDecoder().decode(v);
  }
  async get(k, type) { this.ops.get++; const e = this._live(k); return e ? this._conv(e.v, typeof type === 'object' ? type.type : type) : null; }
  async getWithMetadata(k, type) { const e = this._live(k); return { value: e ? this._conv(e.v, type) : null, metadata: e ? e.meta : null }; }
  async put(k, v, opts = {}) {
    this.ops.put++;
    if (opts.expirationTtl !== undefined && opts.expirationTtl < 60) throw new Error('expirationTtl must be >= 60');
    const val = v instanceof ArrayBuffer ? v.slice(0) : String(v);
    if (opts.metadata && JSON.stringify(opts.metadata).length > 1024) throw new Error('metadata too large');
    this.m.set(k, { v: val, meta: opts.metadata ?? null, exp: opts.expirationTtl ? this.clock() + opts.expirationTtl * 1000 : 0, ttl: opts.expirationTtl });
  }
  async delete(k) { this.ops.delete++; this.m.delete(k); }
  async list({ prefix = '', cursor } = {}) {
    this.ops.list++;
    const keys = [...this.m.keys()].filter((k) => k.startsWith(prefix) && this._live(k)).sort();
    const start = cursor ? Number(cursor) : 0, page = keys.slice(start, start + 2);  // tiny pages exercise the cursor loop
    const done = start + 2 >= keys.length;
    return { keys: page.map((name) => ({ name, metadata: this.m.get(name).meta })), list_complete: done, cursor: done ? undefined : String(start + 2) };
  }
}

export class FakeR2 {
  constructor() { this.m = new Map(); }
  async put(k, body, opts = {}) { const buf = await new Response(body).arrayBuffer(); this.m.set(k, { buf, httpMetadata: opts.httpMetadata || {} }); }
  async get(k) { const e = this.m.get(k); return e ? { body: e.buf, httpMetadata: e.httpMetadata } : null; }
  async delete(k) { this.m.delete(k); }
}
