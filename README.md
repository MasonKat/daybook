# DAYBOOK (P0)

**Status (2026-11-03, cloud build):**
1. P1 complete and tested: schema + `students_public`, `init`, `demo`, `roster add/list/import`, Worker `/submit` + `/submissions`, student 오늘 tab, `pull`, parent page, `publish` + no-PII lint, `import screeners`, deploy steps.
2. P2 complete: board + attitude buttons, `score`, class reward bar, `note`, `weekly`, Playwright checks. P3: manifest/icons/standalone, `rotate-tokens`, `export pdf` stub, Worker node tests.
3. `make demo` is green from a clean clone: 57 pytest + 10 Worker node tests + the 360 px Playwright end-to-end check.
4. Not done here (needs the teacher): the Cloudflare deploy, and a real-phone install test on iPhone Safari / Android Chrome. Deferred items are listed in `DECISIONS.md`.
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

Needs Python ≥ 3.11 and Node ≥ 20.

```bash
git clone <this private repo> daybook
```

```bash
cd daybook
```

```bash
make demo
```

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
| in class, optional | `daybook session start 고1A --set <set_id>` | prints the handout QR URL `…/go/?set=<set_id>` (no token) |
| Saturday | `daybook pull` | new submissions from the Worker |
| Saturday | `daybook import screeners <dir>` | the screener round snapshots → `tests` (unmatched rows are listed) |
| Saturday | `daybook note <slug> 2026-W45 "한 줄"` | the parent page shows the latest note |
| Saturday | `make deploy-pages` | `daybook publish` (renders `dist/`, runs the no-PII lint, stops on any finding), then `wrangler pages deploy ./dist` |
| Saturday, optional | `daybook weekly 고1A` | paste-ready Korean message per student |

Other commands: `daybook score <class> <date> <slug> <0-3>`, `daybook submit <slug> [--source paper]`,
`daybook roster edit <slug> --display <name>`, `daybook roster rotate-tokens <class> [--student slug]`,
`daybook import tests <csv>`, `daybook pull --photos photos/`, `daybook class list|edit`, `daybook export pdf` (stub).
Every command has `--help`.

## Teacher deploy steps (Cloudflare, free plan)

Run these on the laptop, in the repo. You need a Cloudflare account; no payment method for the default KV photo store.

1. Log in (opens a browser):

   ```bash
   cd worker && npm install && npx wrangler login
   ```

2. Create the KV namespace and paste the printed `id` into `worker/wrangler.toml` (`REPLACE_WITH_KV_NAMESPACE_ID`):

   ```bash
   npx wrangler kv namespace create SUBMISSIONS
   ```

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
| GET | `/status?t=<token>` | Pages origin only | `{ok, day, days}`: the token's submission days in the last 8 days |
| GET | `/submissions?since=<iso>` | `X-Teacher-Token` | `{now, items: [{token, set, day, ts, updated, key, n}]}` |
| GET | `/photo?key=<key>` | `X-Teacher-Token` | the photo bytes |

One record per token per Seoul day; a second upload the same day replaces the photo and keeps the first `ts`.
Rate limit: `RATE_PER_HOUR` (10) uploads per token per hour → 429. The Worker never sees names.

Manual check against a deployed Worker (replace the URL; the token only needs the right format):

```bash
curl -s -F t=abcdefghijklmnopqrstuvwxyz -F photo=@demo/screenshots/student-today.png\;type=image/png https://daybook-worker.example.workers.dev/submit
```

```bash
curl -s -H "X-Teacher-Token: $DAYBOOK_TEACHER_TOKEN" https://daybook-worker.example.workers.dev/submissions
```

Locally, `node worker/test/dev_server.mjs` runs the same Worker on http://127.0.0.1:8788 with an in-memory KV.

## Checks

- `make test` — pytest: schema, tokens, streak/goal/milestones, importers (unmatched rows reported), the no-PII lint
  (seeded phone, `full_name`, `ext_id` each stop publish), pull, the CLI flow, the board.
- `make test-worker` — node tests: token format, type/size, same-day replacement, R2 replacement, rate limit, CORS, auth.
- `make e2e` — Playwright at 360×740 on every generated page (0 console errors, no horizontal scroll, ≤ 100 KB,
  generic title, 0 third-party requests), the upload → 오늘 완료 flow with in-browser shrinking, `/go`, pull →
  parent ✓, and the board buttons. Screenshots go to `demo/screenshots/`.

Screenshots (synthetic data): `demo/screenshots/student-today.png`, `student-done.png`, `parent.png`, `board.png`.
