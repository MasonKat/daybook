# DAYBOOK (P0)

**Status (2026-11-04: P0, design v2, F1–F9, hotfix H1–H6, F10 videos per class, F11 members in the Teacher tab, F12 insights, F13 parent-contact desk, F14 weekly-test report):**
1. P1 complete and tested: schema + `students_public`, `init`, `demo`, `roster add/list/import`, Worker `/submit` + `/submissions`, student 오늘 tab, `pull`, parent page, `publish` + no-PII lint, `import screeners`, deploy steps.
2. P2 complete: board + attitude buttons, `score`, class reward bar, `note`, `weekly`, Playwright checks. P3: manifest/icons/standalone, `rotate-tokens`, `export pdf` stub, Worker node tests.
3. `make demo` is green from a clean clone (Python 3.12/3.13, Node 22): 322 pytest + 61 Worker node tests (incl. the class-day budget with a Live round) + the Playwright end-to-end check (every page at 360 and 390 px, light and dark). Windows: `scripts\demo.ps1`.
   Design v2: shorts reels + 강의 cards, story rings with content, status-coloured profile tiles, one weekly report per parent message.
4. Features: taste build (콩, `make deploy-taste`), 출석 with a 4-digit code, 오늘 한 줄, 오늘의 장면 + reactions, 휴강, QR sheet, class band, term report, note drafts,
   the Teacher tab (no terminal on Saturdays), AMP Live (cooperative in-class rounds), the answer-card spike, an offline-capable student page.
   F13: the parent-contact desk on the laptop (연락 필요 with reasons, call scripts and formal 카톡 drafts; 연락 현황;
   the weekly 100-minute planner; monthly good news; per-class 지각 rules; 오늘 푼 문제 수; the self-study block). Nothing is ever sent.
   F14: 주간 테스트 inside DAYBOOK: the teacher opens a round, students report it on their own page (✕/– per quiz, the
   hardest, reasons, a wish, one word), `daybook pull` brings it home and `daybook export reports` writes the Notion columns.
   F10–F12: videos per class/grade (several video files, pins); the Teacher tab adds, moves and retires students, reissues
   links, creates classes and previews a roster CSV; `daybook import insights` shows a student-safe slice of the teacher's own
   analysis as an "이번 주 복습" ring and a parent line.
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

**Windows:** run every `daybook …` command below as `.venv\Scripts\python.exe -m console …` (and tests as
`.venv\Scripts\python.exe -m pytest`). Antivirus (Norton on the teacher's laptop) blocks pip's generated launcher
executables. `scripts\*.ps1` already do this. Mac/Linux: `daybook …` (or `python -m console …`).

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
| once, the real setup (F20) | `daybook setup` (`--from <step>`, `--english`) | the Thursday checklist as a guided wizard: Cloudflare steps to click, daybook.json, classes, roster, consent, rounds, 발행, 배포, each with a preview and a doctor check; resumable (setup_state.json) |
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
| before a weekly test (F14) | `daybook round add Adaptive_Screeners_1S_Nov06_G10 --grade 1학년 --classes 고1A --quizzes 20 --open "2026-11-06 20:00" --close "2026-11-08 23:59"` (or Teacher tab → 주간 테스트) | students see a 주간 테스트 card on 오늘 until it closes; the round id never goes online |
| after the round (F14) | `daybook pull`, then `daybook export reports --round <id> [--format notion-json]` (or 내보내기) | `reports/<round>.csv` with Student ID, Name, Grade, Round ID, Screening Date, Incorrect/Missed Quiz #, Most Challenging Quiz, Reasoning, Wishlist, metaphor |
| Saturday, optional (F12) | `daybook import insights <file.json>` (or Teacher tab → 가져오기) | per student ≤ 3 focus items, ≤ 3 videos and one line each for student and parent (format: `mappings/insights.example.json`) |
| Saturday (F13) | Teacher tab → 연락 필요 / 이번 주 할 일, or `daybook contact needed --script` / `daybook contact plan` | who to call and why (dates, times), a 3-line call script and a formal 카톡 draft; the 100-minute plan with a 4-week rotation; you call, then press 통화함 (or `daybook contact log <slug> --summary "…"`) |
| last week of the month (F13) | Teacher tab → 이달의 좋은 소식, or `daybook contact goodnews` | positive-only drafts for every parent; edit, send yourself, press 메시지 보냄 |
| Saturday (F18.3) | Teacher tab → 학생 리뷰, or `daybook review list` / `review approve <id>…` / `review hide <id>…` | students' one-line class reviews wait for 승인 (nothing reaches classmates before it); 발행 plans each class's random 24-hour vote (a class day, 15:00–21:30) and `daybook pull` counts it; `daybook review open-vote <class>` (지금 투표 열기) opens it now. Extra filter words: `filters/review_words.local.txt` (gitignored) |
| after class (F19.1) | Teacher tab → 음성 메모 가져오기 (paste the CLOVA Note text export), or `daybook voice import memo.txt --class 고1A [--save]` | "칭찬 가람 2점 …", "개념 …", "장면 …", "취소": a preview (누구? lines get candidate chips, nothing stored), then 저장: 🎤 shoutouts (korea mode: the student's "선생님 한마디" and the board's 오늘의 칭찬), teacher-only 개념 메모, 장면 drafts. Call signs: `roster edit <slug> --callsign 7번` |
| any time (F19.2/F19.3) | daybook.json `"rank_scope": "grade"` (or `class` / `all`), `"video_reactions": {…}` labels, `"brand": {"mascot_px": 36}` (28–44) | students react to videos (🔥 👍 🤔 😵 💪; totals from 3 students, 어려워요 only in Teacher tab → 영상); every 발행 updates the 2-week TOP list per grade, shown as the header's "TOP 3" ticker |
| in class (F19.4) | Teacher tab → 리모컨 → open the QR once on your phone (or `daybook remote link`) | the phone shows the class as name buttons: tap a student (or 반 전체), +1/+2/+3, say or type the praise, 저장 (취소 for 2 minutes; works offline); `daybook pull` or the board's poll brings it in |
| in class (F21.2) | board → 수업 문제 열기 (or the remote's 수업 문제, or `daybook classwork open 고1A --label "쎈 이차방정식 A"`) | students mark ○ ✕ – per quiz on their phone until tonight; tags from your own classwork_tags.json (never online) build each student's 내 실력 지도 |
| after class (F21.4/5) | Teacher tab → 실력 지도 · 서술형 · 사진 확인 필요; `daybook classwork import results.csv`, `classwork export <class>` | hand-graded 서술형 (○ △ ✕ or a score), photo-graded marks from a local model (confident ones stored, the rest queued), the tag × student grid as CSV |
| once per class (F13) | `daybook class edit 기초B --start 20:00 --end 22:00 --late-min 30 [--self-study 21:00]` | class hours and the 지각 rule (defaults: 10 min, 3 of the last 6, or an average of 15 min) |

Printable 안내 (G4): `daybook cards students <class>` (cut-out cards) and `daybook cards parents <class>` (A4 letters with
`{원장·학원 문구}` placeholders). Paper never carries a link or a QR: links go by the teacher's 1:1 KakaoTalk only.
Opt-in push (F15.3), once: `daybook push keys --subject mailto:you@example.com` (keys go into daybook.json only), then
in `worker/`: `npx wrangler secret put VAPID_PRIVATE`, `… VAPID_PUBLIC`, `… VAPID_SUBJECT` (the values it prints),
`npx wrangler deploy`, and publish once. Students and parents then see "알림 받기"; after every 발행 one generic push goes
out (never at night, at most one a day). `daybook push send --kind message`, `daybook push count`.
Practice mode (F16): the default is `korea` (the Korean hagwon design). The Teacher tab's "캐나다 연습 모드" switches to
the teacher's personal Manitoba practice layer (no 3-point names on the projector, strength / challenge / next-step
comments with a fuller lint); it is local, logged, and never changes the default. `daybook metrics weekly` and
`daybook export portfolio --synthetic` work in both modes.
운영 가이드 (Korean, for the teacher): `docs/운영가이드.md`; print it with `daybook guide`.
Before every class and every publish: `daybook doctor` (or 점검 in the Teacher tab): ✓ / ⚠ / ✗ with a fix per line.
Backups: `daybook backup [--to <dir>] [--keep 30]`; every 발행 also backs up. Set `"backup_dir"` in daybook.json to a
folder on a **BitLocker-encrypted USB drive** (the database holds full names and phone numbers; DAYBOOK has no encryption
of its own).

Other commands: `daybook class cancel <class> <date>` (휴강), `daybook qr <class> --set <id>` / `daybook qr sheet <class>`
(handout QR, never a token), `daybook note suggest <class>` (draft notes), `daybook export report <slug>` (one-page
상담 자료, print to PDF), `daybook lines <class>`, `daybook checkin <slug>`, `daybook moment add|list`,
`daybook omr --spike --eval 200` / `--image card.jpg --student <slug> --set <id>` (answer-card reading spike),
`daybook score <class> <date> <slug> <0-3>`, `daybook submit <slug> [--source paper]`,
`daybook roster edit <slug> --display <name>`, `daybook roster rotate-tokens <class> [--student slug]`,
`daybook import tests <csv>`, `daybook pull --photos photos/`, `daybook class list|edit`, `daybook export pdf` (stub),
`daybook contact status|log|snooze|profile` (F13: 연락 현황, a call/message record, 이번 주는 보류, 전화/카톡 and 매주/필요할 때만).
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
`daybook publish --taste` into `dist-taste/` and deploy it to the Pages project `amp-taste` (the teacher's existing
project, `https://amp-taste.pages.dev`; `TASTE_PROJECT=<name>` / `-Project <name>` picks another). Needs `npx wrangler login` once, nothing else:
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
| POST | `/review` · `/review/vote`, GET `/reviews?t=` | Pages origin, own class only | F18.3 학생 리뷰: form `t`, `text` (2–60), `hide` → `{ok, status: pending}` or `blocked\|length\|one_a_day` (nothing stored when blocked); a vote `t`, `round`, `review` (`own_review\|already_voted\|closed`); the list: own reviews with status, the class's approved ones, 베스트 리뷰, the open vote |
| POST | `/vreact`, GET `/vreacts?t=` | Pages origin | F19.2: form `t`, `v`, `r` (fire\|good\|curious\|hard\|try, or empty to take it back) → `{mine, totals}`; totals per pool only from 3 students, never `hard` |
| GET | `/vreacts-pull?since=` | `X-Teacher-Token` | every reaction (token, video, reaction, ts), 어려워요 included |
| GET | `/t/remote/` (+ `remote.js`, `sw.js`, `manifest.webmanifest`) | none (a shell, no data) | F19.4: the teacher remote page |
| GET/POST | `/t/remote/roster` · `/today?class=` · `/shout` · `/undo` | `X-Teacher-Token` | the encrypted class lists; today's counts; a save `{id, t (token or *), class, points 1-3, text, at}` (one per student per 3 s; an id seen before is a no-op); undo within 2 minutes |
| POST | `/classwork-mark`; GET `/status` (`classwork`) | Pages origin, own class only | F21.2: form `t`, `s` (set id), `marks` (N of o / x / m) until the end of the class day |
| POST | `/classwork-open` (also `/t/remote/classwork`) · GET `/classwork-pull?since=` | `X-Teacher-Token` | open a set `{class, label, n}` (no quiz text, no tags) · sets and marks |
| PUT | `/remote-roster` · GET `/shouts-pull?since=` | `X-Teacher-Token` | the console: the encrypted lists · shout-outs and undos |
| PUT | `/reviews-sync` · GET `/reviews-pull?since=` | `X-Teacher-Token` | decisions, labels, rounds, winners, the wordlist and HMAC name hashes · new reviews and votes |

One record per token per Seoul day; a second upload the same day replaces the photo and keeps the first `ts`.
Rate limit: `RATE_PER_HOUR` (10) uploads per token per hour → 429. The Worker never sees names, with one exception: the
display name shown with an approved korea-mode 학생 리뷰 whose writer did not hide it (DECISIONS F18.3).

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
