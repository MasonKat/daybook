// Run the Worker locally on Node with an in-memory KV (no wrangler, no Cloudflare account).
// Usage: node test/dev_server.mjs [--port 8788] [--origin http://127.0.0.1:8790] [--teacher dev-teacher-token]
import http from 'node:http';
import worker from '../src/index.js';
import { FakeKV } from './fake_kv.mjs';

const arg = (name, d) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : d; };
const port = Number(arg('port', 8788));
const env = {
  SUBMISSIONS: new FakeKV(), PHOTO_STORE: 'kv', PHOTO_TTL_DAYS: '7', RECORD_TTL_DAYS: '14', RATE_PER_HOUR: arg('rate', '10'),
  ALLOWED_ORIGIN: arg('origin', 'http://127.0.0.1:8790'), TEACHER_TOKEN: arg('teacher', 'dev-teacher-token'),
};

http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const request = new Request(`http://127.0.0.1:${port}${req.url}`, {
    method: req.method, headers: req.headers, body: ['GET', 'HEAD'].includes(req.method) ? undefined : body,
  });
  try {
    const r = await worker.fetch(request, env);
    res.writeHead(r.status, Object.fromEntries(r.headers));
    res.end(Buffer.from(await r.arrayBuffer()));
  } catch (e) {
    res.writeHead(500); res.end(String(e && e.stack || e));
  }
}).listen(port, '127.0.0.1', () => console.log(`daybook worker (dev, in-memory KV) on http://127.0.0.1:${port}`));
