#!/usr/bin/env node
/* End-to-end render + flow check (BUILD_BRIEF §11, Appendix C) on the published demo in dist/.
 *
 * - serves dist/ on 127.0.0.1:8790 (like Pages) and the Worker on 127.0.0.1:8788 (in-memory KV)
 * - renders every generated page at 360x740: 0 console errors, no horizontal overflow, <= 100 KB,
 *   generic <title>, 0 requests outside the two local origins
 * - student 오늘 tab: choose a photo -> shrunk in the browser -> POST /submit -> "오늘 완료";
 *   a second upload the same day replaces the photo and keeps the first ts
 * - /go/?set=… redirects a phone that opened its link once, shows the message on one that never did
 * - daybook pull + publish -> the ✓ and time show on the parent page and on the board
 * - board: an attitude button writes the score and moves the class bar
 * - screenshots (390 px) into demo/screenshots/
 * Env: DAYBOOK_BIN (default .venv/bin/daybook), DAYBOOK_DB (demo.sqlite), DAYBOOK_CONFIG (demo/daybook.demo.json)
 */
'use strict';
const fs = require('fs');
const path = require('path');
const http = require('http');
const zlib = require('zlib');
const { spawn, execFileSync } = require('child_process');

let chromium;
try { ({ chromium } = require('playwright')); } catch (e) {
  console.error('Playwright is not installed. Run: npm install --no-save playwright && npx playwright install chromium');
  process.exit(1);
}

const ROOT = path.resolve(__dirname, '..', '..');
const DIST = path.join(ROOT, 'dist');
const SHOTS = path.join(ROOT, 'demo', 'screenshots');
/* F0: the committed screenshots change only on purpose (`make screenshots` / check.ps1 -Screenshots);
   a normal check run never rewrites tracked files, so the teacher's clone stays clean for `git pull`. */
const SHOOT = process.argv.includes('--screenshots') || process.env.DAYBOOK_SCREENSHOTS === '1';
const shot = (pg, name, opts = {}) => (SHOOT ? pg.screenshot({ path: path.join(SHOTS, name), ...opts }) : Promise.resolve());
const PAGES_PORT = 8790, WORKER_PORT = 8788, BOARD_PORT = 8787;
const PAGES = `http://127.0.0.1:${PAGES_PORT}`, WORKER = `http://127.0.0.1:${WORKER_PORT}`, BOARD = `http://127.0.0.1:${BOARD_PORT}`;
const TEACHER = 'dev-teacher-token';
const TITLE = 'AMP 학습 기록';
/* H4: the checks never touch the demo data. They run on a throw-away copy of the demo database, config and
   moment store; the taste build they make goes to a temp folder, not dist-taste/. */
const SRC_DB = process.env.DAYBOOK_DB || path.join(ROOT, 'demo.sqlite');
const SRC_CFG = process.env.DAYBOOK_CONFIG || path.join(ROOT, 'demo', 'daybook.demo.json');
const TMP = fs.mkdtempSync(path.join(require('os').tmpdir(), 'daybook-e2e-'));
const cfgCopy = JSON.parse(fs.readFileSync(SRC_CFG, 'utf-8'));
const srcMoments = path.resolve(ROOT, cfgCopy.moments_dir || 'moments');
cfgCopy.moments_dir = path.join(TMP, 'moments');
if (fs.existsSync(srcMoments)) fs.cpSync(srcMoments, cfgCopy.moments_dir, { recursive: true });
fs.writeFileSync(path.join(TMP, 'daybook.json'), JSON.stringify(cfgCopy));
if (fs.existsSync(SRC_DB)) fs.copyFileSync(SRC_DB, path.join(TMP, 'e2e.sqlite'));
const env = { ...process.env, DAYBOOK_DB: path.join(TMP, 'e2e.sqlite'), DAYBOOK_CONFIG: path.join(TMP, 'daybook.json') };
const TASTE_OUT = path.join(TMP, 'dist-taste');
const WIN = process.platform === 'win32';
const VENV_BIN = path.join(ROOT, '.venv', WIN ? 'Scripts' : 'bin');
const DAYBOOK = process.env.DAYBOOK_BIN || path.join(VENV_BIN, WIN ? 'daybook.exe' : 'daybook');
const PYTHON = path.join(VENV_BIN, WIN ? 'python.exe' : 'python');

let failures = 0;
function check(cond, msg) {
  if (cond) console.log(`  ok   ${msg}`);
  else { failures++; console.log(`  FAIL ${msg}`); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const kstToday = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);

/* ---------- static server for dist/ (Pages-like: dir/ -> dir/index.html) */
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.json': 'application/json', '.webmanifest': 'application/manifest+json', '.txt': 'text/plain' };
function staticServer(root = DIST, port = PAGES_PORT) {
  return http.createServer((req, res) => {
    let p = decodeURIComponent(new URL(req.url, PAGES).pathname);
    if (p.endsWith('/')) p += 'index.html';
    const f = path.join(root, p);
    if (!f.startsWith(root) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); return res.end('not found'); }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(f)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    fs.createReadStream(f).pipe(res);
  }).listen(port, '127.0.0.1');
}

async function waitFor(url, tries = 50) {
  for (let i = 0; i < tries; i++) {
    try { await fetch(url); return; } catch (e) { await sleep(100); }
  }
  throw new Error(`not reachable: ${url}`);
}

/* ---------- a large synthetic photo (PNG 3000x2000) to prove the in-browser shrink */
function makePng(file, w, h) {
  const rows = [];
  for (let y = 0; y < h; y++) {
    const row = Buffer.alloc(1 + w * 3);
    for (let x = 0; x < w; x++) {
      const o = 1 + x * 3;
      row[o] = (x * 255 / w) | 0; row[o + 1] = (y * 255 / h) | 0; row[o + 2] = ((x ^ y) & 0x3f) + 120;
    }
    rows.push(row);
  }
  const crcTable = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (b) => { let c = 0xffffffff; for (const x of b) c = crcTable[(c ^ x) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([len, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  fs.writeFileSync(file, Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(Buffer.concat(rows), { level: 1 })), chunk('IEND', Buffer.alloc(0))]));
}
function jpegSize(buf) {
  if (buf[0] !== 0xff || buf[1] !== 0xd8) return null;
  let i = 2;
  while (i < buf.length) {
    if (buf[i] !== 0xff) return null;
    const m = buf[i + 1], len = buf.readUInt16BE(i + 2);
    if (m >= 0xc0 && m <= 0xc3) return { h: buf.readUInt16BE(i + 5), w: buf.readUInt16BE(i + 7) };
    i += 2 + len;
  }
  return null;
}
const workerGet = async (p) => {
  const r = await fetch(WORKER + p, { headers: { 'X-Teacher-Token': TEACHER } });
  return p.startsWith('/photo') ? Buffer.from(await r.arrayBuffer()) : r.json();
};
const daybook = (...args) => execFileSync(DAYBOOK, args, { env: { ...env, PYTHONIOENCODING: 'utf-8' }, cwd: ROOT, encoding: 'utf-8' });

/* ---------- page audit */
const VIEWPORTS = [{ width: 360, height: 740 }, { width: 390, height: 844 }];
const SCHEMES = ['light', 'dark'];
async function audit(browser, url, file, label, viewport, colorScheme) {
  const ctx = await browser.newContext({ viewport, colorScheme });
  const page = await ctx.newPage();
  const errors = [], foreign = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('request', (r) => { const u = r.url(); if (!u.startsWith(PAGES) && !u.startsWith(WORKER) && !u.startsWith('data:') && !u.startsWith('blob:')) foreign.push(u); });
  await page.goto(url, { waitUntil: 'networkidle' });
  const r = await page.evaluate(() => ({
    /* widest horizontal overflow of the document or of any element (scroll containers hide it from the document) */
    sw: Math.max(document.documentElement.scrollWidth, ...Array.from(document.querySelectorAll('body *')).filter((e) => e.offsetParent !== null && e.clientWidth > 1 && !e.closest('.stories') && getComputedStyle(e).overflowX !== 'visible' && getComputedStyle(e).textOverflow !== 'ellipsis').map((e) => e.scrollWidth - e.clientWidth + document.documentElement.clientWidth)),
    title: document.title,
    og: Array.from(document.querySelectorAll('meta[property^="og:"]')).map((m) => m.content),
    robots: (document.querySelector('meta[name=robots]') || {}).content,
    frames: document.querySelectorAll('iframe').length }));
  const size = file ? fs.statSync(file).size : 0;
  const ok = errors.length === 0 && foreign.length === 0 && r.frames === 0 && r.sw <= viewport.width && r.title === TITLE && r.og.every((o) => o === TITLE) && r.robots === 'noindex,nofollow' && size <= 100 * 1024;
  if (!ok || process.env.E2E_VERBOSE || (viewport.width === 360 && colorScheme === 'light')) check(ok, `${label} ${viewport.width}px ${colorScheme}: errors=${errors.length} external=${foreign.length} scrollWidth=${r.sw} title=${JSON.stringify(r.title)} ${size}B`);
  if (!ok) console.log('       ', errors.slice(0, 3), foreign.slice(0, 3));
  await ctx.close();
  return ok;
}

(async () => {
  if (!fs.existsSync(path.join(DIST, 'go', 'index.html'))) { console.error('dist/ is empty: run `make demo` (or daybook demo) first'); process.exit(1); }
  if (SHOOT) fs.mkdirSync(SHOTS, { recursive: true });
  const srv = staticServer();
  const worker = spawn(process.execPath, ['--experimental-sqlite', '--no-warnings', path.join(ROOT, 'worker', 'test', 'dev_server.mjs'), '--port', String(WORKER_PORT), '--origin', PAGES, '--teacher', TEACHER], { stdio: 'ignore' });
  let board;
  const browser = await chromium.launch();
  try {
    await waitFor(WORKER + '/health');
    await waitFor(PAGES + '/go/');

    const students = fs.readdirSync(path.join(DIST, 's'));
    const parents = fs.readdirSync(path.join(DIST, 'p'));
    console.log(`\n[1] every page at 360x740 and 390x844, light and dark (${students.length} student, ${parents.length} parent, go, index)`);
    check(students.length === 22 && parents.length === 22, 'dist/s and dist/p exist for all 22 demo students (4 classes, incl. 콩)');
    const targets = [];
    for (const t of students) targets.push([`${PAGES}/s/${t}/`, path.join(DIST, 's', t, 'index.html'), `s/${t.slice(0, 6)}…`]);
    for (const t of parents) targets.push([`${PAGES}/p/${t}/`, path.join(DIST, 'p', t, 'index.html'), `p/${t.slice(0, 6)}…`]);
    targets.push([`${PAGES}/go/?set=Adaptive_Screeners_3S_Nov06_G10`, path.join(DIST, 'go', 'index.html'), 'go (no token)']);
    targets.push([`${PAGES}/`, path.join(DIST, 'index.html'), 'index']);
    for (const vp of VIEWPORTS) for (const cs of SCHEMES) {
      let n = 0;
      for (const [u, f, l] of targets) if (await audit(browser, u, f, l, vp, cs)) n++;
      check(n === targets.length, `${n}/${targets.length} pages at ${vp.width}x${vp.height} ${cs}: 0 console errors, 0 third-party requests, 0 iframes, no horizontal scroll, <= 100 KB, generic title`);
    }
    let big = 0;
    const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]));
    for (const f of walk(DIST)) big = Math.max(big, fs.statSync(f).size);
    check(big <= 100 * 1024, `every file in dist/ <= 100 KB (largest ${big} B)`);
    const thumbs = fs.existsSync(path.join(DIST, 't')) ? fs.readdirSync(path.join(DIST, 't')) : [];
    check(thumbs.length > 0 && thumbs.every((f) => fs.statSync(path.join(DIST, 't', f)).size <= 60 * 1024), `${thumbs.length} thumbnails in dist/t, each <= 60 KB`);

    /* today's class (F2): the demo has 고1A on 화·목·토 and 기초B on 월·수·금 (F13: four classes; 심화C and 케어D are
       not driven here). The class with class today gets a session (code + window) through the real CLI; the other
       one has no class today (on 일 고1A gets the session). */
    const today = kstToday();
    const wdNow = (new Date(today + 'T00:00:00Z').getUTCDay() + 6) % 7;
    const CLASS_ON = [0, 2, 4].includes(wdNow) ? '기초B' : '고1A';
    const CLASS_OFF = CLASS_ON === '고1A' ? '기초B' : '고1A';
    const nowHM = new Date(Date.now() + 9 * 3600e3).toISOString().slice(11, 16);
    const pickFrom = (cls) => JSON.parse(execFileSync(PYTHON, ['-c', `
import json, sqlite3, sys
c = sqlite3.connect(sys.argv[1]); c.row_factory = sqlite3.Row
r = c.execute("""SELECT s.student_token, s.parent_token, s.display_name FROM students_public s JOIN classes k ON k.id = s.class_id
  WHERE k.name = ? AND s.active = 1 AND s.id NOT IN (SELECT student_id FROM submissions WHERE day = ?) ORDER BY s.id LIMIT 1""", (sys.argv[3], sys.argv[2])).fetchone()
print(json.dumps(dict(r)))`, env.DAYBOOK_DB, today, cls], { encoding: 'utf-8' }));
    const pick = pickFrom(CLASS_ON), offPick = pickFrom(CLASS_OFF);
    const started = daybook('session', 'start', CLASS_ON, '--at', nowHM);
    const CODE = (started.match(/오늘의 코드: (\d{4})/) || [])[1];
    check(!!CODE && /sent to the Worker/.test(started), `daybook session start ${CLASS_ON} --at ${nowHM}: code drawn and sent to the Worker`);
    // F14: a weekly-test round open now for both demo classes driven here (the round id never reaches a page)
    const RID = 'Adaptive_Screeners_1S_E2E_G10';
    const kst = (ms) => new Date(Date.now() + ms + 9 * 3600e3).toISOString().slice(0, 16).replace('T', ' ');
    const radd = daybook('round', 'add', RID, '--grade', '1학년', '--classes', `${CLASS_ON},${CLASS_OFF}`, '--quizzes', '20',
      '--open', kst(-3600e3), '--close', kst(2 * 86400e3));
    check(/주간 테스트/.test(radd) && /round\(s\) accepted/.test(radd), `daybook round add: the round is open and sent to the Worker as a hash`);
    const MTEXT = '다 같이 찾은 꼭짓점';
    const mAdd = (answer) => { try { return execFileSync(DAYBOOK, ['moment', 'add', CLASS_ON, '--photo', path.join(cfgCopy.moments_dir, '_demo_src', 'm3.jpg'), '--text', MTEXT],
      { env: { ...env, PYTHONIOENCODING: 'utf-8' }, cwd: ROOT, input: answer, encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'] }); } catch (e) { return 'EXIT ' + e.status + ' ' + e.stdout; } };
    check(/^EXIT [12] /.test(mAdd('n\n')), 'daybook moment add: answering N to the faces/names check posts nothing');
    check(/EXIF removed\) sent to the Worker/.test(mAdd('y\n')), 'daybook moment add: y → EXIF stripped, sent to the Worker for the same-day view');
    const tok = pick.student_token;

    console.log('\n[2] student 오늘 tab: photo -> shrink -> Worker -> 오늘 완료');
    const PHONE = { width: 390, height: 844 };
    const phone = await browser.newContext({ viewport: PHONE });
    const page = await phone.newPage();
    const errs = [];
    page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
    page.on('pageerror', (e) => errs.push(String(e)));
    const foreignBefore = [];
    page.on('request', (r) => { const u = r.url(); if (!u.startsWith(PAGES) && !u.startsWith(WORKER) && !u.startsWith('blob:') && !u.startsWith('data:')) foreignBefore.push(u); });
    await page.route(/youtube-nocookie\.com/, (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>stub</title>' }));
    const momentReqStates = [];
    await page.route(/127\.0\.0\.1:8788\/moments\?/, async (route) => {
      momentReqStates.push(await route.request().frame().evaluate(() => document.readyState).catch(() => 'unknown'));
      route.continue();
    });
    await page.goto(`${PAGES}/s/${tok}/?set=Adaptive_Screeners_3S_Nov06_G10#today`, { waitUntil: 'networkidle' });
    check((await page.textContent('#r-title')) === '오늘 수업' && await page.isVisible('#steps') && await page.isVisible('#st-checkin') && await page.isHidden('#st-idle'),
      'class day: the ritual starts with ① 출석');
    await page.click('#ck-open');
    for (const k of (CODE === '0000' ? '1111' : '0000')) await page.click(`#keys button[data-k="${k}"]`);
    await page.waitForFunction(() => /코드가 달라요/.test(document.getElementById('pad-err').textContent), null, { timeout: 5000 }).catch(() => {});
    check(/코드가 달라요 \(4번 남음\)/.test(await page.textContent('#pad-err')), `a wrong code is refused ("${await page.textContent('#pad-err')}")`);
    await shot(page, 'student-checkin-pad.png');
    for (const k of CODE) await page.click(`#keys button[data-k="${k}"]`);
    await page.waitForSelector('#st-idle:not([hidden])', { timeout: 5000 }).catch(() => {});
    check(await page.isHidden('#pad') && /^\d\d:\d\d$/.test(await page.textContent('#sp-ck-v')) && await page.isVisible('#st-idle .plus'),
      `the right code checks in (출석 ${await page.textContent('#sp-ck-v')}) and moves on to ② 과제 끝`);
    check((await page.textContent('#cta')) === '과제 끝 · 찍고 가자', 'step ③ reads "과제 끝 · 찍고 가자"');
    await page.waitForSelector(`.sv-group[data-story="moments"] .slide .mtext:text("${MTEXT}")`, { state: 'attached', timeout: 5000 }).catch(() => {});
    check(momentReqStates.length > 0 && momentReqStates.every((x) => x === 'complete'), `live moments are fetched only after load (${momentReqStates.join(',')})`);
    check(await page.locator(`.sv-group[data-story="moments"] .slide`, { hasText: MTEXT }).count() === 1 && await page.isVisible('.story[data-story="moments"]'),
      'the 오늘의 장면 ring holds today\'s live moment');
    await page.click('#sp-m');
    await page.waitForSelector('#sv:not([hidden])');
    const liveSlide = page.locator('.sv-group[data-story="moments"] .slide', { hasText: MTEXT });
    while (await liveSlide.isHidden()) { await page.click('#sv-next'); await page.waitForTimeout(60); }
    await page.waitForFunction((t) => { const s = Array.from(document.querySelectorAll('.s-moment')).find((x) => x.textContent.includes(t)); return s && s.querySelector('img').src.startsWith('blob:'); }, MTEXT, { timeout: 5000 }).catch(() => {});
    await liveSlide.locator('.rx[data-r="fire"]').click();
    await page.waitForFunction(() => document.querySelector('.s-moment:not([hidden]) .rx[data-r="fire"] .rc').textContent === '1', null, { timeout: 5000 }).catch(() => {});
    await liveSlide.locator('.rx[data-r="clap"]').click();
    await page.waitForTimeout(300);
    const rx = await liveSlide.locator('.reacts').innerText();
    check(/🔥\s*1/.test(rx) && !/👏\s*\d/.test(rx), `a reaction counts once per student (${rx.replace(/\s+/g, ' ')})`);
    await shot(page, 'student-moment.png');
    await page.keyboard.press('Escape');
    check((await page.textContent('#sp-m-v')) === '🔥', 'step ② 장면 shows the reaction');
    const setText = await page.textContent('#today-set');
    check(/^\d{1,2}\/\d{1,2} 과제$/.test(setText) && !/Adaptive|_G\d/.test(setText), `?set= shows a clean label, never the raw id ("${setText}")`);
    const png = path.join(require('os').tmpdir(), 'daybook-e2e-photo.png');
    makePng(png, 3000, 2000);
    await page.setInputFiles('#photo', png);
    await page.waitForSelector('#st-preview:not([hidden])', { timeout: 5000 });
    check(await page.isVisible('#preview') && await page.isVisible('#post'), 'choosing a photo shows the preview with "올리기"');
    const LINE = '통분 이제 이해됐어요';
    check(await page.isVisible('#line') && (await page.getAttribute('#line', 'maxlength')) === '40' && (await page.locator('#chips .cbtn').count()) === 3,
      '오늘 한 줄 (0–40 chars) and three chips before 올리기');
    await page.fill('#line', LINE);
    await page.click('#chips .cbtn[data-chip="ok"]');
    await page.fill('#solved', '75');
    check((await page.inputValue('#solved')) === '60', '오늘 푼 문제 수 is capped at 60');
    await page.fill('#solved', '12');
    await page.click('.sv-b[data-d="1"]');
    check((await page.inputValue('#solved')) === '13', 'the + key adds one (12 -> 13)');
    await page.click('#post');
    await page.waitForSelector('#st-done:not([hidden])', { timeout: 15000 });
    check((await page.textContent('.done-title')) === '오늘 완료', '"오늘 완료" shown');
    check(/연속 \d+일/.test(await page.textContent('#d-streak')), `streak shown: ${await page.textContent('#d-streak')}`);
    check(/반 누적 \+\d+/.test(await page.textContent('#d-class')), `class total shown: ${await page.textContent('#d-class')}`);
    check(await page.isVisible('#st-done label.small'), '"다시 올리기" offered');
    check(/^출석 \d\d:\d\d → 장면 🔥 → 끝 \d\d:\d\d$/.test(await page.textContent('#d-day')), `day summary "${await page.textContent('#d-day')}"`);
    check(/^오늘의 한 장 · \d+\/\d+ 기록됨 \d\d:\d\d$/.test(await page.textContent('#d-shot')), `"${await page.textContent('#d-shot')}" (no photo)`);
    await page.waitForTimeout(250);
    await shot(page, 'student-done.png');
    let subs = await workerGet('/submissions');
    let rec = subs.items.find((i) => i.token === tok);
    check(rec && rec.day === today && rec.n === 1, 'record landed in KV with today\'s Seoul date');
    check(rec && rec.line === LINE && rec.chip === 'ok', 'the line and the chip are stored with the submission');
    const photo1 = await workerGet('/photo?key=' + encodeURIComponent(rec.key));
    const dim = jpegSize(photo1);
    check(dim && Math.max(dim.w, dim.h) <= 1600 && photo1.length < 1024 * 1024, `photo shrunk in the browser: ${fs.statSync(png).size} B png 3000x2000 -> ${photo1.length} B jpeg ${dim && dim.w + 'x' + dim.h}`);
    const ts1 = rec.ts;
    await page.setInputFiles('#photo', png);
    await page.waitForSelector('#st-preview:not([hidden])', { timeout: 5000 });
    await page.click('#post');
    await page.waitForSelector('#d-replaced:not([hidden])', { timeout: 15000 });
    subs = await workerGet('/submissions');
    rec = subs.items.find((i) => i.token === tok);
    check(rec.n === 2 && rec.ts === ts1 && rec.updated > ts1, 'second upload the same day replaced the photo and kept the first ts');
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForSelector('#st-done:not([hidden])', { timeout: 5000 }).catch(() => {});
    check(await page.isVisible('#st-done'), 'reopening the page shows today as done (Worker /status)');

    const offCtx = await browser.newContext({ viewport: PHONE });
    const off = await offCtx.newPage();
    await off.goto(`${PAGES}/s/${offPick.student_token}/#today`, { waitUntil: 'networkidle' });
    check((await off.textContent('#r-title')) === '오늘 공부 인증' && await off.isHidden('#steps') && await off.isHidden('#st-checkin') && await off.isVisible('#st-idle'),
      `no class today (${CLASS_OFF}): 출석 hides, the tab is "오늘 공부 인증" (photo only)`);
    await offCtx.close();

    console.log('\n[2c] 주간 테스트 (F14): the report card on 오늘, edit until it closes, no score shown back');
    {
      const wc = await browser.newContext({ viewport: PHONE });
      const wp = await wc.newPage();
      const werrs = [];
      wp.on('console', (m) => { if (m.type() === 'error') werrs.push(m.text()); });
      await wp.goto(`${PAGES}/s/${pick.student_token}/#today`, { waitUntil: 'networkidle' });
      await wp.waitForSelector('#wt-card:not([hidden])', { timeout: 5000 }).catch(() => {});
      check(await wp.isVisible('#wt-card') && /주간 테스트/.test(await wp.textContent('#wt-label')) && /까지 입력해요/.test(await wp.textContent('#wt-state')),
        `the open round shows as a card ("${await wp.textContent('#wt-label')}" · ${await wp.textContent('#wt-state')})`);
      check(!(await wp.content()).includes(RID), 'the round id is not on the page');
      await wp.click('#wt-open');
      check((await wp.locator('#wt-grid .wq').count()) === 20 && (await wp.locator('#wt-grid .wq.c').count()) === 20, '20 quiz tiles, all ○ 맞음 by default');
      await wp.click('#wt-grid .wq[data-k="3"]');
      await wp.click('#wt-grid .wq[data-k="7"]');
      await wp.click('#wt-grid .wq[data-k="12"]'); await wp.click('#wt-grid .wq[data-k="12"]');
      check((await wp.textContent('#wt-grid .wq[data-k="3"] .wm')) === '✕' && (await wp.textContent('#wt-grid .wq[data-k="12"] .wm')) === '–', 'a tap cycles ○ → ✕ → –');
      for (const k of ['7', '12', '3', '5']) await wp.click(`#wt-hard .wh[data-k="${k}"]`);
      check((await wp.locator('#wt-hard .wh[aria-pressed="true"]').count()) === 3 && /3개까지/.test(await wp.textContent('#wt-err')), '가장 어려웠던 문제: at most 3');
      await wp.click('#wt-send');
      check(await wp.isVisible('#wt') && /하나 이상/.test(await wp.textContent('#wt-err')), '✕ or – without a reason: asks for one, nothing sent');
      await wp.click('#wt-reasons .cbtn[data-r="계산실수"]');
      await wp.click('#wt-reasons .cbtn[data-r="시간부족"]');
      await wp.fill('#wt-wish', '풀이 영상을 더 보고 싶어요');
      await wp.fill('#wt-meta', '마라톤');
      await shot(wp, 'weekly-test.png');
      await wp.click('#wt-send');
      await wp.waitForSelector('#wt', { state: 'hidden', timeout: 6000 }).catch(() => {});
      const doneTxt = await wp.textContent('#wt-state');
      check(/^제출 완료 · 수정 가능 \(. \d\d:\d\d까지\)$/.test(doneTxt) && (await wp.textContent('#wt-open')) === '수정하기', `제출 → "${doneTxt}"`);
      await wp.reload({ waitUntil: 'networkidle' });
      await wp.click('#wt-open');
      check((await wp.locator('#wt-grid .wq.x').count()) === 2 && (await wp.locator('#wt-grid .wq.m').count()) === 1
        && (await wp.inputValue('#wt-meta')) === '마라톤', 'reopening shows the student\'s own report to edit');
      await wp.click('#wt-grid .wq[data-k="3"]'); await wp.click('#wt-grid .wq[data-k="3"]');     // ✕ → – → ○
      await wp.click('#wt-send');
      await wp.waitForSelector('#wt', { state: 'hidden', timeout: 6000 }).catch(() => {});
      const mine = (await workerGet('/reports')).items.filter((i) => i.token === pick.student_token);
      check(mine.length === 1 && mine[0].n === 2 && mine[0].incorrect === '7' && mine[0].missed === '12', `an edit replaces the report (records ${mine.length}, edits ${mine[0] && mine[0].n}, ✕ ${mine[0] && mine[0].incorrect})`);
      check(!/점수|등수|순위|\d+점/.test(await wp.textContent('#tab-today')), 'no score or ranking is shown back');
      check(werrs.length === 0, `0 console errors (${werrs.slice(0, 2).join(' | ')})`);
      await wc.close();
    }

    console.log('\n[2b] stories, reels, profile');
    const nStories = await page.locator('.story').count();
    check(nStories >= 3, `${nStories} story rings (새 영상, 연속, 반 목표, 선생님)`);
    const rings = await page.evaluate(() => ({
      img: !!document.querySelector('.story[data-story="videos"] .story-in img[src^="/t/"]'),
      prog: (document.querySelector('.story[data-story="streak"] .ring.prog') || { style: {} }).style.getPropertyValue('--p'),
      donut: !!document.querySelector('.story[data-story="goal"] svg.donut .d-fill'),
      mono: (document.querySelector('.story[data-story="teacher"] .mono') || {}).textContent }));
    check(rings.img && rings.prog !== '' && rings.donut && rings.mono === 'AMP', `rings hold content: newest thumbnail, streak progress (${rings.prog}%), goal donut, AMP monogram`);
    await page.click('.story[data-story="videos"]');
    await page.waitForSelector('#sv:not([hidden])', { timeout: 3000 });
    const segN = await page.locator('#sv-segs .sv-seg').count();
    check(segN >= 1 && (await page.locator('.sv-group[data-story="videos"] .slide:not([hidden])').count()) === 1, `story viewer open with ${segN} segment(s), one slide visible`);
    await shot(page, 'student-stories.png');
    await page.click('#sv-x');
    check(await page.isHidden('#sv'), 'story viewer closes');
    check(await page.getAttribute('.story[data-story="videos"]', 'class').then((c) => c.includes('seen')), 'opened ring turns grey (seen)');
    await page.click('.story[data-story="streak"]');
    await page.waitForSelector('#sv:not([hidden])');
    check(/^\d+$/.test((await page.textContent('.sv-group[data-story="streak"] .big-n')).trim()), 'streak story shows the big number');
    await page.keyboard.press('Escape');
    check(await page.isHidden('#sv'), 'Escape closes the story');
    await page.reload({ waitUntil: 'networkidle' });
    check(await page.getAttribute('.story[data-story="videos"]', 'class').then((c) => c.includes('seen')), 'seen state survives a reload (localStorage)');

    await page.click('#t-videos');
    const nShorts = JSON.parse(fs.readFileSync(path.join(ROOT, 'demo', 'videos.demo.json'), 'utf-8')).filter((v) => v.kind === 'short').length;
    const feedInfo = await page.evaluate(() => {
      const f = document.getElementById('feed'), reels = Array.from(f.querySelectorAll('.reel'));
      const m = f.querySelector('.media.short').getBoundingClientRect();
      return { snap: getComputedStyle(f).scrollSnapType, h: f.clientHeight, heights: reels.map((r) => r.getBoundingClientRect().height), n: reels.length,
        ratio: m.width / m.height, longInFeed: f.querySelectorAll('.media.wide').length };
    });
    check(feedInfo.n === nShorts && feedInfo.longInFeed === 0, `reels show only kind "short" (${feedInfo.n}/${nShorts})`);
    check(feedInfo.snap.includes('y') && feedInfo.snap.includes('mandatory') && feedInfo.heights.every((h) => Math.abs(h - feedInfo.h) <= 1), `snap feed: each reel exactly one screen (${feedInfo.h}px)`);
    check(Math.abs(feedInfo.ratio - 9 / 16) < 0.02, `reel media is 9:16 (${feedInfo.ratio.toFixed(3)})`);
    await shot(page, 'student-reels.png');
    await page.evaluate(() => { const f = document.getElementById('feed'); f.scrollTop = f.clientHeight; });
    await page.waitForTimeout(400);
    const second = await page.evaluate(() => { const f = document.getElementById('feed'); return Math.round(f.querySelectorAll('.reel')[1].getBoundingClientRect().top - f.getBoundingClientRect().top); });
    check(Math.abs(second) <= 1, 'scrolling one screen shows exactly the next reel');
    check(foreignBefore.length === 0, `0 third-party requests before any tap (${foreignBefore.length})`);
    await page.locator('.reel').nth(1).locator('button.play').click();
    const src = await page.locator('.reel').nth(1).locator('iframe').getAttribute('src');
    check(/^https:\/\/www\.youtube-nocookie\.com\/embed\/[A-Za-z0-9_-]{11}\?/.test(src || ''), `tapping ▶ inserts the nocookie iframe (${(src || '').split('?')[0]})`);
    check((await page.locator('.reel').nth(1).locator('iframe').getAttribute('referrerpolicy')) === 'strict-origin', 'iframe sends only the origin as referrer');
    await page.click('#grid-toggle');
    const grid = await page.evaluate(() => { const r = document.querySelectorAll('#feed .reel'); return { cls: document.getElementById('feed').className, sameRow: Math.abs(r[0].getBoundingClientRect().top - r[1].getBoundingClientRect().top) < 2 }; });
    check(grid.cls.includes('grid') && grid.sameRow && (await page.getAttribute('#grid-toggle', 'aria-pressed')) === 'true', '목록 toggle switches to a 2-column grid');
    await page.click('#grid-toggle');
    check(!(await page.getAttribute('#feed', 'class')).includes('grid'), 'and back to the feed');
    await page.click('#v-long');
    check((await page.locator('#tab-videos iframe').count()) === 0 && await page.isHidden('#feed') && await page.isVisible('#longs'), '강의 sub-tab: the shorts feed hides and its video stops');
    const longInfo = await page.evaluate(() => {
      const box = document.getElementById('longs').getBoundingClientRect();
      const cards = Array.from(document.querySelectorAll('#longs .lcard'));
      const visible = cards.filter((c) => { const r = c.getBoundingClientRect(); return r.top >= box.top - 1 && r.bottom <= box.bottom + 1; }).length;
      const m = cards[0].querySelector('.media.wide').getBoundingClientRect();
      return { n: cards.length, visible, ratio: m.width / m.height, shortInLongs: document.querySelectorAll('#longs .media.short').length };
    });
    check(longInfo.visible >= 2 && longInfo.shortInLongs === 0 && Math.abs(longInfo.ratio - 16 / 9) < 0.02, `long videos: 16:9 cards, ${longInfo.visible} fully on one 844 px screen (of ${longInfo.n})`);
    await shot(page, 'student-long.png');
    await page.locator('#longs .lcard').first().locator('button.play').click();
    check(/youtube-nocookie\.com\/embed\//.test(await page.locator('#longs .lcard').first().locator('iframe').getAttribute('src') || ''), 'tapping ▶ on a long card plays it inline');

    await page.click('#t-me');
    check(await page.isHidden('#stories'), 'stories row hidden on 내 기록');
    check((await page.getAttribute(`#tiles li[data-date="${today}"]`, 'class') || '').match(/\b(sub|hl)\b/), '내 기록 grid marks today as submitted');
    const tileInfo = await page.evaluate(() => {
      const t = Array.from(document.querySelectorAll('#tiles .tile'));
      const kinds = {};
      t.forEach((x) => { const k = ['hl', 'sub', 'miss', 'off', 'open'].find((c) => x.classList.contains(c)) || 'none'; kinds[k] = (kinds[k] || 0) + 1; });
      const bg = (sel) => { const e = document.querySelector('#tiles .tile.' + sel); return e ? getComputedStyle(e).backgroundImage : ''; };
      return { n: t.length, kinds, heads: Array.from(document.querySelectorAll('#tiles .mhead')).map((h) => h.textContent),
        hearts: document.querySelectorAll('#tiles .tile.hl .hrt').length, hl: document.querySelectorAll('#tiles .tile.hl').length,
        subBg: bg('sub'), hlBg: bg('hl'), missShadow: (() => { const e = document.querySelector('#tiles .tile.miss'); return e ? getComputedStyle(e).boxShadow : 'none'; })(),
        imgs: document.querySelectorAll('#tiles img').length };
    });
    check(tileInfo.n >= 21 && tileInfo.imgs === 0 && !tileInfo.kinds.none, `day tiles by status ${JSON.stringify(tileInfo.kinds)}, no photos`);
    check(tileInfo.hl > 0 && tileInfo.hearts === tileInfo.hl && tileInfo.subBg !== tileInfo.hlBg && /0\.16/.test(tileInfo.subBg), 'submitted = soft tint; only 3-point days get the full gradient + ♥');
    check(!tileInfo.kinds.miss || tileInfo.missShadow !== 'none', 'a missed class day has a thin outline');
    check(tileInfo.heads.length >= 2 && tileInfo.heads.every((h) => /^\d{1,2}월$/.test(h)), `month headers ${tileInfo.heads.join(', ')}`);
    const months = await page.locator('.hl-b').count();
    check(months === tileInfo.heads.length, `${months} month highlights`);
    const lastKey = await page.locator('.hl-b').last().getAttribute('data-month');
    await page.locator('.hl-b').last().click();
    await page.waitForTimeout(700);
    const jump = await page.evaluate((k) => { const h = document.getElementById('m-' + k).getBoundingClientRect(), m = document.getElementById('s-main').getBoundingClientRect(); return Math.round(h.top - m.top); }, lastKey);
    check(jump >= -2 && jump <= 24, `tapping a month highlight scrolls the grid to it (${jump}px from the top)`);
    await page.evaluate(() => { document.getElementById('s-main').scrollTop = 0; });
    await page.waitForTimeout(500);
    check((await page.locator('#mylines li', { hasText: LINE }).count()) === 1, 'the student sees their own line on the profile');
    check((await page.locator('.msgs .bubble').count()) >= 1 && (await page.locator('#tab-me input, #tab-me textarea').count()) === 0, 'teacher messages as bubbles, no input box');
    const meOverflow = await page.evaluate(() => { const m = document.getElementById('s-main'); return m.scrollWidth - m.clientWidth; });
    const tileW = await page.evaluate(() => Array.from(document.querySelectorAll('#tiles .tile')).slice(0, 3).map((t) => t.getBoundingClientRect().right));
    check(meOverflow === 0 && Math.max(...tileW) <= PHONE.width, `profile grid fits 3 per row inside ${PHONE.width} px (overflow ${meOverflow}px)`);
    await shot(page, 'student-profile.png');
    check(errs.length === 0, `no console errors during the student flows (${errs.length}) ${errs.slice(0, 2).join(' | ')}`);

    console.log('\n[3] handout QR /go/?set=… (no token in the QR)');
    await page.goto(`${PAGES}/go/?set=Adaptive_Screeners_3S_Nov13_G10`);
    await page.waitForURL(/\/s\//, { timeout: 5000 }).catch(() => {});
    const u = new URL(page.url());
    check(u.pathname === `/s/${tok}/` && u.searchParams.get('set') === 'Adaptive_Screeners_3S_Nov13_G10' && u.hash === '#today', 'a phone that opened its link once lands on its own 오늘 tab');
    const fresh = await browser.newContext({ viewport: { width: 360, height: 740 } });
    const fp = await fresh.newPage();
    await fp.goto(`${PAGES}/go/?set=Adaptive_Screeners_3S_Nov13_G10`, { waitUntil: 'networkidle' });
    check(new URL(fp.url()).pathname === '/go/' && (await fp.textContent('#go-msg')).includes('먼저 선생님이 준 내 링크를 한 번 열어 주세요'), 'a phone that never opened its link sees the message');
    await fresh.close();

    console.log('\n[3b] offline (F9): the shell opens without network, a submit waits and is sent once');
    {
      const oc = await browser.newContext({ viewport: PHONE });
      const op = await oc.newPage();
      const url = `${PAGES}/s/${offPick.student_token}/#today`;
      await op.goto(url, { waitUntil: 'networkidle' });
      check(await op.isVisible('#a2hs'), '"홈 화면에 추가" hint on the first visit');
      await op.evaluate(() => navigator.serviceWorker.ready.then(() => true));
      await op.reload({ waitUntil: 'networkidle' });                       // now controlled: the page is cached
      check(await op.isHidden('#a2hs'), 'the hint is shown only once');
      await oc.setOffline(true);
      await op.reload({ waitUntil: 'domcontentloaded' });
      await op.waitForSelector('#tab-today', { timeout: 5000 });
      check(await op.isVisible('#st-idle .plus') && (await op.title()) === TITLE, 'offline reload: the cached shell opens');
      await op.setInputFiles('#photo', png);
      await op.click('#post');
      await op.waitForSelector('#st-queued:not([hidden])', { timeout: 8000 }).catch(() => {});
      check(await op.isVisible('#st-queued'), 'an offline submit waits on the phone ("보낼 준비 완료")');
      await oc.setOffline(false);                                          // the 'online' event sends it
      await op.waitForSelector('#st-done:not([hidden])', { timeout: 8000 }).catch(() => {});
      await op.reload({ waitUntil: 'networkidle' });                       // and the next open does not send it again
      await op.waitForSelector('#st-done:not([hidden])', { timeout: 8000 }).catch(() => {});
      const recs = (await workerGet('/submissions')).items.filter((i) => i.token === offPick.student_token);
      const left = await op.evaluate(() => new Promise((res) => { const r = indexedDB.open('daybook', 1); r.onsuccess = () => { const q = r.result.transaction('queue').objectStore('queue').getAll(); q.onsuccess = () => res(q.result.length); }; }));
      check(await op.isVisible('#st-done') && recs.length === 1 && recs[0].n === 1 && left === 0, `back online: sent exactly once (records ${recs.length}, uploads ${recs[0] && recs[0].n}, queue ${left})`);
      // F14: a weekly-test report made offline waits in the same queue and is sent once
      await op.waitForSelector('#wt-card:not([hidden])', { timeout: 5000 }).catch(() => {});
      await oc.setOffline(true);
      await op.reload({ waitUntil: 'domcontentloaded' });
      await op.waitForSelector('#wt-card:not([hidden])', { timeout: 5000 }).catch(() => {});
      check(await op.isVisible('#wt-card'), 'offline: the open round is still shown (cached from the last visit)');
      await op.click('#wt-open');
      await op.click('#wt-grid .wq[data-k="1"]');
      await op.click('#wt-reasons .cbtn[data-r="개념탈락"]');
      await op.click('#wt-send');
      await op.waitForFunction(() => /보낼 준비/.test(document.getElementById('wt-state').textContent), null, { timeout: 6000 }).catch(() => {});
      check(/보낼 준비 완료/.test(await op.textContent('#wt-state')), 'an offline report waits on the phone');
      await oc.setOffline(false);
      await op.waitForFunction(() => /제출 완료/.test(document.getElementById('wt-state').textContent), null, { timeout: 8000 }).catch(() => {});
      await op.reload({ waitUntil: 'networkidle' });
      await op.waitForTimeout(500);
      const reps = (await workerGet('/reports')).items.filter((i) => i.token === offPick.student_token);
      const left2 = await op.evaluate(() => new Promise((res) => { const r = indexedDB.open('daybook', 1); r.onsuccess = () => { const q = r.result.transaction('queue').objectStore('queue').getAll(); q.onsuccess = () => res(q.result.length); }; }));
      check(reps.length === 1 && reps[0].n === 1 && reps[0].incorrect === '1' && left2 === 0 && /제출 완료/.test(await op.textContent('#wt-state')),
        `back online: the report was sent exactly once (records ${reps.length}, sends ${reps[0] && reps[0].n}, queue ${left2})`);
      await oc.close();
    }

    console.log('\n[4] PWA: manifest + icons');
    const man = await (await fetch(`${PAGES}/s/${tok}/manifest.webmanifest`)).json();
    check(man.display === 'standalone' && man.start_url === './#today' && man.theme_color === '#2563eb', 'manifest standalone, start_url = own page, theme #2563eb');
    for (const n of [180, 192, 512]) check((await fetch(`${PAGES}/static/icon-${n}.png`)).ok, `icon ${n}`);
    await phone.close();

    console.log('\n[5] daybook pull -> publish -> parent page ✓ + time');
    console.log('      ' + daybook('pull').trim());
    {
      const outDir = path.join(TMP, 'reports');
      console.log('      ' + daybook('export', 'reports', '--round', RID, '--out', outDir).trim().split('\n')[0]);
      const lines = fs.readFileSync(path.join(outDir, RID + '.csv'), 'utf-8').replace(/^\uFEFF/, '').trim().split('\n');
      check(lines[0] === 'Student ID,Name,Grade,Round ID,Screening Date,Incorrect Quiz #,Missed Quiz #,Most Challenging Quiz,Reasoning,Wishlist,metaphor'
        && lines.length === 3 && lines.some((l) => l.includes('"3,7,12"') && l.includes('"계산실수,시간부족"') && l.includes('마라톤')),
        `pull → export reports: the Notion columns, one row per reporting student (${lines.length - 1} rows)`);
    }
    console.log('      ' + daybook('publish').trim().split('\n').pop());
    for (const cs of SCHEMES) {
      const pctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: cs });
      const pp = await pctx.newPage();
      await pp.goto(`${PAGES}/p/${pick.parent_token}/`, { waitUntil: 'networkidle' });
      await pp.waitForTimeout(150);
      const atBottom = await pp.evaluate(() => window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2);
      check(atBottom, `parent page (${cs}) loads scrolled to the bottom`);
      if (cs === 'light') {
        const solvedTxt = await pp.locator('.msg.newest #solved').innerText().catch(() => '');
        const nSolved = Number((solvedTxt.match(/이번 주 (\d+)문제/) || [])[1] || -1);
        check(nSolved >= 13 && /스스로 기록/.test(solvedTxt), `parent card: this week's 푼 문제 sum includes today's 13 ("${solvedTxt}")`);
        check(/^수업 \d\d:\d\d–\d\d:\d\d/.test(await pp.textContent('#hours')), `parent card shows the class hours ("${await pp.textContent('#hours')}")`);
        const room = await pp.evaluate(() => ({
          weeks: document.querySelectorAll('.msg').length, past: document.querySelectorAll('details.past').length,
          openPast: document.querySelectorAll('details.past[open]').length, newest: document.querySelectorAll('.msg.newest .report').length,
          svgs: document.querySelectorAll('svg.trend').length, svgInNewest: document.querySelectorAll('.msg.newest svg.trend').length,
          sums: Array.from(document.querySelectorAll('details.past > summary')).map((x) => x.textContent.trim()) }));
        check(room.newest === 1 && room.past === room.weeks - 1 && room.openPast === 0, `one open report (newest), ${room.past} earlier weeks collapsed`);
        check(room.svgs === 1 && room.svgInNewest === 1, 'the trend chart appears once, in the newest week');
        check(room.sums.length > 0 && room.sums.every((x) => /^\d+\/\d+–\d+\/\d+ · 제출 \d+일( · 테스트 (상승|유지|조정))? · 태도 \+\d+$/.test(x)), `summary lines like "${room.sums[0]}"`);
        const clipped = await pp.evaluate(() => Array.from(document.querySelectorAll('svg.trend text')).filter((t) => {
          const b = t.getBBox(), v = t.ownerSVGElement.viewBox.baseVal;
          return b.x < v.x - 0.5 || b.y < v.y - 0.5 || b.x + b.width > v.x + v.width + 0.5 || b.y + b.height > v.y + v.height + 0.5;
        }).map((t) => t.textContent));
        check(clipped.length === 0, `no axis label outside its SVG box (${clipped.join(', ') || 'none'})`);
        const last = pp.locator('.msg.newest');
        const wd = (new Date(today + 'T00:00:00Z').getUTCDay() + 6) % 7;
        const cell = (await last.locator('ol.strip li').nth(wd).innerText()).replace(/\s+/g, ' ');
        check(/✓ \d\d:\d\d/.test(cell), `newest report shows today's ✓ and time (${cell})`);
        const rit = await last.locator('.ritual').innerText().catch(() => '');
        check(/출석 \d\d:\d\d · 끝 \d\d:\d\d/.test(rit) && !/지각/.test(rit), `parent row shows both times (${rit.split('\n').pop()}), no 지각 label`);
        check(!(await pp.content()).includes(LINE), 'the parent page does not show the line (parent_shows_reflection: false)');
        check(/제출 \d+일 \(수업일 \d+일 중 \d+일\)|제출 \d+일$/m.test(await last.innerText()), 'count reads "제출 k일 (수업일 c일 중 d일)"');
        check(/^주간 리포트 · \d{1,2}월 \d주 \(\d+\/\d+–\d+\/\d+\)$/.test((await last.locator('.report h2').innerText()).trim()), `title "${(await last.locator('.report h2').innerText()).trim()}"`);
        check(/업데이트 \d{4}-\d\d-\d\d \d\d:\d\d/.test(await pp.textContent('footer')), 'footer update time');
        check(/(상승|유지|조정)/.test(await last.innerText()) && /이번 주 \+\d+ · 누적 \d+점/.test(await last.innerText()) && await last.locator('.bar .fill').count() === 1, 'tests with direction words, attitude +week / cumulative, class bar');
        const ms = await last.locator('.mstrip li').count();
        check(ms >= 1 && ms <= 3 && !(await last.innerText()).includes('🔥'), `parent report: "이번 주 수업 장면" strip with ${ms} moment(s), no reaction counts`);
        check(await last.locator('.bubble').count() === 1 && /\d\d:\d\d/.test(await last.locator('.mtime').innerText()), 'teacher line as a separate bubble, time under the message');
        check(await pp.locator('input, textarea').count() === 0 && (await pp.textContent('.room-end')).includes('이 방은 주간 기록 전용입니다'), 'no input bar; room note at the bottom');
        await pp.locator('details.past > summary').last().click();
        check(await pp.locator('details.past').last().locator('.report').isVisible(), 'tapping an earlier week opens its report');
        await pp.locator('details.past > summary').last().click();
        await pp.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      }
      await shot(pp, `parent-${cs}.png`);
      await pctx.close();
    }

    console.log('\n[6] board (127.0.0.1): live ✓, attitude buttons write and move the bar');
    board = spawn(DAYBOOK, ['board', CLASS_ON, '--port', String(BOARD_PORT)], { env, cwd: ROOT, stdio: 'ignore' });
    await waitFor(BOARD + '/api/state');
    const bctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const bp = await bctx.newPage();
    const berrs = [];
    bp.on('console', (m) => { if (m.type() === 'error') berrs.push(m.text()); });
    await bp.goto(BOARD + '/', { waitUntil: 'networkidle' });
    check((await bp.textContent('#b-code')).trim() === CODE && /\d\d:\d\d/.test(await bp.textContent('#b-arrivals')), `board shows 오늘의 코드 ${CODE} and the arrival`);
    const row = bp.locator('.row', { hasText: pick.display_name });
    check(/출석 \d\d:\d\d/.test(await row.locator('.ck').innerText()), 'the row shows the pulled 출석 time');
    check((await row.locator('.bline').innerText()).includes(LINE), 'the board shows the student\'s line to the teacher');
    check(daybook('lines', CLASS_ON).includes(LINE), '`daybook lines` shows it in the console');
    await bp.waitForTimeout(500);
    const bstate = await (await fetch(BOARD + '/api/state')).json();
    check(/🔥 1/.test(bstate.reactions || ''), `the board shows the anonymous reaction totals (${bstate.reactions})`);
    const otherId = await bp.locator('.row').filter({ has: bp.locator('button.ckfor') }).first().getAttribute('data-id');
    const other = bp.locator(`.row[data-id="${otherId}"]`);
    await other.locator('button.ckfor').click();
    await bp.waitForTimeout(300);
    check(/출석 \d\d:\d\d/.test(await other.locator('.ck').innerText()), '"출석 처리" checks a student in from the board (source teacher)');
    check(/✓ \d\d:\d\d/.test(await row.locator('.sub').innerText()), `board shows the pulled submission for ${pick.display_name.replace(/./g, '*')}`);
    const before = await bp.$eval('#b-fill', (e) => parseFloat(e.style.width));
    await row.locator('button.pt[data-p="3"]').click();
    await bp.waitForFunction((b) => parseFloat(document.getElementById('b-fill').style.width) > b, before, { timeout: 5000 }).catch(() => {});
    const after = await bp.$eval('#b-fill', (e) => parseFloat(e.style.width));
    check(after > before && (await bp.textContent('#b-three')).includes(pick.display_name), `attitude 3 moved the class bar ${before}% -> ${after}% and lists the student under 오늘 3점`);
    check(await bp.evaluate(() => document.documentElement.scrollWidth) <= 390 && berrs.length === 0, 'board fits 390 px with 0 console errors');
    await shot(bp, 'board.png', { fullPage: true });
    // F13: flags are never on the projected board; "선생님 보기" fetches them on demand and removes them again
    check(await bp.isHidden('#tv-flags') && (await bp.innerHTML('#tv-flags')) === '', 'projected board: no 연락 필요 in the page until "선생님 보기"');
    await bp.click('#tv-toggle');
    await bp.waitForSelector('#tv-flags:not([hidden])', { timeout: 5000 }).catch(() => {});
    const tvN = await bp.locator('#tv-flags .tvf').count();
    check(await bp.isVisible('#tv-flags') && (tvN > 0 || /연락 필요 없음/.test(await bp.textContent('#tv-flags'))), `"선생님 보기" shows this class's flags (${tvN})`);
    await bp.click('#tv-toggle');
    check(await bp.isHidden('#tv-flags') && (await bp.innerHTML('#tv-flags')) === '', 'toggling it off removes them from the page');

    console.log('\n[6a] AMP Live (F7): projector + 2 phones, counts only, no question text on phones');
    const twoToks = JSON.parse(execFileSync(PYTHON, ['-c', `
import json, sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(json.dumps([r[0] for r in c.execute("SELECT s.student_token FROM students_public s JOIN classes k ON k.id = s.class_id WHERE k.name = ? AND s.active = 1 ORDER BY s.id LIMIT 2", (sys.argv[2],))]))`, env.DAYBOOK_DB, CLASS_ON], { encoding: 'utf-8' }));
    const proj = await bctx.newPage();
    await proj.setViewportSize({ width: 1100, height: 800 });
    const perr = [];
    proj.on('console', (m) => { if (m.type() === 'error') perr.push(m.text()); });
    await proj.goto(BOARD + '/live', { waitUntil: 'networkidle' });
    await proj.click('#lv-open');
    await proj.waitForSelector('#lv-room:not([hidden])', { timeout: 5000 });
    const ROOM = (await proj.textContent('#lv-code')).trim();
    check(/^\d{4}$/.test(ROOM), `projector shows the room code ${ROOM}`);
    const phones = [];
    for (const t of twoToks) {
      const c = await browser.newContext({ viewport: PHONE });
      const p = await c.newPage();
      const errs2 = [];
      p.on('console', (m) => { if (m.type() === 'error') errs2.push(m.text()); });
      await p.goto(`${PAGES}/s/${t}/#today`, { waitUntil: 'networkidle' });
      await p.click('#live-open');
      for (const k of ROOM) await p.click(`#keys button[data-k="${k}"]`);
      await p.waitForSelector('#live:not([hidden])', { timeout: 5000 });
      phones.push({ c, p, errs2 });
    }
    check(phones.length === 2 && await phones[1].p.isVisible('#live'), 'two phones joined with their own links + the room code (no nickname)');
    await proj.click('#lv-next');
    for (const ph of phones) await ph.p.waitForSelector('#lv-choices:not([hidden]) button:not([disabled])', { timeout: 6000 });
    check(!(await phones[0].p.content()).includes('x + 5'), 'the phone shows answer buttons only, never the question text');
    await phones[0].p.click('#lv-choices button[data-a="3"]');
    await phones[1].p.click('#lv-choices button[data-a="1"]');
    await proj.waitForFunction(() => /답한 사람 2\//.test(document.getElementById('lv-count').textContent), null, { timeout: 6000 }).catch(() => {});
    check(/답한 사람 2\//.test(await proj.textContent('#lv-count')) && (await proj.textContent('#lv-q')).includes('x + 5'), `projector: question text + live bar (${await proj.textContent('#lv-count')})`);
    await shot(phones[0].p, 'live-phone.png');
    await proj.click('#lv-reveal');
    await phones[0].p.waitForFunction(() => document.getElementById('lv-status').textContent.includes('정답'), null, { timeout: 6000 }).catch(() => {});
    await phones[1].p.waitForFunction(() => document.getElementById('lv-status').textContent.includes('아쉬워요'), null, { timeout: 6000 }).catch(() => {});
    check((await phones[0].p.textContent('#lv-mine')) === '✓' && (await phones[1].p.textContent('#lv-mine')) === '', 'after reveal each phone sees only its own ✓');
    check(/반 정답 1\//.test(await proj.textContent('#lv-count')) && !/[가-힣]{2}\s*✓/.test(await proj.textContent('#lv-room')), 'projector: class count only, no names, no ranking');
    await shot(proj, 'live-projector.png');
    await proj.click('#lv-close');
    await phones[0].p.waitForFunction(() => document.getElementById('lv-status').textContent.includes('끝'), null, { timeout: 6000 }).catch(() => {});
    check((await phones[0].p.textContent('#lv-status')).includes('끝났어요'), 'closing the room ends Live on the phones');
    check(phones.every((ph) => ph.errs2.length === 0) && perr.length === 0, `0 console errors on the projector and the phones ${[...perr, ...phones.flatMap((ph) => ph.errs2)].slice(0, 2).join(' | ')}`);
    for (const ph of phones) await ph.c.close();
    console.log('      ' + daybook('pull').trim());
    const eff = execFileSync(PYTHON, ['-c', `
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*), SUM(correct) FROM effort WHERE source = 'live'").fetchone())`, env.DAYBOOK_DB], { encoding: 'utf-8' }).trim();
    check(eff === '(2, 1)', `daybook pull imported the per-student counts into effort, source 'live' ${eff}`);

    console.log('\n[6b] Teacher tab (F6): roster, notes, publish from the browser');
    const tpage = await bctx.newPage();
    const terr = [];
    tpage.on('console', (m) => { if (m.type() === 'error') terr.push(m.text()); });
    await tpage.goto(BOARD + '/teacher', { waitUntil: 'networkidle' });
    check((await tpage.locator('#roster-rows tr').count()) === 22, `roster lists every class (${await tpage.locator('#roster-rows tr').count()} students)`);
    // F11: add a student from the tab, links + messages to copy; search and the class filter
    await tpage.fill('#sa-full', '도하윤');
    check((await tpage.inputValue('#sa-display')) === '하윤', 'the display name is prefilled with the given name');
    await tpage.fill('#sa-ext', 'S950');
    await tpage.click('#sa-save');
    await tpage.waitForSelector('#sa-links pre', { timeout: 5000 }).catch(() => {});
    check(/\/s\/[a-z2-7]{26}\/\n.*\/p\/[a-z2-7]{26}\//.test(await tpage.textContent('#sa-links pre')) && (await tpage.locator('#sa-links .links button').count()) === 4,
      '학생 추가 shows the new student and parent links with copy buttons (links + 안내 문구)');
    await tpage.waitForFunction(() => document.querySelectorAll('#roster-rows tr').length === 23, null, { timeout: 5000 }).catch(() => {});
    await tpage.fill('#rs-q', '하윤');
    check((await tpage.locator('#roster-rows tr').count()) === 1, 'the search box finds the new student');
    await tpage.fill('#rs-q', '');
    await tpage.selectOption('#rs-class', '케어D');
    check((await tpage.locator('#roster-rows tr').count()) === 5, 'the class filter shows one class (케어D: 5)');
    await tpage.selectOption('#rs-class', '');
    check((await tpage.locator('#notes-rows .nrow input').first().inputValue()).length > 0, 'notes are prefilled (note or data draft)');
    check((await tpage.locator('#weekly-rows pre').count()) >= 5, 'weekly paste messages with copy buttons');
    // F13: the parent-contact desk
    await tpage.waitForSelector('#need-rows .need', { timeout: 5000 }).catch(() => {});
    const needN = await tpage.locator('#need-rows .need').count();
    const topCard = tpage.locator('#need-rows .need').first();
    const topSlug = await topCard.getAttribute('data-slug');
    check(needN >= 3 && (await topCard.locator('.reasons li').count()) >= 2 && (await topCard.locator('.script li').count()) === 3,
      `연락 필요: ${needN} cards; the top one has its reasons and a 3-line call script`);
    check(/수학 실력/.test(await topCard.locator('.script li').first().innerText()) && /AMP 수학입니다/.test(await topCard.locator('details pre').textContent()),
      'the late strong student is on top; the script opens with math strength; a formal 카톡 draft is ready');
    check((await tpage.locator('#status-rows tr').count()) === 22 && (await tpage.locator('#status-rows tr.red').count()) >= 2,
      `연락 현황 lists all 22 students, red first (${await tpage.locator('#status-rows tr.red').count()} red)`);
    check((await tpage.locator('#plan-rows li').count()) >= 5 && /4주 내 연락 \d+%/.test(await tpage.textContent('#plan-cover')),
      `이번 주 할 일: ${await tpage.locator('#plan-rows li').count()} items · ${await tpage.textContent('#plan-total')} · ${await tpage.textContent('#plan-cover')}`);
    await topCard.locator('.csum').fill('출석 시간 같이 목표 잡기로 함');
    await topCard.locator('button.primary').click();
    await tpage.waitForFunction((sl) => !document.querySelector(`#need-rows .need[data-slug="${sl}"]`), topSlug, { timeout: 5000 }).catch(() => {});
    check((await tpage.locator(`#need-rows .need[data-slug="${topSlug}"]`).count()) === 0 && (await tpage.locator(`#status-rows tr[data-slug="${topSlug}"] .dd`).innerText()).startsWith('0'),
      '"통화함" logs the call: the flag clears and 연락 현황 resets to 0 days');
    await tpage.click('#pub-do');
    await tpage.waitForFunction(() => /lint passed|STOPPED/.test(document.getElementById('pub-out').textContent), null, { timeout: 30000 }).catch(() => {});
    check(/lint passed/.test(await tpage.textContent('#pub-out')), `Publish button: ${(await tpage.textContent('#pub-out')).split('\n').filter(Boolean).join(' | ')}`);
    check(terr.length === 0, `Teacher tab: 0 console errors (${terr.length})`);
    await tpage.setViewportSize({ width: 1100, height: 900 });
    await shot(tpage, 'teacher.png', { fullPage: false });
    await bctx.close();

    console.log('\n[7] taste build (F1): only 콩, no Worker, nothing stored');
    console.log('      ' + daybook('publish', '--taste', '--out', TASTE_OUT).trim().split('\n').pop());
    const TASTE = 'http://127.0.0.1:8791', TDIST = TASTE_OUT;
    const tsrv = staticServer(TDIST, 8791);
    try {
      await waitFor(TASTE + '/');
      const pagesT = [];
      const walkT = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walkT(path.join(d, e.name)) : [path.join(d, e.name)]));
      for (const f of walkT(TDIST)) if (f.endsWith('index.html')) pagesT.push(path.relative(TDIST, f).split(path.sep).join('/'));
      check(JSON.stringify(pagesT.sort()) === JSON.stringify(['index.html', 'p/kong/index.html', 's/kong/index.html']), `dist-taste holds only the landing, /s/kong/, /p/kong/ (${pagesT.join(', ')})`);
      const tctx = await browser.newContext({ viewport: PHONE });
      const tp = await tctx.newPage();
      const terrs = [], treqs = [];
      tp.on('console', (m) => { if (m.type() === 'error') terrs.push(m.text()); });
      tp.on('pageerror', (e) => terrs.push(String(e)));
      tp.on('request', (r) => { const u = r.url(); if (!u.startsWith(TASTE) && !u.startsWith('blob:') && !u.startsWith('data:')) treqs.push(u); });
      await tp.goto(TASTE + '/', { waitUntil: 'networkidle' });
      check(await tp.locator('a.btn', { hasText: '학생 화면 보기' }).count() === 1 && await tp.locator('a.btn', { hasText: '학부모 화면 보기' }).count() === 1, 'landing has the two buttons');
      check((await tp.textContent('.taste-bar')) === '체험판 · 기록되지 않아요', 'banner "체험판 · 기록되지 않아요"');
      await shot(tp, 'taste-landing.png');
      await tp.click('a.btn.primary');
      await tp.waitForURL(/\/s\/kong\//);
      await tp.waitForLoadState('networkidle');
      check(await tp.isVisible('.taste-bar') && (await tp.textContent('.who, .sbar')).length > 0, 'student page /s/kong/ with the banner');
      check(/^20$|^21$/.test((await tp.textContent('.story[data-story="streak"] b')).trim()), `콩's streak ring shows ${(await tp.textContent('.story[data-story="streak"] b')).trim()}`);
      check(await tp.isVisible('#st-checkin'), 'taste: 콩 starts with ① 출석 (simulated)');
      await tp.click('#ck-open');
      for (const k of '1234') await tp.click(`#keys button[data-k="${k}"]`);
      await tp.waitForSelector('#st-idle:not([hidden])', { timeout: 3000 });
      check(/^\d\d:\d\d$/.test(await tp.textContent('#sp-ck-v')), 'taste: any 4 digits check in locally');
      await shot(tp, 'taste-ritual.png');
      await tp.setInputFiles('#photo', png);
      await tp.click('#post');
      await tp.waitForSelector('#st-done:not([hidden])', { timeout: 5000 });
      check(/연속 2[12]일/.test(await tp.textContent('#d-streak')), `upload simulated locally: 오늘 완료, ${await tp.textContent('#d-streak')}`);
      await tp.click('#live-open');
      for (const k of '0000') await tp.click(`#keys button[data-k="${k}"]`);
      await tp.waitForSelector('#lv-choices:not([hidden]) button:not([disabled])', { timeout: 6000 });
      await tp.click('#lv-choices button[data-a="3"]');
      await tp.waitForFunction(() => /정답|아쉬워요/.test(document.getElementById('lv-status').textContent), null, { timeout: 12000 }).catch(() => {});
      check(/정답|아쉬워요/.test(await tp.textContent('#lv-status')) && /반 정답 \d\/8/.test(await tp.textContent('#lv-class')), `taste: the 8-student practice room plays itself (${await tp.textContent('#lv-class')})`);
      await tp.click('#lv-x');
      check(await tp.isVisible('#wt-card'), 'taste: a practice 주간 테스트 round is open for 콩');
      await tp.click('#wt-open');
      check((await tp.locator('#wt-grid .wq').count()) === 20, 'taste: 20 quiz tiles');
      await tp.click('#wt-send');
      await tp.waitForSelector('#wt', { state: 'hidden', timeout: 3000 }).catch(() => {});
      await tp.waitForFunction(() => /제출 완료/.test(document.getElementById('wt-state').textContent), null, { timeout: 3000 }).catch(() => {});
      check(/제출 완료/.test(await tp.textContent('#wt-state')), 'taste: 제출 is simulated on the phone (nothing sent)');
      await tp.goto(TASTE + '/p/kong/', { waitUntil: 'networkidle' });
      check(await tp.isVisible('.taste-bar') && /AMP 수학 · 콩/.test(await tp.textContent('.room-title')), 'parent page /p/kong/ with the banner');
      check(treqs.length === 0, `0 requests outside the taste site during the whole visit (${treqs.slice(0, 2).join(', ')})`);
      check(terrs.length === 0, `0 console errors in the taste build (${terrs.length})`);
      await tctx.close();
    } finally { tsrv.close(); }
  } finally {
    await browser.close();
    srv.close();
    worker.kill();
    if (board) board.kill();
  }
  fs.rmSync(TMP, { recursive: true, force: true });
  console.log(failures ? `\nE2E: ${failures} check(s) FAILED` : '\nE2E: all checks passed');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
