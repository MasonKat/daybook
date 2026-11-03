# DECISIONS

One line each. "deferred" = not built in P0, on purpose.

## Process
- 2026-11-03: No `daybook` repo was attached to the cloud session and attaching one was refused, so the build lives in a local git repo; the push needs the teacher (see HANDOFF.md, "Push this repo").
- The build ran on 11-03 (one day early). P1, P2 and most of P3 are done; the dates in this file are build dates.
- Python ≥ 3.11 (tested on 3.12 and 3.13), not pinned to 3.12: nothing in the code needs 3.12.

## Data model
- `classes.term` holds `START..END` (ISO dates); it defines the scheduled class days and `remaining_sessions`.
- `classes.weekday_mask`: Mon=1, Tue=2, Wed=4, Thu=8, Fri=16, Sat=32, Sun=64; `--days 화,금` or `tue,fri`.
- A class day = a `sessions` row OR a scheduled weekday inside the term (so the Friday pilot needs no `session start`).
- `submissions` has no set column (exact schema): the set id is kept in the Worker record and the `submission` event payload.
- One submission row per student per day (`UNIQUE(student_id, day)`); a phone upload after a teacher/paper mark turns it into `phone`.
- One attitude row per student per session; a second tap corrects it (the `events` log keeps every tap).
- `said_aloud` = 1 for board taps, 0 for `daybook score`.
- `events` is append-only, enforced by SQLite triggers; the last pull position is the latest `pull` event (no extra table).
- Extra indexes and a `PRAGMA user_version` = 1 for future migrations.
- Slug = the display name, de-duplicated with a numeric suffix; editable with `roster edit --slug`.
- Times are stored as UTC ISO; "day" is computed with a fixed UTC+9 (Korea has no DST), no tz database needed.

## Derived values
- Streak: today without a submission is still open (neither counts nor breaks); a past class day without one breaks.
- The student page computes the streak for days after the publish date in the browser (Worker `/status` days + schedule), on top of the streak through the day before publish.
- Reward goal = active × remaining sessions × `goal_coef`, rounded up, computed at the first publish and frozen into `reward_goal_points` (a goal that shrank every week would never fill); `daybook class edit <c> --goal 0` recomputes.
- `class_total` = all attitude points of the class's active students over the whole term. Multiple reward cycles per term: deferred.
- "반 누적 +N" on the 오늘 완료 screen = the class total as of the last publish (the Worker never knows classes).
- Trend: least-squares slope per round over the last 6 rounds; thresholds 0.01 (efficiency) and 0.03 (reach); the SVG y-axis is 0–100 % and assumes both values are 0..1 (clamped).
- Class median band on the trend: deferred ("median band deferred").

## Importers
- Screeners: match `student` → `ext_id`, else `name` → `full_name`, else `student` → `full_name` (the field may hold a name); a name shared by two students is reported as ambiguous, never guessed.
- Unmatched rows are printed and counted in the `import_screeners` event; the import still exits 0 so the rest is kept.
- Re-imports upsert on (student, test_name, date) and (ext_id | unambiguous full_name): no duplicates, tokens kept.
- Roster rows not in the CSV are left as they are (never deleted); `Status` values other than `active_values` → inactive.
- A display name equal to the full name (2- or 4-character names) is allowed at import with a warning, and publish refuses it until `roster edit --display`.

## Publish and pages
- Publish renders into `.dist-staging/`, lints it, and swaps it into `dist/` only if clean; a failed publish leaves `dist/` and the DB unchanged.
- Lint errors: phone pattern, any `full_name`, `ext_id` (word-bounded), `parent_phone` digits, non-generic `<title>`/`og:`, third-party resources, files > 100 KB. Another student's display name on a per-student page is a warning only (short given names collide with ordinary words).
- `dist/_headers` sets `X-Robots-Tag: noindex`, `Referrer-Policy: no-referrer` and a CSP (`connect-src` = self + the Worker); `robots.txt` disallows all.
- The parent page has no JavaScript and no links; the student page loads one same-origin script.
- `videos.json` is inlined into the student page at publish (no runtime fetch); only http(s) URLs are kept; newest first per group.
- Each student page has its own `manifest.webmanifest` (`start_url ./#today`) so the home-screen icon opens that student's page; there is no shared `static/manifest.webmanifest` and no service worker (offline: deferred).
- Student JSON data is embedded as `<script type="application/json">` with `<`, `>`, `&` escaped.
- The demo uses its own `demo.sqlite` and refuses to seed a database that has non-demo students.

## Worker
- Added `GET /status?t=` (the token's days in the last 8 days) so a reopened 오늘 tab shows "done" and the streak is right between publishes.
- Records (token, day, ts, key; no photo) live 14 days (`RECORD_TTL_DAYS`), photos 7 days (`PHOTO_TTL_DAYS`): a weekly pull must not miss a 7-day-old record.
- The record is also stored as KV metadata so `/submissions` needs only `list` calls, no per-key reads.
- CORS: a browser request from another origin to `/submit` or `/status` gets 403; requests without `Origin` (curl, console) pass; teacher endpoints need `X-Teacher-Token` (constant-time compare).
- Uppercase tokens are lower-cased before validation; `set` is restricted to `[A-Za-z0-9_-]{0,80}`.
- The KV rate-limit counter is approximate (KV is eventually consistent); good enough for 10/hour.
- If the browser cannot decode the photo (e.g. HEIC on desktop Chrome), the original is sent if ≤ 8 MB.
- Pull reads since the previous Worker `now` − 5 minutes (KV list is eventually consistent); upserts are idempotent.
- Old student tokens from `roster rotate-tokens` still map to their student on pull (kept in the event log).

## Console and board
- Extra commands beyond the brief: `class add|edit|list` (roster commands need a class), `roster edit`, `roster list --links` (to send links), `submit` (paper/teacher mark), `pull --photos`.
- The board creates today's session when it starts; it is bound to 127.0.0.1 and refuses cross-origin POSTs.
- Playwright checks run in Node (`tests/e2e/check.cjs`, same runtime as the Worker tests); `make` installs Playwright only if Node cannot already resolve it.
- Config lives in `daybook.json` (gitignored) or `DAYBOOK_*` environment variables; the teacher token never enters the repo.

## Design pass v1 (2026-11-03, from DESIGN_PROMPT_cloudCC_20261104)
- Interaction patterns only: no third-party logo, name, trademark colour or copied screen; the lint now fails on `kakao`, `instagram` or `#FEE500` (case-insensitive, Korean words such as 카카오톡 are allowed).
- Student theme: dark by default, light when the phone asks for light (`prefers-color-scheme: light`); the reels feed and the story viewer are always dark.
- Tabs moved to a bottom nav (영상 · 오늘 · 내 기록) with our own inline SVG icons; the stories row shows on 영상 and 오늘, not on 내 기록.
- Stories: 새 영상 (videos added in the last 7 days; hidden when none), 연속, 반 목표 (class total only), 선생님 (latest note; hidden when none). The ring is grey once seen; the seen key is a hash of the story's content, so new content colours it again.
- Story slides auto-advance every 5 s (none with reduced motion); tap right/left, swipe down, × or Escape; after the last slide the next story opens.
- 반 목표 story: "다음 단계까지 N점" = the next milestone (or the goal) above the class total; "목표 달성!" once reached.
- Reels: the YouTube iframe is created only on tap, uses `youtube-nocookie.com`, `referrerpolicy="strict-origin"` (YouTube refuses embeds without a referrer; the origin carries no token) and `autoplay=1` (the tap is the user's action). Opening another reel or leaving the tab removes the iframe. CSP gains `frame-src https://www.youtube-nocookie.com`.
- A reel without `yt_id` keeps ▶ as a link to `url`; a reel without `thumb` shows a gradient card with the title.
- Thumbnails need Pillow (new Python dependency): shrunk to 480 px wide, JPEG q70, stepping quality down until ≤ 60 KB; the lint enforces 60 KB for `dist/t/`.
- Demo videos are written to `demo/videos.demo.json` (gitignored) with dates relative to today, so the 새 영상 story is never empty in the demo; demo thumbnails are abstract synthetic images in `demo/thumbs/`.
- 오늘 is two steps: choose a photo → preview with 올리기 / 다시 찍기 → upload. The success screen dropped the reward line (it lives in the 반 목표 story).
- 내 기록 profile: counters 연속 · 제출 (submission days so far) · 누적 점수; one tile per day from the term start (at most 180), newest first, 3 per row; tiles are coloured by status (submitted / class day missed / no class / today open), ♥ = 3 points that day; never a photo.
- Teacher messages on 내 기록 = every note, oldest first (newest at the bottom), dated by the note's week; no input box.
- Parent room: one group of report cards per week from the term start, last 8 weeks; the 2 most recent are open, older ones sit in "이전 기록" (`<details>`). Past weeks are dated on their Saturday (the publish day) and carry no time; the current week shows the publish time.
- Past weeks' cards are computed as of that week's end: submissions, trend (rounds up to then), week sum, cumulative and the class bar (class total then, against today's frozen goal).
- "이번 주 {k}/{n}회": k = submission days, n = days of that week up to the publish day.
- The parent page now loads one script (scroll to the bottom on open); it still has no links and no input.
- The room footer reads "이 방은 주간 기록 전용입니다 · 답장·문의는 카카오톡으로" (both strings from §5).
- The e2e audit renders every page at 360×740 and 390×844 in light and dark; overflow is measured on scroll containers too, not only the document.

## Pipeline fixes from the v2 review (2026-11-03)
- Windows: every text file read/write names UTF-8 (`scripts/check_encoding.py`, enforced by pytest); the CLI writes UTF-8 to the console and recovers undecoded POSIX arguments; a test runs the CLI under an ASCII locale with UTF-8 mode off (stricter than cp1252). `scripts/demo.ps1`, `check.ps1`, `deploy-pages.ps1` are the twins of the make targets; they were written without a PowerShell to run them here, so the local agent runs them first.
- Raw ids: a student page never contains a set/round id. It carries `{fnv1a(set_id): label}` and maps `?set=` in the browser; label = `assignments.json` (optional, gitignored; example in `mappings/`), else the session topic, else "{M/D} 과제" of the session date, else today's date.
- Lint `raw_id` (student and parent pages): `Adaptive_`, `round_`, `_G1`..`_G12`, `.json`. A note that quotes a round id now stops publish.

## Design pass v2 (2026-11-03)
- `videos.json` gains `kind` ("short" | "long", default long) and `duration`. 영상 = 쇼츠 (9:16 full-screen snap reels, shorts only) and 강의 (16:9 cards, 2+ per 844 px screen) as sub-tabs; with no shorts only 강의 shows. Short thumbnails are shrunk to 360 px wide, long to 480 px.
- Story rings hold content: the newest video's thumbnail, the streak number inside a ring filled by this week's submission days / 7 (conic gradient), a goal donut (% of the class goal), an "AMP" monogram for the teacher. Seen = grey.
- Profile tiles: submitted = 16 % tint of the gradient, a 3-point day = full gradient + ♥ (even without a submission), missed class day = thin rose outline + dot, no class = faint grey, today = accent outline. Month headers in the grid; month highlight rings (count of submitted days) jump to the month. Bio line "{class} · {n}번째 기록" (n = submission days).
- Done screen adds "오늘의 한 장 · M/D 기록됨 HH:MM" (time from the upload response, kept per device; from the published data when published the same day). No photo.
- Parent room: one report message per week (title "주간 리포트 · M월 N주 (range)", where N = which Thursday of the month); rows 제출 (7-day strip with ✓ + time; "제출 k일 (수업일 c일 중 d일)"), 주간 테스트 (latest 확보 효율 / 도전 도달 % + direction words; the chart only in the newest week), 태도 ("이번 주 +w · 누적 c점"; the class bar only in the newest week). The teacher's line is a separate bubble. Earlier weeks are one summary line each in a closed `<details>`; the summary uses the efficiency direction as "테스트 …".
- Each past week is dated and timed by the last successful publish inside that week (from the `events` log); without one it is dated on its Saturday with no time.
- Trend chart: the first date label is left-anchored and the last right-anchored, so no label is clipped (checked in e2e with getBBox).
- Contrast: light muted text #5b6472 (≥ 5.2:1 on every light background), green/amber direction words darkened in light mode; all dark-mode muted text ≥ 6:1.
- Screenshots are taken at 390×844; the audit still renders every page at 360 and 390 px, light and dark.

## F1: demo student 콩 and the taste build (2026-11-03)
- 콩 (full name 강콩, S913, slug 콩) joins 고1A in the demo: submissions every day from 22 days ago to yesterday except one no-class day (streak 21), today left open for the visitor, 3 points on the last five sessions (five ♥), 2 on the others, three notes in the last three weeks, rising screener rows (appended to the committed rounds; the other students' rows are unchanged).
- The demo now has 13 students (12 + 콩); the e2e expects 13.
- `daybook publish --taste` renders only the student with slug 콩 from the demo database (refuses one without the `demo_seed` event), at `/s/kong/` and `/p/kong/`, a landing page instead of `/go/`, the banner on every page, no Worker URL (CSP `connect-src 'self'`), and logs `publish_taste` (not `publish`, so it never feeds the parent room's publish times).
- Taste lint on top of the normal one: no Worker URL or Worker endpoint string in HTML/JSON, no other demo student's display name.
- In the taste build the upload is simulated in the browser (500 ms, no shrink, no network); 출석 and reactions will be simulated the same way when F2–F4 land.
- `make deploy-taste` / `scripts\deploy-taste.ps1` reseed `demo.sqlite` with `demo --force --no-publish` (so the real `dist/` is untouched) and deploy `dist-taste/` to the separate Pages project `daybook-taste`; the teacher runs them.
- The taste banner is amber (#f59e0b), not a messenger brand colour.

## F2: 출석 (2026-11-03)
- Schema v2 (additive, migrated automatically on the next command): `checkins (id, student_id, session_id, ts, source phone|teacher, UNIQUE(student_id, session_id))`; `sessions.start_time` (HH:MM) and `sessions.code` (4 random digits, `secrets`). `PRAGMA user_version` = 2.
- `daybook session start <class> [--at HH:MM]` (default now) keeps an existing code on re-runs (`--new-code` draws again) and PUTs `/session` with the class key, date, code, window (30 min before → 60 min after) and the token → class-key map of all active students. The Worker never sees a class name: the class key is fnv1a("class:<id>").
- The board starts the session the same way (code kept), shows 오늘의 코드 in huge digits, the arrivals (initial + time) and a "출석 처리" button per student; `daybook checkin <slug>` is the CLI twin. Both write source `teacher`.
- Worker: `PUT /session` (teacher), `POST /checkin` (Pages origin; t + code as form data), `GET /checkins?since=` (teacher), `/status` adds `checkin_ts`, `class_today`, `start`. The tokmap is one KV key. A refused check-in (wrong code, closed window, no class today, 5 wrong codes per token per day) answers 200 with `ok: false`, so the student's browser console stays clean; malformed input still gets 4xx.
- After a successful check-in, any further check-in that day is idempotent (keeps the first time), even with a wrong code.
- `daybook pull` also reads `/checkins` (same `since`) and creates the session row if needed.
- Student 오늘 tab: on a class day (the published schedule or sessions, or `/status class_today`) the ritual is ① 출석 (4-digit pad, submits on the 4th digit) → ② 과제 끝 · 찍고 가자 (the photo). On other days the tab is "오늘 공부 인증" (the photo only). Step ② 오늘의 장면 arrives with F4; until then the steps are numbered 1–2.
- Parent weekly report: one line per class day so far, "화 출석 18:02 · 끝 20:55" (– when missing); "지각" only with `"show_late": true` in daybook.json and a check-in after the start time. No points depend on time.
- The deployed Worker must be redeployed (`cd worker && npx wrangler deploy`) for 출석; an older Worker keeps uploads working and `pull` skips check-ins.

## F3: 과제 끝 with 오늘 한 줄 (2026-11-03)
- Schema v3 (additive): `submissions.reflection` (0–40 characters) and `submissions.chip` (`ok` 이해됐어요 · `confused` 아직 헷갈려요 · `rewatch` 다시 볼 영상 있어요).
- The preview step has an optional text field (maxlength 40) and three toggle chips (one at a time). The page sends `line`/`chip` only when filled; the Worker keeps the earlier ones on a re-upload without them, trims control characters and caps 40 characters; unknown chips become empty.
- Who sees it: the teacher on the board row and with `daybook lines <class> [--date] [--days N]`; the student on 내 기록 ("내 한 줄", newest first, at most 30); parents only with `"parent_shows_reflection": true` in daybook.json (a quoted line under that day's 출석/끝 row).
- Student-written text never blocks the teacher's publish: `lint.text_scrubber` (console code, may read the private columns) drops a line that holds a phone number, any full name / ext_id / parent phone, a raw id or a brand word; the chip still shows. The original stays in the local database for the teacher.
- The done screen is the day summary: "출석 18:02 → 끝 20:55" on a class day ("끝 20:55" otherwise), 오늘의 한 장, 연속, 반 누적. "장면" joins it with F4.
- Demo: sessions start at 18:00 with a code; students who usually come get check-ins at 17:40–18:09 (some by 출석 처리); 콩 has a check-in for every past class day and six reviews.

## F4: 오늘의 장면 (2026-11-03)
- Schema v4: `moments (id, class_id, session_id, ts, text, image_key)`, at most 3 per session; images live in `moments_dir` (default `moments/`, gitignored), never in git.
- On the laptop, before anything is stored or sent: `ImageOps.exif_transpose` then a fresh RGB copy (all EXIF/GPS/ICC dropped), long edge ≤ 1080 px, JPEG quality stepped down to ≤ 200 KB. The gate "학생 얼굴·이름·문제집 원문 전체가 보이지 않나요? (y/N)" defaults to N; only y stores (CLI prompt; a checkbox on the board that the server also requires).
- Same day: the console PUTs the JPEG (base64) to `/moment-put`, stored as `moment:<class key>:<date>:<n>` with a 14-day TTL. A student token reads only its own class (`GET /moments?t=` → metadata + counts + its own reaction; `GET /moment?t=&k=` → the JPEG, shown through a blob URL so the CSP stays `img-src 'self' blob: data:`). The page asks only after `load`.
- Saturday publish bakes the moments as 720 px ≤ 100 KB copies into `dist/m/<class key>/<date>_<n>.jpg` (the class key, never the name). The student story shows the moments of the last 7 days; the parent report shows up to 3 per week ("이번 주 수업 장면"), without reaction counts.
- Reactions 🔥 👏 🤯: anonymous class counters (`POST /react`), one per token per moment, the first tap counts; students see the totals; the board shows the latest totals ("지난 장면 반응"). No names, no comments, no ranking.
- The ritual is ① 출석 → ② 장면 → ③ 과제 끝 on class days. Step ② is done when the student opened today's moment (✓) or reacted (the emoji); without a moment today it shows "–" and never blocks. The day summary reads "출석 18:02 → 장면 🔥 → 끝 20:55". In the taste build step ② uses the newest baked moment and reactions stay in the browser.
- The board uploads a file (on a laptop that is a file or the camera app's photo); a live webcam capture is deferred.
- `/moments` for a token whose class is unknown answers an empty list (not 404), so the student console stays clean.
- Demo: two synthetic chalkboard scenes on each class's latest class day and one on the class day before (with synthetic EXIF, so the stripping runs in every demo).

## F5: the proposal's leftovers (2026-11-03)
- `daybook class cancel <class> <date> [--reason 휴강] [--undo]`: schema v5 adds `sessions.cancelled` (the reason). A cancelled day is removed from the class days (streak, ritual, parent "수업일" counts) and from `remaining_sessions` for a goal that is not frozen yet (a frozen goal stays as it is). The profile shows it as a grey hatched tile with the reason.
- QR: `daybook qr <class> --set <id> [--out f.svg|f.png]` and `daybook qr sheet <class>` (A4 HTML, one card per set id of the class, labels from assignments.json, else "M/D 과제") with `segno` (new pure-Python dependency). The URL is always `{pages_url}/go/?set=<id>`; the code refuses anything that is not a plain set id. Output in `qr/` (gitignored).
- Class band on the parent trend: the class's middle 50 % (p25–p75) per round, for 확보 효율 and 도전 도달, as a faint polygon; only when every shown round has ≥ 5 active students in the class, so no single student's value can be read back. Never another student's point.
- `daybook export report <slug>`: one A4 page (print → PDF in the browser): current and longest streak in the term, submission rate on class days, attitude total, the trend (with the band), every teacher note, the number of class moments. Written to `reports/` (gitignored); display name only.
- `daybook note suggest <class> [--date]`: prints a ready `daybook note …` command per student without a note this week, e.g. "이번 주 수업일 2일 모두 제출, 태도 5/6, 확보 효율 상승"; the teacher edits and runs it. Nothing is written by itself.

## Hotfix H1–H6 after the local review of `0419974` (2026-11-03)
- H1 (choice (a)): the Worker's records moved from KV to **D1** (Cloudflare's SQLite): submissions, check-ins, sessions, the token map, moments, reactions and rate counters. KV keeps only the photos and moment JPEGs (with TTLs). No KV `list` is used anywhere; the board's poll is three indexed `SELECT … WHERE updated/ts > ?` queries.
- The Worker creates its tables itself (`CREATE TABLE IF NOT EXISTS`, once per database binding); a daily cron (04:17 KST) deletes records older than `RECORD_TTL_DAYS` and rate counters older than 2 days.
- The board polls every **60 s** (`board_poll_seconds`) and only from 30 min before the session start to 2 h after it; outside that window it does not call the Worker at all.
- Class-day budget (`worker/test/budget.test.mjs`, the real Worker code against a SQLite-backed D1 stand-in): 20 students, a 3 h board at 60 s, 3 moments × 20 reactions, 5 page opens per student, 20 check-ins, 21 uploads, one pull. Free plan per day; the test fails at 30 %.

  | resource | used | free limit | share |
  |---|---|---|---|
  | Worker requests | 910 | 100,000 | 0.9 % |
  | KV reads | 60 | 100,000 | 0.06 % |
  | KV writes | 24 | 1,000 | 2.4 % |
  | KV lists | 0 | 1,000 | 0 % |
  | D1 rows read (estimate: rows returned + 1 per query) | 10,103 | 5,000,000 | 0.2 % |
  | D1 rows written | 188 | 100,000 | 0.19 % |
- H2: a reaction is one row `(moment, token)` inserted with `INSERT OR IGNORE`; totals are `COUNT(*) … GROUP BY r`. No shared counter key, so 20 simultaneous taps all count (tested). Rate limits are atomic `INSERT … ON CONFLICT DO UPDATE SET n = n + 1 RETURNING n`. Any storage error answers `{ok: false, error: "storage"}` with 200 on student endpoints (503 on teacher endpoints), never a 500.
- H3: `/submit` accepts only tokens in the token map (`not_ready` while there is none, `unknown_token` otherwise; both 200 + ok:false, nothing written, no rate-limit row). The console sends the map (`PUT /tokmap`, replaces it) after every successful publish, after `roster add|import|rotate-tokens` and after `roster edit --active/--inactive/--class`, and with every `session start`. A send failure is a warning, never a failed publish.
- H4: the e2e copies `demo.sqlite`, the demo config and the moment store into a temp folder and works only there; its taste build goes to the temp folder; its texts read like class content. The taste lint rejects the words e2e, test(s), fixture(s), dummy in visible text (not in scripts or attributes).
- H5: all four `.ps1` scripts use `$ErrorActionPreference = 'Continue'` and test `$LASTEXITCODE` after each native command; the Playwright probe runs through `cmd /c`. Static pytest checks: ASCII only (5.1 reads BOM-less scripts as ANSI), no 'Stop', no native `2>$null`. They were not executed here (no PowerShell in the cloud build): the local agent confirms on 5.1.
- H6: the profile bio's "{n}번째 기록" is recomputed in the browser from the same value as the 제출 counter. Demo times are realistic: 출석 17:40–18:09, 과제 끝 19:50–21:00 (earlier the same day when the seed runs before then).
- Node ≥ 22 is now needed for the local Worker tests and the dev server (`node:sqlite`); they run with `--experimental-sqlite --no-warnings` so Node 22.5–22.12 works too. The deployed Worker is unaffected.

## F0 (2026-11-03)
- Checks never write tracked files: the e2e takes screenshots only with `--screenshots` (`make screenshots`, `scripts\check.ps1 -Screenshots`), which reseeds the demo first. A normal `make demo` / `demo.ps1` leaves `git status` clean, so the teacher's `git pull` always merges.

## F6: Teacher tab (2026-11-03)
- The local board app gains `/teacher` (link "선생님" on the board): roster (display name, 재원 toggle, copy student/parent link), this week's one-line notes prefilled with the note or the F5.5 draft, screener-round / tests-CSV import (file picker → the same importers), moments (with the faces/names gate) and 휴강, **발행** (pull when a Worker is configured → publish → lint → send the student links) and **배포** (runs the teacher's own `npx wrangler pages deploy ../dist --project-name <pages_project, default daybook> --branch main` and shows the output), and the weekly paste messages with copy buttons.
- Everything calls the CLI's functions (`ops.edit_student` is now shared with `roster edit`; `suggest_notes(include_existing=True)` prefills every row). One pytest per action through the HTTP layer.
- The board server now answers only when the Host header is 127.0.0.1/localhost on its port (blocks DNS-rebinding reads of the console) and still refuses cross-origin writes. `teacher.js` is never copied into `dist/`.
- The board is per class (`daybook board 고1A`); the Teacher tab works on that class, and 발행 publishes everything.
- A projector poll that lands just after 방 닫기 gets `{open: false}` (200), not an error.

## F7: AMP Live prototype (2026-11-03)
- Free-plan finding: SQLite-backed Durable Objects are on the Workers Free plan (the key-value backend is Paid only): 100,000 requests/day, 5 M rows read, 100 k rows written, 13,000 GB-s/day, 5 GB. Source: Cloudflare's DO pricing/limits pages as quoted by a web search (developers.cloudflare.com itself is not reachable from the build's network): **re-check the official page before the first Live class.**
- So: one Durable Object `LiveRoom` per room (SQLite storage), behind the existing Worker. Transport: **HTTP polling every 2 s, only while a room is open** (phones while the Live screen is open, the projector while its room is open). WebSockets are deferred: the polling path runs in the Node tests and the local dev server without workerd, and the class-day budget stays far below 30 %.
- What leaves the laptop: per question only `kind` (choice ①–⑤ / num) and the answer key; the question text and the choices stay in the local JSON file (`live/*.json`, gitignored; `demo/live/demo-round.json` is the synthetic example) and appear only on the projector. Phones show only answer buttons or a number pad.
- Room code: 4 random digits, unique among open rooms (D1 `live_rooms`). A phone joins with its own token + the code (no nickname); only students of the room's class can join. One answer per question (the first one stays); answers close at the reveal.
- Cooperative, not a race: the projector shows "답한 사람 a/s", after the reveal "반 정답 c/s"; each phone sees only its own ✓. No names, no speed, no ranking anywhere.
- Class bonus: when ≥ 80 % of the class (all active students of the class, not only those who joined) is correct on a question, the board adds `live_bonus` points (default 5, daybook.json) once per question to the new `class_bonus` table (schema v6), which counts in the class total and the reward bar. "장면으로" turns the revealed question into a 오늘의 장면 from a generated card (numbers only: no face, name or book text, so the faces/names gate is answered by construction).
- Retention: the room closes on "방 닫기" or 90 minutes after opening (DO alarm); it then writes only per-student answered/correct counts to D1 `live_results` and wipes its storage. `daybook pull` imports them into `effort` (`source = 'live'`, `set_id = live:<date>:<room>`).
- Budget (`budget.test.mjs`, class day + one Live round of 20 students × 10 questions, everyone polling every 2 s for ~20 min): Worker requests 13.8 %, DO requests 12.8 %, DO rows read 1.8 %, every other limit below 3 %.
- The taste build has a self-playing 8-student practice room (5 questions, nothing sent).
- deferred: a WebSocket transport (hibernation API) for Live; polling is enough for one class.

## F8: answer-card reading spike (2026-11-03)
- `console/omr.py`, pure Pillow (no numpy/OpenCV): the card is 20 items × ①–⑤ with four solid corner squares. Find the squares with a **local** (adaptive) threshold and connected components (largest solid blobs nearest each image corner), solve the 4-point perspective, warp to the canonical sheet, compare each bubble's interior with the paper right next to it (filled when below 62 %), and report per item blank / one choice / several.
- Synthetic test cards: perspective (corners jittered ±3.6 %), blur σ 0.6–1.8, a shading gradient from a random side, a hard-edged shadow on 25 % of cards, a light pencil on 20 %, 8 % blank and 4 % double-marked items, JPEG q 55–85, a darker table around the paper.
- Accuracy on 200 cards (4,000 items, seed 20261104), as the reader evolved:

  | version | not located | item accuracy | cards fully right |
  |---|---|---|---|
  | global (Otsu) threshold | 44 / 200 | 78.0 % | 77.5 % |
  | local threshold | 13 / 200 | 93.5 % | 92.5 % |
  | local threshold + fill rule 0.6 (current) | 2 / 200 | **98.9 %** | **97.5 %** |

  On located cards: blank 100 %, single 99.97 %, double-marked 98.9 %. The misses left are a corner square cut by a hard shadow edge.
- `daybook omr --spike` (`--eval N`, `--sample out.jpg`, `--image card.jpg --student <slug> --set <id> [--key 3,1,4,…]`) writes one `effort` row per item (`source = 'card'`, attempted = any bubble filled, correct only with a key). Without `--spike` it refuses; nothing reaches the pages or publish.
- Next steps if it graduates: real phone photos of a printed card (the synthetic set is only a proxy), a printable card template from `blank_sheet()`, and a "retake" answer when the squares are not found.

## F9: offline and install (2026-11-03)
- A service worker (`dist/sw.js`, served from the root, scope `/s/` only) caches the student page shell: pages network-first with the last copy as the offline fallback (so a new publish shows as soon as there is a connection), `/static/` cache-first. It never touches the Worker (another origin) or the parent pages.
- A photo submitted without a connection is shrunk as usual and kept in IndexedDB (`daybook` → `queue`, one record per student per day, the newest photo wins); the 오늘 tab shows "보낼 준비 완료". It is sent on the next page open with a connection or on the browser's `online` event. A sender first **claims** the record with a 60 s lease in one IndexedDB read-write transaction (serialised across tabs and page loads) and deletes it only after a confirmed upload, so two tabs never send it twice and a page that dies mid-upload loses nothing (the lease expires, the next open retries; at worst a harmless same-day replacement). The e2e checks it arrives exactly once, also under the slower screenshot run that first exposed a double send.
- The Worker dates a queued photo by when it arrives (it never trusts a date from the phone), so a photo queued at 23:50 and sent after midnight counts for the next day.
- "홈 화면에 추가" card (iPhone Safari steps, Android Chrome steps) on the 오늘 tab, shown once per phone and never inside the installed app.

## F13: the parent-contact desk (2026-11-03, from F13_PROMPT_cloudCC_20261104)
- **Order.** The prompt says "run after F10–F12", but no F10–F12 prompt ever reached this session. F13 was built on top of F9 (`a5a89b3`); F10–F12 are not started and nothing here assumes them.
- **Principle: the system notices, drafts and remembers; the teacher calls.** Nothing is sent: every draft is copied by hand, and every button only writes the laptop's database.
- **Laptop only.** Schema v7 adds `parent_profiles` (channel call|kakao|both, style weekly|needed, greeting 어머님|아버님|보호자님), `contacts` (call|message|meeting, out|in, summary ≤ 120, follow-up date) and `snoozes`. Every write also goes to `events` (`contact`, `contact_snooze`, `parent_profile`, `class_policy`, `solved_fix`). The generator never reads these tables; the e2e and `tests/test_contact.py` check that no reason, draft or summary reaches `dist/`, `dist-taste/` or the projected board.
- **연락 현황:** days since the last contact, counted from the student's enrolment (`created_at`) when there is none, so a fresh roster is not all red. Amber ≥ 21, red ≥ 35 days (`contact_amber_days`, `contact_red_days` in daybook.json).
- **Rules (per class, `daybook class edit --late-min/--late-count/--late-avg-min` or the Teacher tab):** computed over the last 6 class days (schedule + sessions, minus 휴강).
  - 지각: lateness = 출석 time − the session's start (else the class start). Late on a day = more than `late_min`; fires on ≥ `late_count` such days **or** an average lateness ≥ `late_avg_min` (early arrivals count as 0). The average-only wording says "평균 N분" without a misleading "0회 지각".
  - 결석: counted backwards from the last *past* class day (today is still open); a day with neither 출석 nor a submission. 1 = 참고 (notice), 2 in a row = 연락 필요.
  - 과제: ≥ 3 past class days in a row without a submission (a paper/teacher submission counts).
  - 테스트: the latest round's 확보 효율 ≥ 15 points below the mean of the 3 rounds before it (needs 4 rounds; the demo has 3, so the demo shows no test flag).
  - 오래 연락 없음: the red state.
  - **Clearing:** a logged contact on or after the flag's latest evidence date clears it until new evidence appears; 오래 연락 없음 clears because the days reset. "이번 주는 보류" writes a 7-day snooze (rule `all`), logged.
  - The basic demo class uses `late_min 30` and `late_avg_min 30` (a basic class whose students drift in focuses on 결석).
- **Script and draft:** line 1 is the most convincing positive fact (a test ≥ 80 % or a rise, the streak, ♥ days, a recent 장면), line 2 the main rule's fact with dates and times, line 3 one concrete proposal. The KakaoTalk draft is greeting + the same three lines + closing. Order of flags: 연락 필요 first, then the number of rules, then days without contact.
- **The board:** the projected page never contains flags. "선생님 보기" (a local-only `static/flags.js`, excluded from `dist/` like `teacher.js`) fetches this class's flags on demand and removes them from the DOM when switched off.
- **Planner (이번 주 할 일):** 연락 필요 items (call, or message for kakao-only parents) are locked; then the rotation: every parent whose last contact is ≥ 21 days ago (one week before the 4-week limit), oldest first, red ones locked; then the weekly lines for 매주 parents, prefilled by `note suggest`. Minutes: call 8, message 2, line 1 (`contact_minutes`), budget `weekly_contact_minutes` 100. Unlocked items over budget move to next week; locked ones never move, even past the budget. A checked call/message logs a contact; a checked line saves the prefilled note. Coverage: parents contacted since Monday and the share contacted within 4 weeks. Tested: 30 students, staggered history, 8 simulated weeks: every gap between contacts ≤ 28 days.
- **Monthly good news:** positive facts only for every parent (hands-off ones too); shown all month, marked as the time to send in the last 7 days of the month. "메시지 보냄" logs it as a message.
- **Tone:** all parent wording is in `strings.ko.json` → `parent_msg` (합니다체, "안녕하세요, {g}. AMP 수학입니다.", no emoji, no `~ ㅋ ㅎ ^^ !`). A pytest lints every `parent_msg` string and every generated draft.
- **오늘 푼 문제 수 (F13.5):** optional on 과제 끝: a text field with the phone's numeric keypad (`inputmode="numeric"`) plus −/+ keys for one hand, clamped to 0–60. The Worker stores it as `subs.solved` (a whole number 0–60, anything else ignored; a replacement without a count keeps the earlier one) and adds the column to an older D1 database by itself. `pull` stores it; a teacher correction (Teacher tab → 푼 문제 수, an event `solved_fix`) is never overwritten by a later pull. The student sees their own weekly sum on the profile; the parent card shows "이번 주 N문제 · 스스로 기록한 수" only in weeks with a count. Never ranked, averaged or compared.
- **Self-study block:** `classes.self_study_until`; the parent card header shows "수업 17:30–20:00 · 자습 ~21:00". The Worker accepts 과제 끝 at any time of day, so the only window to widen was the board's polling window (H1): it now runs until the end of the self-study block (or the class end) when that is later than start + 2 h.
- **`export report`** gains 연락 기록 (date, kind, in/out, summary, follow-up) for the term.
- **Demo:** four classes, 22 students: 고1A (strong, 화목토 18–20, 5 incl. 콩), 기초B (basic, 월수금 20–22, 6, late_min 30), 심화C (strong, 월수금 18–20, 6), 케어D (care, 화목토 17:30–20:00, 자습 ~21:00, 5). 시온 (심화C, S916) arrives 18–45 min late (about 30 on average), tests about 90 %, last parent contact 180 days ago: always on top, script opens "확보 효율 …%로, 수학 실력이 …". Also seeded: one 2-in-a-row absence (기초B), one single absence (케어D, notice), one 3-day homework gap (심화C), one red parent (케어D, 38 days). Everyone enrolled 150 days ago with a contact history.

## Fixes before F10–F12 (2026-11-04)
- `scripts\check.ps1` runs every Worker test file (`node --test 'test/*.test.mjs'`, the glob is expanded by Node, so PowerShell needs no globbing); it had missed `test/live.test.mjs`.
- The board reads the whole request body before any reply, refusals included (wrong Host, wrong Origin, too big; a body over 25 MB is drained in chunks up to 64 MB, then the connection closes). Answering first and closing with unread bytes made Windows reset the connection (WinError 10054 in `test_board_submit_for_and_guards`). A Content-Length that is not a number gets a 400 and a closed connection (nothing can be drained safely). `test_every_refusal_reads_the_body_first` sends 4 MB bodies on each refusal path; against the old board it fails the Origin and Host cases intermittently and the too-big case always; the board tests passed 20 runs in a row here (Linux). On Windows the 26 MB too-big case then timed out 3/20 under load: the test now lowers the board's limit to 1 MB and sends 2 MB, with a 60 s socket timeout.

## F14: the weekly-test report inside DAYBOOK (2026-11-04, from F14_F10_F12_COMBINED_cloudCC_20261104)
- **Order:** F14 → F10 → F11 → F12 as the combined prompt asks (F14 first, for Friday 11-06).
- **Rounds** (`daybook round add|edit|close|list|status`, Teacher tab → 주간 테스트): the teacher's own round id (e.g. `Adaptive_Screeners_1S_Nov06_G10`), grade (1학년/2학년/3학년, the Notion "Grade"), classes, N (1–60), an open and a close time in KST, and a student-facing label (default "11/6 주간 테스트"; a label that looks like a raw id is refused). Schema v8: `rounds`, `test_reports`.
- **What goes online:** only `rid` = the first 16 hex characters of SHA-256(`round:<id>`), the class keys, N, the window and the label. The round id never reaches the Worker, a page, `dist/` or the taste build (tested). Rounds travel with the token map on every publish (only when there are rounds, so a publish without rounds sends exactly what it did before) and on every `round add/edit/close`. Rounds closed more than 14 days ago are no longer sent.
- **Worker:** D1 `rounds` (replaced as a whole by `PUT /rounds`) and `reports` keyed (rid, token). `POST /report` (form fields, like `/submit`, so a browser sends it without a CORS preflight): the token must be published and in a class of the round, the round open (server time), quiz numbers whole 1…N without repeats, ✕ and – never on the same quiz, at most 3 hardest, reasons from 개념탈락 · 계산실수 · 정보이해부족 · 시간부족 (at least one when anything is ✕ or –), wishlist ≤ 100 and metaphor ≤ 30 characters, control characters stripped; 30 reports per token per hour. Refusals are 200 + `ok:false` (as for 출석). An edit replaces the report and keeps the first time (`n` counts the sends). `/status` lists the open rounds of the student's class with the student's own last report (to edit it), never anyone else's. Reports are deleted 14 days after their last edit by the daily cleanup.
- **Student page:** a 주간 테스트 card on 오늘 while a round is open; one screen: a 5-per-row grid (tap ○ → ✕ → –), up to 3 hardest (a 10-per-row number strip), reason chips, 바라는 점, 이번 시험을 한 단어로, 제출. Afterwards only "제출 완료 · 수정 가능 (일 23:59까지)"; reopening shows the student's own answers. The open rounds are cached on the phone, so an offline page still shows the card; an offline report waits in the F9 queue (same lease rules) and is sent once. Taste: a practice round with 20 quizzes, kept in memory.
- **Pull:** `GET /reports?since=` → `test_reports` (the latest edit wins; unknown tokens or rounds are skipped). Teacher tab: per round "6/6 제출" and a ✓ per student (no answers shown; the board never shows any of it).
- **Export** (`daybook export reports --round <id> [--format csv|notion-json]`, Teacher tab → 내보내기): `reports/<round>.csv|json` (gitignored), columns exactly `Student ID, Name, Grade, Round ID, Screening Date, Incorrect Quiz #, Missed Quiz #, Most Challenging Quiz, Reasoning, Wishlist, metaphor`. One row per student **who reported** (the command lists who has not). Screening Date = the round's opening date. The CSV has a UTF-8 BOM (Excel on Windows) and comma-joined lists; the JSON keeps lists. Example: `mappings/reports.example.json`. Nothing here talks to Notion.
- **Budget:** the class-day test now includes an open round with 20 students × (report + one edit) and the open-round lookup in every `/status`; the totals stay at or below 13.8 % of every free limit.
- **next (not built):** the Google Form → make.com → Notion intake can retire once the teacher has tried this (the teacher decides after the trial).

## F10: videos per class (2026-11-04)
- Each item in `videos.json` may carry `classes` (e.g. `["고1A"]`) and/or `grades` (e.g. `["고1"]`); a class has a `grade` (`daybook class add/edit --grade 고1`; schema v8 adds `classes.grade`). An item with neither shows to everyone; otherwise only to students of a matching class or grade (story ring, shorts and 강의 alike). A string instead of a list is accepted.
- `pin: true` puts an item on top of the 강의 list for the students who see it (the rest stays newest first).
- `videos_path` may be a folder: every `*.json` in it is merged in name order; the same URL twice is kept once (the first file wins), so per-source files (solutions.json, shorts.json, concepts.json) can overlap safely. A file that is not valid JSON is skipped.
- An unknown class or grade in the tags is a publish **warning** (`video_tag`, listing the item); the item then shows to nobody until it is fixed.
- Thumbnails are still made for every item (a thumbnail is not personal data).
- Demo: 고1A, 심화C and 케어D are 고1, 기초B is 기초; the November solution set is pinned for 고1A and 심화C; two 기초 items show only to 기초B.
- `daybook session start` now defaults to the class start time (F11 "start time"), then to now.

## F11: the teacher manages the class members alone (2026-11-04)
- The Teacher tab's roster now lists **every class** with a search box (display name, full name, student ID) and a class filter. **Full names (and IDs) are shown here** (the tab is local: 127.0.0.1, Host- and Origin-checked); F6 kept them out of the tab, F11 asks for them. Pages still get display names only (`students_public`; tested after adding a student with a full name and a phone).
- **학생 추가:** full name, display name (prefilled with the given name of a 3-syllable name, as `roster add` does), class, track, student ID (must be unused), parent phone (digits, `+`, `-`, spaces). A display name equal to the full name is refused (the no-PII lint would stop the publish). The new student and parent links and both 안내 문구 come back with copy buttons.
- **반 이동, 퇴원 처리 (with a date, `students.left_on`, schema v8) and its undo, 링크 재발급 (behind a confirm):** all through the same ops as the CLI. Unlike the prompt's "on the next 발행", the token map is **sent right away** when a Worker is configured (best effort, never blocking; the next publish sends it again anyway), so a new or moved student can upload at once. The old token keeps mapping to the student for pulls (as `rotate-tokens` does).
- **반 만들기 / 반 수정:** name (rename), grade (F10), weekdays, term, reward label, goal (0 = computed), start time (the default for `session start`).
- **안내 문구:** the parent text is `parent_msg.link_intro` (formal; the greeting from the parent profile; checked by the tone test), the student text `link_intro_student`. Copy buttons only; nothing is sent.
- **Roster CSV:** the existing import with `mappings/roster.json`, now with **가져오기 미리보기** first: the same matching inside a SQLite savepoint that is rolled back (nothing written, nothing logged), a table of new / updated (with the changed fields) / unchanged / skipped rows, then "이대로 가져오기".

## F12: the weekly-test bridge (2026-11-04)
- `daybook import insights <file.json>` (and the Teacher tab's import picker) reads the teacher's own per-round analysis in the format of `mappings/insights.example.json`: round, date, and per student `ext_id`, `focus`, `videos`, `student_line`, `parent_line`. Matched by ext_id only; an unknown ext_id is reported and skipped. At most 3 focus items (30 characters each) and 3 http(s) videos are kept; lines are capped (120 / 160). Stored in `insights` (keyed student + round; a re-import replaces). DAYBOOK does not know how the analysis is made.
- **Student page:** a story ring "이번 주 복습" for an insight dated in this week (Mon–Sun): the student line, the focus chips and the recommended videos (a `videos.json` entry by URL, with its title, else a plain "추천 영상 보기" link; a URL that looks like a raw id is dropped). No insight this week: no ring.
- **Parent page:** the 주간 테스트 row of the week the insight belongs to shows `parent_line` under the trend (older weeks keep theirs). Focus chips for parents only with `"parent_shows_focus": true` in daybook.json.
- Every text passes `text_scrubber` at publish: a line or a focus item with a phone number, a full name, a student ID, a raw id or a brand name is left out (not rewritten). The round id is stored locally and never rendered.
- Demo: `daybook demo` writes `demo/insights.demo.json` (gitignored) dated today for 고1A and 기초B and imports it.
- **next (from the combined prompt, not built):** retiring the Google Form → make.com → Notion intake in favour of F14's report; the teacher decides after the trial.

## Windows fixes after ffd6d96 (2026-11-04, from the local agent's run on the teacher's laptop)
- `export reports` writes with `newline=""`: the CSV (UTF-8 BOM, `\n` line ends) and the JSON have identical bytes on every OS (Windows text mode had turned them into CRLF, so the exact-header check failed and the local Notion sync would see different bytes). A pytest reads the file in binary and asserts there is no `\r`; the e2e splits on `\r?\n`.
- The board uses a **lingering close** on every path that closes the connection itself (a non-numeric Content-Length; a body over the drain cap): reply, half-close (`SHUT_WR`), read and discard until EOF, 2 s or 64 KB, then close, as Apache and nginx do. The bad-length test now sends a 32 KB body and stays strict. Linux does not reproduce the Windows reset (the old code passes there too), so the local 20x loop on Windows is the proof.

## G1: `daybook doctor` (2026-11-04, Friday readiness)
- One preflight (CLI and the Teacher tab's 점검): daybook.json and worker_url / pages_url / teacher_token; schema version and `PRAGMA integrity_check`; the Worker's `/health` (now with `version` and a cheap D1 read), the version against what this console needs (v6; ⚠ "redeploy: cd worker && npx wrangler deploy"); the pushed token map against the active roster through the teacher-only `/tokmap-info` (a count and a SHA-256 hash of the sorted `token:class` lines, never the tokens); for each class meeting today: start time, session started, open 주간 테스트 round; last publish, last deploy (recorded by the Teacher tab's 배포 button; "unknown" otherwise), last backup (⚠ none or over 7 days) and whether the backup folder is on a removable drive (Windows only; unknown elsewhere); dist/ through the no-PII lint.
- ✓ / ⚠ / ✗ with a one-line fix each; exit 0 when there is no ✗ (a ⚠ such as "session not started" before class is normal). `--json` for the local agent. It only reads.

## G2: `daybook backup` (2026-11-04)
- SQLite's online backup API (consistent while the board writes), into `backup_dir` from daybook.json; the default `backups/` and any relative path sit next to the database file (not the current folder), so a test or a script never litters the repo. The copy is written as `.partial`, checked with `PRAGMA integrity_check`, then renamed to `daybook-YYYYMMDD-HHMM.sqlite` (`-2`, `-3` … within the same minute); the newest N are kept (`--keep 30`, or `"backup_keep"` in daybook.json).
- Every 발행 ends with a backup (CLI `publish` and the Teacher tab; a failed backup is a warning, never a failed publish), plus the 백업 button.
- No encryption library: point `backup_dir` at a BitLocker-encrypted USB drive or folder; doctor shows whether it is removable.

## G3: the Korean operations guide (2026-11-04)
- `docs/운영가이드.md` (plain Korean, short sentences): 수업 전, 수업 중, 수업 끝, 토요일, 학생 추가 · 퇴원 · 링크 재발급, and 문제가 생기면 with the ten likeliest failures. Buttons are named exactly as the screens show them (a test compares them with strings.ko.json).
- `daybook guide [--out cards/운영가이드.html]`: printable A4 HTML, one routine per page, from a small built-in Markdown subset (no new dependency). No link or token appears on paper.

## G4: printable 안내 cards (2026-11-04)
- **Standing rule: no printed paper ever carries a token, as text or as a QR.** Links travel only by the teacher's 1:1 KakaoTalk (the F11 안내 문구), so a lost card is never a lost link. A test renders every demo class's cards and letters and asserts there is no token, no link of any kind (`http`, `/s/`, `/p/`), no QR or image, and no full name or student ID.
- `daybook cards students <class>`: an A4 sheet of cut-out cards, one per active student: the display name, "내 링크는 선생님 카톡으로 받아요", the 홈 화면 추가 steps (iPhone / Android), the 오늘 ritual as three drawn pictures (① 출석 코드 → ② 오늘의 장면 → ③ 과제 끝 찍기), and "링크는 나만 쓰기 · 잃어버리면 선생님께 (새 링크 발급)". Six cards per page.
- `daybook cards parents <class>`: one warm-formal A4 letter per student (display name only): what the page shows, what it never shows (no ranks or comparisons, no other students, no face or homework photos), the Saturday rhythm, that the link comes by KakaoTalk from the teacher, and the placeholders `{원장·학원 문구}` and `{개인정보 안내 문구}` for the teacher to fill. The wording lives in `strings.ko.json` → `parent_msg.letter_*`, so the tone test covers it (it now also checks list items and headings; the playful-ending rule no longer flags words like 전용).
- Output goes to `cards/` (gitignored); print from the browser (A4).

## Deferred
- deferred: live webcam capture for 장면 올리기 on the board (a file input is used).
- deferred: `export pdf` stays a stub (exit 2); `daybook export report <slug>` writes print-ready HTML instead (print → PDF in the browser, no PDF library).
- deferred: several reward cycles per term (the class total counts the whole term).
- deferred: a real-device install test on iPhone Safari and Android Chrome (manifest, icons, apple meta tags and standalone mode are in place; only Chromium was tested here).
- deferred: the Cloudflare deploy itself (needs the teacher's account: README "Teacher deploy steps").
- deferred: the `effort` table is created empty (P2 data source not defined yet).
- out of scope: login, database server, chat, push notifications, app store, analytics; nothing talks to Notion, Google or make.com.
- not in P0: retiring Google Forms / make.com (the teacher decides later).
