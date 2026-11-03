# DAYBOOK (P0)

**Status (2026-11-04: P0, design v2, F1–F9, hotfix H1–H6):**
1. P1 complete and tested: schema + `students_public`, `init`, `demo`, `roster add/list/import`, Worker `/submit` + `/submissions`, student 오늘 tab, `pull`, parent page, `publish` + no-PII lint, `import screeners`, deploy steps.
2. P2 complete: board + attitude buttons, `score`, class reward bar, `note`, `weekly`, Playwright checks. P3: manifest/icons/standalone, `rotate-tokens`, `export pdf` stub, Worker node tests.
3. `make demo` is green from a clean clone (Python 3.12/3.13, Node 22): 120 pytest + 32 Worker node tests (incl. the class-day budget with a Live round) + the Playwright end-to-end check (every page at 360 and 390 px, light and dark). Windows: `scripts\demo.ps1`.
   Design v2: shorts reels + 강의 cards, story rings with content, status-coloured profile tiles, one weekly report per parent message.
4. Features: taste build (콩, `make deploy-taste`), 출석 with a 4-digit code, 오늘 한 줄, 오늘의 장면 + reactions, 휴강, QR sheet, class band, term report, note drafts,
   the Teacher tab (no terminal on Saturdays), AMP Live (cooperative in-class rounds), the answer-card spike, an offline-capable student page.
   Not done here (needs the teacher): the Cloudflare deploys (redeploy the Worker for 출석/장면), a real-phone install test. Deferred items: `DECISIONS.md`.
5. Real data is attached by the local agents: see `HANDOFF.md`. This repo holds synthetic data only.

DAYBOOK is a single-teacher student ledger. Each day a student photographs today's task and uploads it from their own
page. The teacher can score attitude 0–3 in class. A laptop console keeps everything in SQLite, imports the weekly
screener results and generates a static page per student and per parent. Pages are deployed to Cloudflare Pages,
and a small Cloudflare Worker receives the photos.

    console/   Python console (click): cli, db, models, generate, lint, importers, pull, board, weekly, demo
    worker/    Cloudflare Worker (src/index.js, wrangler.toml) + node tests + a local dev server
    templates/ Jinja pages: base, student, parent, go, board, index; weekly.txt
    static/    app.css, app.js, icons        strings.ko.json   all Korean UI strings
    mappings/  roster.json + synthetic examples                 demo/   seed rounds + screenshots
    tests/     pytest + e2e/check.cjs (Playwright)              dist/   generated (gitignored)

## Setup (≤ 10 commands)

Needs Python ≥ 3.11 (Pillow and segno are installed by `make`) and Node ≥ 22 (the local Worker tests use `node:sqlite`).

```bash
git clone <this private repo> daybook
```

```bash
cd daybook
```

```bash
make demo
```

Windows (no `make`), Windows PowerShell 5.1 or PowerShell 7: `powershell -ExecutionPolicy Bypass -File scripts\demo.ps1`, then `scripts\check.ps1`;
`scripts\deploy-pages.ps1` replaces `make deploy-pages`.

`make demo` creates `.venv`, installs the console, seeds the synthetic demo into `demo.sqlite`, publishes it into
`dist/`, and runs every check. If Playwright is missing it installs it once (`npm install playwright` plus
`npx playwright install chromium`). The demo never touches the real database `daybook.sqlite`.

For real data (after the deploy below):

```bash
cp daybook.example.json daybook.json
```

```bash
.venv/bin/daybook init
```

Then follow `HANDOFF.md` (classes, roster import, links).

## The teacher's week

| when | command | what it does |
|---|---|---|
| once | `daybook class add 고1A --term 2026-11-02..2027-02-26 --days 화,금 --reward "피자 파티"` | a class, its weekdays and its reward |
| once | `daybook roster import roster.csv --map mappings/roster.json --class 고1A` | students from the roster export |
| once | `daybook roster list 고1A --links` | each student's and parent's link, to send privately |
| in class (P2) | `daybook board 고1A` → open http://127.0.0.1:8787 | live ✓, attitude 1·2·3, class bar, "대신 제출" |
| in class, optional | `daybook board 고1A` → "AMP Live" (http://127.0.0.1:8787/live) | open a room from a local question file, show the code; phones answer ①–⑤; ≥ 80 % correct → class +5 |
| in class | `daybook moment add 고1A --photo board.jpg --text "…"` (or the board) | 오늘의 장면: EXIF stripped, ≤ 1080 px, ≤ 200 KB, asks the faces/names check |
| in class | `daybook session start 고1A --at 18:00 [--set <set_id>]` | draws 오늘의 코드 for 출석 and sends it to the Worker; prints the handout QR URL `…/go/?set=<set_id>` (no token) |
| Saturday, no terminal | `daybook board 고1A` → "선생님" (http://127.0.0.1:8787/teacher) | roster, notes (drafts prefilled), imports, 장면/휴강, 발행, 배포, weekly messages |
| Saturday | `daybook pull` | new submissions from the Worker |
| Saturday | `daybook import screeners <dir>` | the screener round snapshots → `tests` (unmatched rows are listed) |
| Saturday | `daybook note <slug> 2026-W45 "한 줄"` | the parent page shows the latest note |
| Saturday | `make deploy-pages` | `daybook publish` (renders `dist/`, runs the no-PII lint, stops on any finding), then `wrangler pages deploy ./dist` |
| Saturday, optional | `daybook weekly 고1A` | paste-ready Korean message per student |

Other commands: `daybook class cancel <class> <date>` (휴강), `daybook qr <class> --set <id>` / `daybook qr sheet <class>`
(handout QR, never a token), `daybook note suggest <class>` (draft notes), `daybook export report <slug>` (one-page
상담 자료, print to PDF), `daybook lines <class>`, `daybook checkin <slug>`, `daybook moment add|list`,
`daybook omr --spike --eval 200` / `--image card.jpg --student <slug> --set <id>` (answer-card reading spike),
`daybook score <class> <date> <slug> <0-3>`, `daybook submit <slug> [--source paper]`,
`daybook roster edit <slug> --display <name>`, `daybook roster rotate-tokens <class> [--student slug]`,
`daybook import tests <csv>`, `daybook pull --photos photos/`, `daybook class list|edit`, `daybook export pdf` (stub).
Every command has `--help`.

## Taste build (F1): let students try it tonight

A separate, safe site with only the synthetic demo student 콩, at `/s/kong/` and `/p/kong/` plus a landing page
("학생 화면 보기", "학부모 화면 보기"). Every page carries the banner "체험판 · 기록되지 않아요"; the Worker is never
called (uploads are simulated in the browser), and the lint fails if a Worker URL or another student's name gets in.
It is built from `demo.sqlite` only and refuses a real database.

```bash
make deploy-taste
```

Windows: `powershell -ExecutionPolicy Bypass -File scripts\deploy-taste.ps1`. Both reseed the demo, run
`daybook publish --taste` into `dist-taste/` and deploy it to the Pages project `daybook-taste` (created on the first
deploy; the URL is printed, usually `https://daybook-taste.pages.dev`). Needs `npx wrangler login` once, nothing else:
no KV, no Worker, no secret. `make taste` builds without deploying.

## Teacher deploy steps (Cloudflare, free plan)

Run these on the laptop, in the repo. You need a Cloudflare account; no payment method for the default KV photo store.

1. Log in (opens a browser):

   ```bash
   cd worker && npm install && npx wrangler login
   ```

2. Create the KV namespace (photos) and the D1 database (records), and paste the printed ids into
   `worker/wrangler.toml` (`REPLACE_WITH_KV_NAMESPACE_ID`, `REPLACE_WITH_D1_DATABASE_ID`). The Worker creates its tables itself.

   ```bash
   npx wrangler kv namespace create SUBMISSIONS
   ```

   ```bash
   npx wrangler d1 create daybook
   ```

   The AMP Live Durable Object (`LiveRoom`, SQLite) needs no setup: `wrangler deploy` creates it from `wrangler.toml`.

3. Create the Pages project once (it prints the URL, usually `https://<name>.pages.dev`; use the printed one):

   ```bash
   npx wrangler pages project create daybook --production-branch main
   ```

4. Put the Pages URL into `worker/wrangler.toml` as `ALLOWED_ORIGIN` (e.g. `https://daybook.pages.dev`, no trailing slash).
5. Set the teacher secret (paste a long random string; the same string goes into `daybook.json` as `teacher_token`):

   ```bash
   npx wrangler secret put TEACHER_TOKEN
   ```

6. Deploy the Worker; note the printed `https://daybook-worker.<subdomain>.workers.dev` URL:

   ```bash
   npx wrangler deploy
   ```

7. In `daybook.json` set `worker_url` (step 6), `pages_url` (step 3) and `teacher_token` (step 5). Then publish and deploy the pages:

   ```bash
   cd .. && make deploy-pages
   ```

   (`make deploy-pages` = `daybook publish`, then `wrangler pages deploy ./dist --project-name daybook --branch main`;
   it stops if the no-PII lint fails. Another project name: `make deploy-pages PAGES_PROJECT=<name>`.)

**Free plan.** Records live in D1, photos in KV; a simulated class day uses under 3 % of every free daily limit
(`worker/test/budget.test.mjs`, table in DECISIONS). The board polls every 60 s, only around class time.

**Retention (7 days).** KV photos expire by themselves (`expirationTtl` = `PHOTO_TTL_DAYS` = 7 in `wrangler.toml`).
Submission records (token, day, time, no photo) are kept 14 days so a weekly pull never misses one.
Optional R2 photo store (needs a payment method): uncomment `[[r2_buckets]]`, set `PHOTO_STORE = "r2"`, then

```bash
npx wrangler r2 bucket create daybook-photos
```

```bash
npx wrangler r2 bucket lifecycle add daybook-photos expire-7d --expire-days 7
```

**Never GitHub Pages.** The pages carry student data behind unguessable links; they must not enter a public git
history. `dist/` and `*.sqlite` are gitignored. Every page sends `noindex` and has the generic title "AMP 학습 기록".

## Worker API

| method | path | auth | body / result |
|---|---|---|---|
| POST | `/submit` | Pages origin only (CORS) | multipart `t` (26-char base32 token), `set` (optional), `photo` (jpeg/png/heic ≤ 8 MB) → `{ok, ts, day, already, days}` |
| GET | `/status?t=<token>` | Pages origin only | `{ok, day, days, checkin_ts, class_today, start}`: recent submission days and today's 출석 |
| GET | `/submissions?since=<iso>` | `X-Teacher-Token` | `{now, items: [{token, set, day, ts, updated, key, n}]}` |
| PUT | `/session` | `X-Teacher-Token` | `{class, date, code, open_from, open_until, start, tokmap}`: today's 출석 code and window (class = an opaque key) |
| POST | `/checkin` | Pages origin only | form `t`, `code` → `{ok, ts, already}` or `{ok: false, error: bad_code\|closed\|no_class_today\|rate_limited}` |
| GET | `/checkins?since=<iso>` | `X-Teacher-Token` | `{now, items: [{token, class, day, ts}]}` |
| PUT | `/tokmap` | `X-Teacher-Token` | `{tokmap: {token: class key}}`: the only tokens `/submit` accepts (sent after every publish and roster change) |
| PUT | `/moment-put` | `X-Teacher-Token` | `{class, date, n (1–3), text, image (base64 JPEG ≤ 200 KB)}`: 오늘의 장면 for the same-day view |
| GET | `/moments?t=<token>` · `/moment?t=&k=` | Pages origin, own class only | moments (text, date, counts, own reaction) · the JPEG |
| POST | `/live/open` · `/live/next` · `/live/reveal` · `/live/close`, GET `/live/state?room=` · `/live/results` | `X-Teacher-Token` | AMP Live room control (kind + answer key only) and per-student counts |
| POST | `/live/join` · `/live/answer`, GET `/live/state?t=&room=` | Pages origin, own class only | AMP Live on the phone: counts and the student's own result |
| POST | `/react` | Pages origin, own class only | form `t`, `k`, `r` (fire\|clap\|mind) → `{counts, mine, already}`; one per token per moment |
| GET | `/photo?key=<key>` | `X-Teacher-Token` | the photo bytes |

One record per token per Seoul day; a second upload the same day replaces the photo and keeps the first `ts`.
Rate limit: `RATE_PER_HOUR` (10) uploads per token per hour → 429. The Worker never sees names.

Manual check against a deployed Worker (replace the URL; the token only needs the right format):

```bash
curl -s -F t=abcdefghijklmnopqrstuvwxyz -F photo=@demo/screenshots/student-done.png\;type=image/png https://daybook-worker.example.workers.dev/submit
```

```bash
curl -s -H "X-Teacher-Token: $DAYBOOK_TEACHER_TOKEN" https://daybook-worker.example.workers.dev/submissions
```

Locally, `node worker/test/dev_server.mjs` runs the same Worker on http://127.0.0.1:8788 with an in-memory KV.

## Checks

- `make test` — pytest: schema, tokens, streak/goal/milestones, importers (unmatched rows reported), the no-PII lint
  (seeded phone, `full_name`, `ext_id` each stop publish), pull, the CLI flow, the board.
- `make test-worker` — node tests: token format, type/size, same-day replacement, R2 replacement, rate limit, CORS, auth.
- `make e2e` — Playwright on every generated page at 360×740 and 390×844, light and dark (0 console errors, no
  horizontal scroll, ≤ 100 KB, generic title, 0 third-party requests, no iframe before a tap), the pick → preview →
  올리기 → 오늘 완료 flow with in-browser shrinking, stories open/close and seen state, the snap feed (one reel per
  screen), tap ▶ → nocookie iframe, the 목록 grid, the profile grid, `/go`, pull → parent room ✓ (opens at the
  bottom), and the board buttons. Screenshots go to `demo/screenshots/`.

Screenshots (synthetic data, 390 px; refreshed only on purpose with `make screenshots` / `check.ps1 -Screenshots`):
`demo/screenshots/student-*.png`, `parent-light.png`, `parent-dark.png`, `board.png`, `teacher.png`, `live-projector.png`,
`live-phone.png`, `taste-*.png`.
