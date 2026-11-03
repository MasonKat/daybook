// A Durable Object namespace stand-in: one instance per name, SQLite storage (node:sqlite) with the
// ctx.storage.sql.exec(...) cursor API, setAlarm/deleteAll, and usage counters (requests, rows read/written).
import { DatabaseSync } from 'node:sqlite';

class Cursor {
  constructor(rows) { this.rows = rows; }
  toArray() { return this.rows; }
  one() { if (this.rows.length !== 1) throw new Error('expected one row'); return this.rows[0]; }
}

class FakeSql {
  constructor(usage) { this.db = new DatabaseSync(':memory:'); this.usage = usage; }
  exec(sql, ...args) {
    const st = this.db.prepare(sql);
    if (/^\s*(SELECT|WITH)\b/i.test(sql)) {
      const rows = st.all(...args).map((r) => Object.assign({}, r));
      this.usage.rowsRead += Math.max(1, rows.length);
      return new Cursor(rows);
    }
    const r = st.run(...args);
    this.usage.rowsWritten += Number(r.changes || 0);
    return new Cursor([]);
  }
}

export class FakeDONamespace {
  constructor(cls, env) { this.cls = cls; this.env = env; this.objs = new Map(); this.usage = { requests: 0, rowsRead: 0, rowsWritten: 0 }; }
  idFromName(name) { return String(name); }
  get(id) {
    if (!this.objs.has(id)) {
      const usage = this.usage;
      const ctx = { alarmAt: null, storage: { sql: new FakeSql(usage),
        async setAlarm(t) { ctx.alarmAt = t; },
        async deleteAll() {
          const names = ctx.storage.sql.db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all().map((r) => r.name);
          for (const n of names) ctx.storage.sql.db.exec(`DROP TABLE IF EXISTS "${n}"`);
        } } };
      this.objs.set(id, { inst: new this.cls(ctx, this.env), ctx });
    }
    const o = this.objs.get(id);
    return { fetch: async (req) => { this.usage.requests++; return o.inst.fetch(req); }, _obj: o };
  }
  async fireAlarm(name) { const o = this.objs.get(String(name)); if (o) await o.inst.alarm(); }
}
