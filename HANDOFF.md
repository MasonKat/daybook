# HANDOFF — for the local agents (real data)

**Windows: every `daybook …` command in this file is `.venv\Scripts\python.exe -m console …`** (antivirus blocks pip's
generated launcher; `scripts\*.ps1` and the e2e already run the module). Mac/Linux keep `daybook`.

## Thursday checklist (real setup before Friday 11-06), in this order

[대시보드] = the teacher's Cloudflare dashboard in a browser. [콘솔] = PowerShell in the repo folder
(`.venv\Scripts\python.exe -m console …` for DAYBOOK, never pip's .exe). [선생님 탭] = the board's Teacher tab.

1. [대시보드] Workers & Pages → pick the free `workers.dev` subdomain (once per account).
2. [콘솔] `cd worker`, `npx wrangler login`, `npx wrangler d1 create daybook`; paste the printed `database_id` into `worker\wrangler.toml`.
3. [콘솔] `npx wrangler secret put TEACHER_TOKEN` (a long random string; keep it for step 5).
4. [콘솔] `npx wrangler deploy` (Worker v12: 학생 리뷰 with its hourly vote cron, 영상 반응, the teacher remote; also creates the AMP Live Durable Object). Note the `https://….workers.dev` URL.
5. [콘솔] copy `daybook.example.json` to `daybook.json`: `worker_url` (step 4), `pages_url`, `teacher_token` (step 3),
   `backup_dir` (a folder on the BitLocker-encrypted USB drive), `academy` (the academy name for the printed letter).
6. [선생님 탭 / 콘솔] classes with hours and the 지각 rule: 반 만들기, then 반 규칙 · 수업 시간
   (`class add 고1A --term 2026-11-06..2027-02-26 --days 월,수,금 --grade 고1 --start 18:00`, `class edit 고1A --end 20:00 --late-min 10`).
7. [선생님 탭] 명단 · 반 관리 → 가져오기 미리보기 with the roster CSV → 이대로 가져오기.
8. [콘솔] print the letters: `cards parents <class>` (one A4 page per family, with a tear-off consent slip; no name, link or QR).
   [선생님 탭] as the slips come back: 연락 현황 → 동의 / 동의 안 함 (`roster edit <slug> --consent yes|no`). Until a yes there is
   no parent page, no watch signal and no push for that student; the class routine works either way.
9. [콘솔] weekly-test rounds: `round add <Friday round id> --grade 1학년 --classes <both 고1 classes> --quizzes <N> --open "2026-11-06 20:00" --close "2026-11-08 23:59"`,
   and the 고2 round for Saturday the same way.
10. [선생님 탭] 발행 (sends the links, the consent list, the rounds and the 학생 리뷰 wordlist / name hashes / vote moment
   to the Worker, then backs up). Optional before it: extra filter words in `filters\review_words.local.txt` (one per line).
11. [선생님 탭] 배포 (needs `npx wrangler login` from step 2; the Pages project is created on the first deploy).
12. [선생님 탭] 점검 (`doctor`): no ✗ ("Worker version ⚠" means the v12 deploy of step 4 did not happen). "consent ⚠" (slips not back yet) and "session not started" (before class) are fine.
13. [선생님 탭] 명단 → 학생 안내 복사 / 학부모 안내 복사 → the teacher's own 1:1 KakaoTalk (parents: only after a yes).
14. From then on: 선생님 탭 → 학생 리뷰 → 승인 / 숨김 (after `pull` or 발행). Nothing a student writes reaches classmates before 승인.
15. [선생님 탭] 명단: a 호출명 per student (가람, 7번; unique per class) before the first 음성 메모 가져오기 (CLOVA Note → 텍스트 내보내기 → paste).
16. [선생님 탭] 리모컨 → QR 보기 → open it with the teacher's iPhone camera once, then 홈 화면에 추가. The QR holds the teacher token: never print it or send it.

The cloud build used synthetic data only. Real students, phones, scores and notes exist only on the teacher's laptop,
in `daybook.sqlite` (gitignored), and on the deployed pages behind unguessable links. Never commit `daybook.sqlite`,
`daybook.json`, `dist/`, a real roster CSV or real screener snapshots. Never deploy to GitHub Pages.

## 0. State at 11-04 (after F6–F14 and G1–G5): what to do before Friday 11-06

`main` is green from a clean clone: 261 pytest, 58 Worker node tests (incl. the class-day budget with an AMP Live round
and a weekly-test round), the full Playwright check. Built: P0, design v2, F1–F14, hotfix H1–H6, G1 doctor, G2 backup,
G3 운영 가이드, G4 안내 cards, G5 taste refresh. Every choice is in `DECISIONS.md`. Console schema v8 (migrates by itself);
Worker v6. The pilot: two Mon/Wed/Fri 고1 classes from Friday 11-06; the weekly test restarts that day (고2 on Saturday).

**Before Friday, in this order (Windows, PowerShell, in the repo folder):**

1. Pull `main`, then `scripts\demo.ps1` once: everything green (it uses only demo data).
2. **D1**: `cd worker`, `npx wrangler d1 create daybook`, paste the printed `database_id` into `worker/wrangler.toml`.
3. **Worker**: `npx wrangler secret put TEACHER_TOKEN` (a long random string), then `npx wrangler deploy`
   (also creates the AMP Live Durable Object). Put the same token and the Worker URL into `daybook.json`
   (copy `daybook.example.json`; set `pages_url`, and `backup_dir` to a folder on the BitLocker-encrypted USB drive).
4. **Classes**: `daybook class add 고1A --term 2026-11-06..2027-02-26 --days 월,수,금 --grade 고1 --start 18:00 --reward "…"`
   (and the second class), then `daybook class edit <class> --end 20:00` and the 지각 rule if it differs
   (`--late-min 30` for a class that drifts in). Or the Teacher tab → 명단 · 반 관리 → 반 만들기 and 반 규칙 · 수업 시간.
5. **Roster**: Teacher tab → 명단 · 반 관리 → 가져오기 미리보기 with the roster CSV (`mappings/roster.json`), check the
   new / updated rows, then 이대로 가져오기 (or `daybook roster import roster.csv --map mappings/roster.json --class 고1A`).
   Fix any display name that equals a full name. Set each parent's profile in 연락 현황 (전화/카톡, 매주/필요할 때만, 호칭).
6. **Publish + deploy**: Teacher tab → 발행 (sends the student links and the rounds to the Worker, then backs up) →
   배포 (or `make deploy-pages`).
7. **Rounds**: `daybook round add Adaptive_Screeners_1S_Nov06_G10 --grade 1학년 --classes 고1A,<second class> --quizzes <N>
   --open "2026-11-06 20:00" --close "2026-11-08 23:59"` for Friday, and the 고2 round for Saturday the same way
   (or Teacher tab → 주간 테스트 → 새 회차). Use the teacher's real round ids; students never see them.
8. **Links**: Teacher tab → 명단 · 반 관리 → 학생 안내 복사 / 학부모 안내 복사 per student → the teacher's own 1:1
   KakaoTalk. Print `daybook cards students <class>` and `daybook cards parents <class>` (fill `{원장·학원 문구}` and
   `{개인정보 안내 문구}`); paper never carries a link. Print `daybook guide` for the teacher.
9. **doctor green**: `daybook doctor` (or 점검): no ✗. Before Friday's class, "session not started" is a normal ⚠;
   it turns ✓ when the board starts.
10. Taste build for curious students: `make deploy-taste` (amp-taste.pages.dev; nothing is stored).

**F15 (after the pilot starts, when the teacher wants them):**

- **Push (opt-in):** `.venv\Scripts\python.exe -m console push keys --subject mailto:<teacher's address>` writes the VAPID
  keys into daybook.json (never the repo). Then in `worker\`: `npx wrangler secret put VAPID_PRIVATE`,
  `npx wrangler secret put VAPID_PUBLIC`, `npx wrangler secret put VAPID_SUBJECT` (the values it prints) and
  `npx wrangler deploy` (Worker v8; doctor says when it is older). Publish once: pages show "알림 받기". Never run
  `push keys --force` casually: new keys end every subscription.
- **YouTube's own counters:** the local agent may fill `views` and `views_7d` (whole numbers) per item in `videos.json`
  (or the files of the videos folder) from the teacher's YouTube Studio exports. Only the Teacher tab shows them
  (영상 → "전체 조회 … · 7일 … · 우리 반 …명"); DAYBOOK never calls YouTube.
- **Mascot:** put the academy's image at `brand\mascot.png` (the folder is gitignored) and add
  `"brand": {"mascot": "brand/mascot.png", "mascot_public": false}` to daybook.json; set `mascot_public` to true only if
  it may appear on the public taste site.

Still not done here: the Cloudflare deploys (above), a real-phone install test, Live over WebSockets (polling is used),
a webcam capture on the board. Re-check Cloudflare's Durable Objects free-plan page once before the first Live class.

### The repo
Everything is on `https://github.com/MasonKat/daybook` (private), branch `main`. If you ever receive a git bundle instead:

```bash
git clone daybook-p0.bundle daybook
```

```bash
cd daybook && git remote set-url origin git@github.com:MasonKat/daybook.git && git push -u origin main
```

## 1. First-time setup on the laptop

```bash
make demo
```

On Windows (no `make`), in PowerShell from the repo folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
```

`scripts\check.ps1` reruns the checks and `scripts\deploy-pages.ps1` is `make deploy-pages`. On Windows write `.venv\Scripts\python.exe -m console`
wherever this file says `daybook` or `.venv/bin/daybook` (never pip's generated launcher exe: Norton blocks it and can kill
the terminal). No `PYTHONUTF8` is needed any more.

(Proves the toolchain works; uses `demo.sqlite`, not the real DB.) Then, after the README deploy steps:

```bash
cp daybook.example.json daybook.json
```

Fill `worker_url`, `pages_url`, `teacher_token` in `daybook.json` (gitignored). Then:

```bash
.venv/bin/daybook init
```

```bash
.venv/bin/daybook class add 고1A --term 2026-11-02..2027-02-26 --days 화,금 --reward "피자 파티"
```

- `--term` is `START..END`: it defines the scheduled class days (streak) and the reward goal.
- `--days` takes `월 화 수 목 금 토 일` or `mon..sun`, comma separated.
- The goal is computed at the first publish as active students × remaining sessions × 2.4 and then frozen;
  `--goal N` fixes it by hand, and `class edit 고1A --goal 0` recomputes it.

## 1b. Taste build for the students (F1)

`scripts\deploy-taste.ps1` (or `make deploy-taste`) reseeds `demo.sqlite`, builds `dist-taste/` with only the demo
student 콩 and deploys it to the Pages project `amp-taste` (https://amp-taste.pages.dev, the teacher's existing classic
Pages project), separate from the real one; `-Project <name>` (or `make deploy-taste TASTE_PROJECT=<name>`) picks
another. It needs only
`npx wrangler login`. Share the printed URL (landing → 학생 화면 보기 / 학부모 화면 보기); nothing a visitor does is sent
or stored anywhere.

## 2. Import the roster

Export the teacher's roster database as CSV. Expected columns: `Name, Student ID, Status, Number, Phone Number,
School` (`Archive` may be present; it is ignored). Then:

```bash
.venv/bin/daybook roster import <export>.csv --map mappings/roster.json --class 고1A
```

`mappings/roster.json` format:

```json
{
  "encoding": "utf-8-sig",
  "columns": {"Name": "full_name", "Student ID": "ext_id", "Status": "active", "Phone Number": "parent_phone"},
  "active_values": ["재원"]
}
```

- `columns`: CSV header → DAYBOOK field. The fields are `full_name` (required), `ext_id`, `active` and `parent_phone`.
  Unlisted columns are dropped.
- `active_values`: the `Status` values meaning "active"; anything else imports as inactive (no page is published).
- If the export uses other headers or another active label, edit the mapping, not the code.
- The CSV holds several classes: filter it per class first, or import once and move students with
  `daybook roster edit <slug> --class <name>`.
- Re-running is safe. Rows match by `Student ID`, else by an unambiguous name. Tokens never change on re-import.
- **Display names:** the default is the given name (last 2 characters of a 3-character Korean name).
  - A 2- or 4-character name, or a non-Korean one, keeps the whole name. The import warns about it, and publish
    refuses it (the full name must never be published).
  - Fix it with `daybook roster edit <slug> --display <given name>`.
- Slugs (the handle used in CLI commands) equal the display name, e.g. `daybook note 가람 …`. Check them with:

```bash
.venv/bin/daybook roster list 고1A
```

## 3. Send the links (once per student)

Publish and deploy once first (`make deploy-pages`), so the links exist before anyone opens them.

```bash
.venv/bin/daybook roster list 고1A --links
```

Send each student their `/s/<token>/` link and each parent their `/p/<token>/` link privately (1:1). Ask each
student to open their link once on their phone, then "홈 화면에 추가" (Safari share menu / Chrome menu). Handout QR
codes must only ever encode `https://<pages>/go/?set=<set_id>`; `daybook session start 고1A --set <set_id>` prints
that URL. A lost or leaked link is replaced with:

```bash
.venv/bin/daybook roster rotate-tokens 고1A --student <slug>
```

## 4. Weekly screener rounds → `tests`

The existing chain stays as the test input: Google Form → make.com → Notion "Screeners Tracker" → `analyze_round.py`.
DAYBOOK reads one local JSON snapshot per round; nothing in this repo talks to Notion, Google or make.com.

- **Files:** one per round, named `round_<set_id>_<YYYY-MM-DD>.json`, in one folder (outside the repo).
- **Top-level keys:** `set_id, date, n_students, n_quizzes, set_pressure_index, concept_difficulty, top_challenging,
  reason_distribution, students`. Only `set_id`, `date`, `n_quizzes` and `students` are read.
- **`students[]` keys:** `student` (ext_id or name), `name`, `correct`, `wrong`, `blank`, `blank_rate`, `commitment`,
  `reach_ceiling`, `archetype`, `top_reason`.
- **Stored as:** `tests.date` = `date`, `test_name` = `set_id`, `n_quizzes` (top level), `correct`, `wrong`, `blank`,
  `reach` = `reach_ceiling`, `efficiency` = correct / (correct + wrong) (1.0 if nothing attempted),
  `persona` = `archetype`, `top_reason`.
- **Matching:** `student` → `ext_id`, else `name` → full name. Unmatched or ambiguous rows are printed and skipped,
  never invented: check the printed list every Saturday.
- **Scale:** the parent trend draws `reach` and `efficiency` on 0–100 % and assumes both are 0..1. If
  `reach_ceiling` uses another scale, convert it when writing the snapshot. Direction thresholds: 0.03 (reach) and
  0.01 (efficiency), the local scripts' values (`console/models.py`).
- If `analyze_round.py` writes another shape, add a small converter on the local side that writes this format
  (see the synthetic `demo/screeners/`). Do not change the importer to read Notion.

```bash
.venv/bin/daybook import screeners <folder with round_*.json>
```

Fallback for any other test (CSV columns `date, student_slug, test_name, n_quizzes, correct, wrong, blank`; example
`mappings/tests.example.csv`):

```bash
.venv/bin/daybook import tests <file>.csv
```

## 5. Videos: the `videos.json` merge point

The student 영상 tab is rendered at publish from the file named by `videos_path` in `daybook.json` (default
`videos.json` in the repo, which holds synthetic links). For real links, write `videos.local.json` (gitignored) and set
`"videos_path": "videos.local.json"`. Schema (the last two fields are optional):

```json
[{"title": "1강 다항식의 연산", "url": "https://masonkat.github.io/q/cv/10-2/1/", "group": "고1 · 개념",
  "added": "2026-10-20", "kind": "long", "duration": "12:05", "yt_id": "<11-character YouTube id>",
  "thumb": "thumbs/cv-10-2-1.jpg"}]
```

- Solution videos use their fixed forwarding address `https://masonkat.github.io/q/<term>/<n>/`.
- Concept videos use `https://masonkat.github.io/q/cv/<grade-term>/<n>/`; their index is `q/cv/map.json`.
- Build the list from those existing records with a small local script (outside this repo or under `scripts/`).
- The feed is newest first by `added`; `group` is the chip on each reel (e.g. "고1 · 개념"). Only `http(s)` URLs are kept.
- Videos whose `added` is within the last 7 days of a publish also appear in the "새 영상" story.
- `kind`: `"short"` for the channel's daily Shorts (9:16 reels; use a vertical thumbnail) or `"long"` (default; 16:9
  cards in the 강의 sub-tab). `duration` ("12:05") shows on long cards.
- `yt_id`: the YouTube id. With it, tapping ▶ plays the video inside the reel through
  `https://www.youtube-nocookie.com/embed/<id>`. The iframe is created only on tap and sends only the site origin as
  the referrer. Without it, ▶ opens `url`.
- `thumb`: a local image path, relative to the videos JSON file. Publish shrinks it into `dist/t/<id>.jpg`
  (480 px wide, ≤ 60 KB; needs Pillow, installed by `make`). Download the thumbnails once into a local folder next to
  `videos.local.json`, never from the page. Without one, the reel shows a gradient card with the title.
- `demo/videos.demo.json` (written by `daybook demo`) and `demo/thumbs/` are a complete synthetic example.

## 5b. Assignment labels (optional)

Students never see a set id. The 오늘 tab shows a label for `?set=<set_id>` from `assignments.json` (gitignored; copy
`mappings/assignments.example.json`), else the topic of `daybook session start <class> --set <id> --topic "…"`,
else "{M/D} 과제". The lint stops publish if a raw id (`Adaptive_…`, `round_…`, `_G10`, `.json`) reaches a page.

## 6. The teacher's Saturday (after the pilot starts Friday 11-06)

Without a terminal (F6): start the board once (`.venv\Scripts\python.exe -m console board 고1A`, or a desktop shortcut to it),
open http://127.0.0.1:8787/teacher and use 가져오기 → 이번 주 한 줄 → 발행 → 배포 → 주간 메시지 복사. Set
`"pages_project"` in daybook.json if the Pages project is not called `daybook`. The same steps in a terminal:


```bash
.venv/bin/daybook pull
```

```bash
.venv/bin/daybook import screeners <round folder>
```

```bash
.venv/bin/daybook note <slug> 2026-W45 "한 줄"
```

```bash
make deploy-pages
```

- `make deploy-pages` runs `daybook publish` first.
- If the no-PII lint stops it, it prints the file and the kind (phone, full_name, ext_id, title, …).
  - Fix the source, usually a note, with a new `note` (the latest one is shown) or `roster edit --display`.
  - Then run it again. `dist/` is not changed by a failed publish.
- `daybook weekly 고1A` prints a paste-ready message per student with the parent link.
- Pages lag up to a week by design. The student's 오늘 완료 is immediate, because it comes from the Worker.

출석 (F2): at the start of class run `daybook session start 고1A --at 18:00` (or just open the board); write the printed
4-digit code on the board. Students tap ① 출석 on the 오늘 tab and type it (30 min before → 60 min after the start).
"출석 처리" on the board, or `daybook checkin <slug>`, covers a phone problem. After pulling this version, redeploy the
Worker once: `cd worker && npx wrangler deploy`.

오늘 한 줄 (F3): students may add one line and a chip when they upload. Read them with `daybook lines 고1A --days 7`
or on the board. Parents do not see them unless `"parent_shows_reflection": true` is set in daybook.json.

오늘의 장면 (F4): during class, "장면 올리기" on the board (photo + one line, tick the faces/names check), or
`daybook moment add 고1A --photo board.jpg --text "…"` and answer y. Up to 3 per class. Students see it the same day
(after the Worker redeploy) and react 🔥 👏 🤯; Saturday's publish bakes it into the pages and the parent report.
Never post faces, names or a whole book page.

F5 tools: `daybook class cancel 고1A 2026-11-12 --reason 휴강` (before 수능 week, so closed days do not break
streaks), `daybook qr sheet 고1A` (print the handout QR cards), `daybook note suggest 고1A` (edit, then run the
printed commands), `daybook export report <slug>` (상담 자료; open and print to PDF).

AMP Live (F7, prototype): put a question file in `live/` (same format as `demo/live/demo-round.json`; the text never
leaves the laptop), open http://127.0.0.1:8787/live from the board, 방 열기, show the code, 다음 문제 / 정답 공개 / 방 닫기.
Students tap "AMP Live" on the 오늘 tab and type the code. Re-check Cloudflare's Durable Objects free-plan page once
(DECISIONS, F7).

Offline (F9): the student page keeps working on bad Wi-Fi; a photo taken offline waits on the phone and is sent the
next time the page opens with a connection. Answer cards (F8) are a spike only: `daybook omr --spike --eval 200`.

Insights (F12): the local agent writes one JSON per screener round from the teacher's analysis scripts, in the format of
`mappings/insights.example.json` (ext_id, ≤ 3 focus items, ≤ 3 video URLs, `student_line`, `parent_line`), then
`daybook import insights <file>` (or the Teacher tab's import). Students see "이번 주 복습" that week; parents see the line
under 주간 테스트 (`"parent_shows_focus": true` adds the focus chips). Lines with a name, a phone or a raw id are left out.

Members (F11, Teacher tab → 명단 · 반 관리, laptop only): add a student (full name, display name, class, track, ID,
parent phone) and copy the new links and the ready 안내 문구; move a student to another class; 퇴원 (with a date, undo
possible); 링크 재발급 (the old link stops working); create or edit a class (name, grade, weekdays, term, reward, goal,
start time); and for a roster CSV, 가져오기 미리보기 before 이대로 가져오기. The Worker learns every change at once when
it is configured (and again at the next 발행). Full names and phones are shown only in this local tab.

Videos per class (F10): in `videos.json` (or a folder of `*.json` files: set `"videos_path": "videos/"`), tag an item
with `"classes": ["고1A"]` and/or `"grades": ["고1"]` and give each class its grade once
(`daybook class edit 고1A --grade 고1`). Untagged items show to everyone; `"pin": true` keeps this week's solution set
on top. A typo in a tag is a publish warning (the item then shows to nobody).

주간 테스트 (F14): before each weekly test, `daybook round add <round id> --grade 1학년 --classes 고1A --quizzes 20
--open "2026-11-06 20:00" --close "2026-11-08 23:59"` (or Teacher tab → 주간 테스트; redeploy the Worker once first:
`cd worker && npx wrangler deploy`). Students see a card on 오늘 and report there; `daybook round status <id>` shows who
has (after `daybook pull`). `daybook export reports --round <id>` writes `reports/<id>.csv` (or `--format notion-json`)
with the Notion "Screeners Tracker" columns; the local agent syncs that file to Notion from the laptop (nothing in the
repo talks to Notion). Example: `mappings/reports.example.json`. Full names are only in that local file.

Parent contact (F13, laptop only, nothing is sent): first set each class's hours and 지각 rule once,
e.g. `daybook class edit 기초B --start 20:00 --end 22:00 --late-min 30 --late-avg-min 30` and
`daybook class edit 케어D --start 17:30 --end 20:00 --self-study 21:00` (or the Teacher tab, 반 규칙), and each
parent's profile in 연락 현황 (전화/카톡/둘 다, 매주/필요할 때만, 어머님/아버님/보호자님). On Saturday open the Teacher tab:
연락 필요 (reasons with dates and times, a 3-line call script, a 카톡 draft) → call → 통화함 with one line; 이번 주 할 일
(about 100 minutes, red and 연락 필요 items never move); in the last week of the month 이달의 좋은 소식. In class, the
board's "선생님 보기" shows the class's flags; switch it off before projecting. Every parent draft lives in
`strings.ko.json` → `parent_msg` (rewrite the voice there once; keep it formal and emoji-free, a test checks it).
The contact log, profiles and summaries are personal data: they stay in `daybook.sqlite`, never in `dist/`.

In class (P2, from the week after the pilot): `daybook board 고1A` → http://127.0.0.1:8787 (attitude 1·2·3, live ✓,
"대신 제출"). Without the board: `daybook score 고1A <date> <slug> <0-3>` and `daybook submit <slug> --source paper`.

## 7. Safety checklist for the local side

- `git status` must never show `daybook.sqlite`, `daybook.json`, `dist/`, a roster CSV, screener snapshots or
  `videos.local.json`.
- Back up `daybook.sqlite` to a private place (it contains full names and phone numbers). `events` is append-only and
  is the audit log.
- The Worker keeps photos 7 days and records 14 days; `daybook pull --photos <private folder>` downloads photos if the
  teacher wants them (keep them out of the repo).
- To check a real-phone install (deferred here), open a student link on an iPhone (Safari → 공유 → 홈 화면에 추가) and
  on Android (Chrome → 홈 화면에 추가). It should open standalone on the 오늘 tab.
