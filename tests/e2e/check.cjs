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
 * - screenshots (360 px) into demo/screenshots/
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
const PAGES_PORT = 8790, WORKER_PORT = 8788, BOARD_PORT = 8787;
const PAGES = `http://127.0.0.1:${PAGES_PORT}`, WORKER = `http://127.0.0.1:${WORKER_PORT}`, BOARD = `http://127.0.0.1:${BOARD_PORT}`;
const TEACHER = 'dev-teacher-token';
const TITLE = 'AMP 학습 기록';
const env = {
  ...process.env,
  DAYBOOK_DB: process.env.DAYBOOK_DB || path.join(ROOT, 'demo.sqlite'),
  DAYBOOK_CONFIG: process.env.DAYBOOK_CONFIG || path.join(ROOT, 'demo', 'daybook.demo.json'),
};
const DAYBOOK = process.env.DAYBOOK_BIN || path.join(ROOT, '.venv', 'bin', 'daybook');

let failures = 0;
function check(cond, msg) {
  if (cond) console.log(`  ok   ${msg}`);
  else { failures++; console.log(`  FAIL ${msg}`); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const kstToday = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);

/* ---------- static server for dist/ (Pages-like: dir/ -> dir/index.html) */
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png',
  '.json': 'application/json', '.webmanifest': 'application/manifest+json', '.txt': 'text/plain' };
function staticServer() {
  return http.createServer((req, res) => {
    let p = decodeURIComponent(new URL(req.url, PAGES).pathname);
    if (p.endsWith('/')) p += 'index.html';
    const f = path.join(DIST, p);
    if (!f.startsWith(DIST) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); return res.end('not found'); }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(f)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    fs.createReadStream(f).pipe(res);
  }).listen(PAGES_PORT, '127.0.0.1');
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
const daybook = (...args) => execFileSync(DAYBOOK, args, { env, cwd: ROOT, encoding: 'utf-8' });

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
  fs.mkdirSync(SHOTS, { recursive: true });
  const srv = staticServer();
  const worker = spawn(process.execPath, [path.join(ROOT, 'worker', 'test', 'dev_server.mjs'), '--port', String(WORKER_PORT), '--origin', PAGES, '--teacher', TEACHER], { stdio: 'ignore' });
  let board;
  const browser = await chromium.launch();
  try {
    await waitFor(WORKER + '/health');
    await waitFor(PAGES + '/go/');

    const students = fs.readdirSync(path.join(DIST, 's'));
    const parents = fs.readdirSync(path.join(DIST, 'p'));
    console.log(`\n[1] every page at 360x740 and 390x844, light and dark (${students.length} student, ${parents.length} parent, go, index)`);
    check(students.length === 12 && parents.length === 12, 'dist/s and dist/p exist for all 12 students');
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
    for (const f of execFileSync('find', [DIST, '-type', 'f'], { encoding: 'utf-8' }).trim().split('\n')) big = Math.max(big, fs.statSync(f).size);
    check(big <= 100 * 1024, `every file in dist/ <= 100 KB (largest ${big} B)`);
    const thumbs = fs.existsSync(path.join(DIST, 't')) ? fs.readdirSync(path.join(DIST, 't')) : [];
    check(thumbs.length > 0 && thumbs.every((f) => fs.statSync(path.join(DIST, 't', f)).size <= 60 * 1024), `${thumbs.length} thumbnails in dist/t, each <= 60 KB`);

    /* pick a student of 고1A without a submission today (the demo leaves the first one open) */
    const today = kstToday();
    const pick = JSON.parse(execFileSync(path.join(ROOT, '.venv', 'bin', 'python'), ['-c', `
import json, sqlite3, sys
c = sqlite3.connect(sys.argv[1]); c.row_factory = sqlite3.Row
r = c.execute("""SELECT s.student_token, s.parent_token, s.display_name FROM students_public s JOIN classes k ON k.id = s.class_id
  WHERE k.name = '고1A' AND s.active = 1 AND s.id NOT IN (SELECT student_id FROM submissions WHERE day = ?) ORDER BY s.id LIMIT 1""", (sys.argv[2],)).fetchone()
print(json.dumps(dict(r)))`, env.DAYBOOK_DB, today], { encoding: 'utf-8' }));
    const tok = pick.student_token;

    console.log('\n[2] student 오늘 tab: photo -> shrink -> Worker -> 오늘 완료');
    const phone = await browser.newContext({ viewport: { width: 360, height: 740 } });
    const page = await phone.newPage();
    const errs = [];
    page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
    page.on('pageerror', (e) => errs.push(String(e)));
    const foreignBefore = [];
    page.on('request', (r) => { const u = r.url(); if (!u.startsWith(PAGES) && !u.startsWith(WORKER) && !u.startsWith('blob:') && !u.startsWith('data:')) foreignBefore.push(u); });
    await page.route(/youtube-nocookie\.com/, (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>stub</title>' }));
    await page.goto(`${PAGES}/s/${tok}/?set=Adaptive_Screeners_3S_Nov06_G10#today`, { waitUntil: 'networkidle' });
    check(await page.isVisible('#st-idle .plus'), 'idle state with the round + button');
    check((await page.textContent('#today-set')).includes('Adaptive_Screeners_3S_Nov06_G10'), 'set id from ?set= is shown');
    const png = path.join(require('os').tmpdir(), 'daybook-e2e-photo.png');
    makePng(png, 3000, 2000);
    await page.setInputFiles('#photo', png);
    await page.waitForSelector('#st-preview:not([hidden])', { timeout: 5000 });
    check(await page.isVisible('#preview') && await page.isVisible('#post'), 'choosing a photo shows the preview with "올리기"');
    await page.click('#post');
    await page.waitForSelector('#st-done:not([hidden])', { timeout: 15000 });
    check((await page.textContent('.done-title')) === '오늘 완료', '"오늘 완료" shown');
    check(/연속 \d+일/.test(await page.textContent('#d-streak')), `streak shown: ${await page.textContent('#d-streak')}`);
    check(/반 누적 \+\d+/.test(await page.textContent('#d-class')), `class total shown: ${await page.textContent('#d-class')}`);
    check(await page.isVisible('#st-done label.small'), '"다시 올리기" offered');
    await page.waitForTimeout(250);
    await page.screenshot({ path: path.join(SHOTS, 'student-done.png') });
    let subs = await workerGet('/submissions');
    let rec = subs.items.find((i) => i.token === tok);
    check(rec && rec.day === today && rec.n === 1, 'record landed in KV with today\'s Seoul date');
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

    console.log('\n[2b] stories, reels, profile');
    const nStories = await page.locator('.story').count();
    check(nStories >= 3, `${nStories} story rings (새 영상, 연속, 반 목표, 선생님)`);
    await page.click('.story[data-story="videos"]');
    await page.waitForSelector('#sv:not([hidden])', { timeout: 3000 });
    const segN = await page.locator('#sv-segs .sv-seg').count();
    check(segN >= 1 && (await page.locator('.sv-group[data-story="videos"] .slide:not([hidden])').count()) === 1, `story viewer open with ${segN} segment(s), one slide visible`);
    await page.screenshot({ path: path.join(SHOTS, 'student-stories.png') });
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
    const feedInfo = await page.evaluate(() => {
      const f = document.getElementById('feed'), reels = Array.from(f.querySelectorAll('.reel'));
      return { snap: getComputedStyle(f).scrollSnapType, h: f.clientHeight, heights: reels.map((r) => r.getBoundingClientRect().height), n: reels.length };
    });
    check(feedInfo.snap.includes('y') && feedInfo.snap.includes('mandatory') && feedInfo.heights.every((h) => Math.abs(h - feedInfo.h) <= 1), `snap feed: ${feedInfo.n} reels, each exactly one screen (${feedInfo.h}px)`);
    await page.screenshot({ path: path.join(SHOTS, 'student-reels.png') });
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
    const grid = await page.evaluate(() => { const r = document.querySelectorAll('#feed .reel'); return { cls: document.getElementById('feed').className, sameRow: Math.abs(r[0].getBoundingClientRect().top - r[1].getBoundingClientRect().top) < 2, frames: document.querySelectorAll('#feed iframe').length }; });
    check(grid.cls.includes('grid') && grid.sameRow && (await page.getAttribute('#grid-toggle', 'aria-pressed')) === 'true', '목록 toggle switches to a 2-column grid');
    await page.click('#grid-toggle');
    check(!(await page.getAttribute('#feed', 'class')).includes('grid'), 'and back to the feed');

    await page.click('#t-me');
    check(await page.isHidden('#stories'), 'stories row hidden on 내 기록');
    check((await page.getAttribute(`#tiles li[data-date="${today}"]`, 'class') || '').includes('sub'), '내 기록 grid marks today as submitted');
    check((await page.locator('#tiles li').count()) >= 21 && (await page.locator('#tiles img').count()) === 0, `day tiles for the term (${await page.locator('#tiles li').count()}), no photos`);
    check((await page.locator('.msgs .bubble').count()) >= 1 && (await page.locator('#tab-me input, #tab-me textarea').count()) === 0, 'teacher messages as bubbles, no input box');
    const meOverflow = await page.evaluate(() => { const m = document.getElementById('s-main'); return m.scrollWidth - m.clientWidth; });
    const tileW = await page.evaluate(() => Array.from(document.querySelectorAll('#tiles li')).slice(0, 3).map((t) => t.getBoundingClientRect().right));
    check(meOverflow === 0 && Math.max(...tileW) <= 360, `profile grid fits 3 per row inside 360 px (overflow ${meOverflow}px)`);
    await page.screenshot({ path: path.join(SHOTS, 'student-profile.png') });
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

    console.log('\n[4] PWA: manifest + icons');
    const man = await (await fetch(`${PAGES}/s/${tok}/manifest.webmanifest`)).json();
    check(man.display === 'standalone' && man.start_url === './#today' && man.theme_color === '#2563eb', 'manifest standalone, start_url = own page, theme #2563eb');
    for (const n of [180, 192, 512]) check((await fetch(`${PAGES}/static/icon-${n}.png`)).ok, `icon ${n}`);
    await phone.close();

    console.log('\n[5] daybook pull -> publish -> parent page ✓ + time');
    console.log('      ' + daybook('pull').trim());
    console.log('      ' + daybook('publish').trim().split('\n').pop());
    for (const cs of SCHEMES) {
      const pctx = await browser.newContext({ viewport: { width: 360, height: 740 }, colorScheme: cs });
      const pp = await pctx.newPage();
      await pp.goto(`${PAGES}/p/${pick.parent_token}/`, { waitUntil: 'networkidle' });
      await pp.waitForTimeout(150);
      const atBottom = await pp.evaluate(() => window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2);
      check(atBottom, `parent page (${cs}) loads scrolled to the bottom`);
      if (cs === 'light') {
        const last = pp.locator('.msg').last();
        const wd = (new Date(today + 'T00:00:00Z').getUTCDay() + 6) % 7;
        const cell = (await last.locator('ol.week li').nth(wd).innerText()).replace(/\s+/g, ' ');
        check(/✓ \d\d:\d\d/.test(cell), `latest week card shows today's ✓ and time (${cell})`);
        check(/이번 주 \d+\/\d+회/.test(await last.innerText()), 'card shows "이번 주 k/n회"');
        check(/업데이트 \d{4}-\d\d-\d\d \d\d:\d\d/.test(await pp.textContent('footer')), 'footer update time');
        check(await last.locator('svg.trend polyline').count() > 0 && /(상승|유지|조정)/.test(await last.locator('.dirs').innerText()), 'trend SVG with direction words');
        check(/이번 주 \d+점/.test(await last.innerText()) && /누적 \d+점/.test(await last.innerText()) && await last.locator('.bar .fill').count() === 1, 'attitude week sum + cumulative + class bar');
        check(await last.locator('.bubble').count() === 1, 'teacher note as a chat bubble');
        check(await pp.locator('.sep').count() >= 2 && await pp.locator('input, textarea').count() === 0, `message history with date separators (${await pp.locator('.sep').count()} weeks), no input bar`);
        check((await pp.textContent('.room-end')).includes('이 방은 주간 기록 전용입니다'), 'room note at the bottom');
      }
      await pp.screenshot({ path: path.join(SHOTS, `parent-${cs}.png`) });
      await pctx.close();
    }

    console.log('\n[6] board (127.0.0.1): live ✓, attitude buttons write and move the bar');
    board = spawn(DAYBOOK, ['board', '고1A', '--port', String(BOARD_PORT)], { env, cwd: ROOT, stdio: 'ignore' });
    await waitFor(BOARD + '/api/state');
    const bctx = await browser.newContext({ viewport: { width: 360, height: 740 } });
    const bp = await bctx.newPage();
    const berrs = [];
    bp.on('console', (m) => { if (m.type() === 'error') berrs.push(m.text()); });
    await bp.goto(BOARD + '/', { waitUntil: 'networkidle' });
    const row = bp.locator('.row', { hasText: pick.display_name });
    check(/✓ \d\d:\d\d/.test(await row.locator('.sub').innerText()), `board shows the pulled submission for ${pick.display_name.replace(/./g, '*')}`);
    const before = await bp.$eval('#b-fill', (e) => parseFloat(e.style.width));
    await row.locator('button.pt[data-p="3"]').click();
    await bp.waitForFunction((b) => parseFloat(document.getElementById('b-fill').style.width) > b, before, { timeout: 5000 }).catch(() => {});
    const after = await bp.$eval('#b-fill', (e) => parseFloat(e.style.width));
    check(after > before && (await bp.textContent('#b-three')).includes(pick.display_name), `attitude 3 moved the class bar ${before}% -> ${after}% and lists the student under 오늘 3점`);
    check(await bp.evaluate(() => document.documentElement.scrollWidth) <= 360 && berrs.length === 0, 'board fits 360 px with 0 console errors');
    await bp.screenshot({ path: path.join(SHOTS, 'board.png'), fullPage: true });
    await bctx.close();
  } finally {
    await browser.close();
    srv.close();
    worker.kill();
    if (board) board.kill();
  }
  console.log(failures ? `\nE2E: ${failures} check(s) FAILED` : '\nE2E: all checks passed');
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
