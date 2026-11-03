# HANDOFF — for the local agents (real data)

The cloud build used synthetic data only. Real students, phones, scores and notes exist only on the teacher's laptop,
in `daybook.sqlite` (gitignored), and on the deployed pages behind unguessable links. Never commit `daybook.sqlite`,
`daybook.json`, `dist/`, a real roster CSV or real screener snapshots. Never deploy to GitHub Pages.

## 0. State at handoff (2026-11-03, after DESIGN v2 and features F1–F5)

- `main` on GitHub is green from a clean clone (Python 3.12/3.13, Node 22): 97 pytest, 22 Worker node tests,
  136 Playwright checks. Windows: `scripts\demo.ps1`, `scripts\check.ps1` (written without a PowerShell to run them
  here: please run them once on the laptop and report).
- Built: P0 (P1–P3), DESIGN v1 + v2, Windows/UTF-8 fixes, the raw-id lint, F1 taste build (콩), F2 출석, F3 오늘 한 줄,
  F4 오늘의 장면, F5 (class cancel, QR, class band, term report, note suggest). See `DECISIONS.md`.
- **Hotfix H1–H6 (D1).** Before the Worker deploy, create the D1 database once and paste its id into
  `worker/wrangler.toml`: `cd worker && npx wrangler d1 create daybook`, then `npx wrangler deploy`. Then run
  `daybook publish` once: it sends the student links (token map) the Worker now requires for uploads.
- Node ≥ 22 for the Worker tests. Please run `scripts\demo.ps1` on Windows PowerShell 5.1 and report (H5 was fixed
  without a PowerShell to run it here).
- Console schema is v5; an existing `daybook.sqlite` is migrated automatically (additive, no data lost) by the next command.
- Not done here: the Cloudflare deploys, a real-phone install test, live webcam capture on the board (deferred).

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

`scripts\check.ps1` reruns the checks and `scripts\deploy-pages.ps1` is `make deploy-pages`. Use `.venv\Scripts\daybook.exe`
wherever this file says `.venv/bin/daybook`. No `PYTHONUTF8` is needed any more.

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
student 콩 and deploys it to the Pages project `daybook-taste`, separate from the real one. It needs only
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
