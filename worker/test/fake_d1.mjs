// A D1 stand-in for tests and the dev server: real SQLite (node:sqlite), the D1 statement API
// (prepare/bind/first/all/run/batch), and usage counters close to how D1 bills (rows read / rows written).
import { DatabaseSync } from 'node:sqlite';

export class FakeD1 {
  constructor() {
    this.db = new DatabaseSync(':memory:');
    this.usage = { queries: 0, rowsRead: 0, rowsWritten: 0 };
    this.failNext = 0;            // tests: make the next N statements throw (storage trouble)
  }
  prepare(sql) { return new Stmt(this, sql, []); }
  async batch(stmts) {
    this.db.exec('BEGIN');
    try {
      const out = [];
      for (const s of stmts) out.push(await s.run());
      this.db.exec('COMMIT');
      return out;
    } catch (e) { this.db.exec('ROLLBACK'); throw e; }
  }
  async exec(sql) { this.db.exec(sql); return { count: 1 }; }
}

class Stmt {
  constructor(d1, sql, args) { this.d1 = d1; this.sql = sql; this.args = args; }
  bind(...args) {
    for (const a of args) if (a === undefined) throw new Error('D1_TYPE_ERROR: undefined is not a valid bind value');
    return new Stmt(this.d1, this.sql, args);
  }
  _check() {
    this.d1.usage.queries++;
    if (this.d1.failNext > 0) { this.d1.failNext--; throw new Error('D1_ERROR: simulated storage failure'); }
  }
  _norm(r) { return r ? Object.assign({}, r) : null; }
  async first() {
    this._check();
    const r = this.d1.db.prepare(this.sql).get(...this.args);
    this.d1.usage.rowsRead += 1;
    if (/^\s*(INSERT|UPDATE|DELETE)/i.test(this.sql) && r) this.d1.usage.rowsWritten += 1;
    return this._norm(r);
  }
  async all() {
    this._check();
    const rows = this.d1.db.prepare(this.sql).all(...this.args).map((r) => this._norm(r));
    this.d1.usage.rowsRead += Math.max(1, rows.length);
    return { results: rows, success: true, meta: {} };
  }
  async run() {
    this._check();
    const r = this.d1.db.prepare(this.sql).run(...this.args);
    this.d1.usage.rowsWritten += Number(r.changes || 0);
    this.d1.usage.rowsRead += 1;
    return { success: true, meta: { changes: Number(r.changes || 0), last_row_id: Number(r.lastInsertRowid || 0) } };
  }
}
