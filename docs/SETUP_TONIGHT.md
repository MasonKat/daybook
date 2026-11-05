# 오늘 밤 설정 (SETUP TONIGHT) — runbook

One wizard does it all: `python -m console setup`. It previews every change, waits for "네", checks each step, and
**remembers where you stopped**. Type `그만` at any prompt to pause; run it again to continue.

Rehearsed end to end on synthetic data on 11-05 (4 classes, a CP949 roster with Korean headers and blank rows,
two rounds, publish against a local Worker, doctor). Fixes from that rehearsal are in this build.

---

## 0. Before you start (5 min)

Open **PowerShell** in the DAYBOOK folder. Always use the venv's Python (never `daybook.exe`).

```powershell
git pull
.venv\Scripts\python.exe -m pip install -e . --quiet
node --version
```

✓ `node --version` prints `v22…`. If it says "not recognized": install Node.js 22 LTS, open a **new** PowerShell.

Have ready:
- the roster CSV(s), one per class (Excel "CSV UTF-8" is best; plain "CSV" also works)
- `brand\mascot_fullicon.png` (the mascot)
- the BitLocker USB drive plugged in (e.g. `E:\daybook-backup`)
- the Cloudflare login (browser)

## 1. Start the wizard

```powershell
.venv\Scripts\python.exe -m console --db daybook.sqlite --config daybook.json setup
```

When it asks **"wrangler 명령은 제가 대신 실행할까요?"**, answer **네**: it runs wrangler for you, puts the secrets in
without showing them, and finds an existing D1 instead of failing. Answer 아니요 only if that fails; it then prints
the exact command to run yourself.

## 2. The 14 steps: what you type, what success looks like

| # | Step | You type | Success looks like |
|---|------|----------|--------------------|
| 1 | 데이터베이스 | 네 | `✓ database schema — v15` |
| 2 | workers.dev 주소 | (dashboard → Workers & Pages → pick a subdomain) 다 했어요 | `✓ workers.dev 주소 정하기` |
| 3 | D1 | 네 (auto) | `D1 'daybook'을 만들었어요` or `이미 있는 D1 'daybook'을 찾았어요` then `✓ D1` |
| 4 | 선생님 토큰 | 네, 네 | `선생님 토큰: AbCd…xy (43글자…)` · `Worker 비밀값 TEACHER_TOKEN 저장 완료` |
| 5 | Worker 배포 | 네, 네 | `Worker 주소: https://….workers.dev` · `✓ Worker version — v14` |
| 6 | 알림 키 | 아니요 (or 네 + your email) | — |
| 7 | daybook.json | Pages 주소, 학원 이름, `E:\daybook-backup`, `brand\mascot_fullicon.png`, Enter (36 px), 네 | `✓ pages_url` |
| 8 | 반 만들기 | per class: name, 월,수,금, term, 고1, 18:00, 20:00, Enter, reward, `#2563eb`, label, Enter (default rubric), 네 · then Enter | `✓ 반 만들기 — 반 4개: …` |
| 9 | 명단 | CSV path, class, 네 (check the preview!), call signs 네/아니요 · repeat per class · Enter | `✓ 명단 가져오기 — 재원생 N명` |
| 10 | 동의서 | y / n / Enter per student, 네 | ✓, or ⚠ "N not yes" (fine tonight) |
| 11 | 회차 | 네 → round id, 1학년, classes, 20, `2026-11-06 20:00`, `2026-11-08 23:59`, 네 · again for G11 (Sat 11-07) · 아니요 | `✓ … 회차 2개` |
| 12 | 발행 | 네 | `(lint passed)` · `Worker: N student links accepted` · `✓ student links on the Worker` |
| 13 | 페이지 배포 | 네 | `✓ 페이지 배포 — deployed` |
| 14 | 마지막 점검 | — | `✗가 없어요. 금요일 수업 준비 끝!` |

A typo (`6pm`, `pink`, a missing file) is caught **at that question** and asked again. Nothing is lost.

## 3. If something goes wrong (most likely first)

**A. "wrangler 로그인이 안 되어 있어요"**
```powershell
cd worker
npx wrangler login
cd ..
```
Allow it in the browser, then type `다 했어요` in the wizard (it checks again).

**B. D1 "already exists"**
Answer 네 to "제가 대신 실행할까요?": it finds the existing `daybook` D1. By hand:
`npx wrangler d1 list`, then copy the id on the `daybook` line and paste the whole line (the wizard extracts the id).
The Worker creates its own tables on the first request; there is no separate migrate step.

**C. The roster preview looks wrong**
- `CSV has no name column`: the header needs `Name` or `이름` / `성명`.
- `read as CP949`: just a note, it worked. ("CSV UTF-8" in Excel avoids it.)
- `display name equals the full name`: after setup, run
  `.venv\Scripts\python.exe -m console roster edit <slug> --display <이름>`
- Blank rows are ignored. A `호출명` column fills the call signs.
Answer **아니요** at the preview: nothing is imported, and it asks for the file again.

**D. 발행 says `publish STOPPED: the no-PII lint failed`**
The line above it names the problem, e.g. `[error] s/<token>/index.html: parent_phone: student id 7`.
Fix that student (usually the display name), then:
```powershell
.venv\Scripts\python.exe -m console --db daybook.sqlite --config daybook.json setup --from publish
```

**E. 마지막 점검 shows ✗ "Worker version" or "student links on the Worker"**
- version below v14: `cd worker` → `npx wrangler deploy`, then `setup --from doctor`.
- 401 / links ✗: the Worker's token differs from daybook.json. Redo the token step:
  `setup --from token` (answer 네 to auto), then `setup --from publish`.

**F. 페이지 배포 fails**
Usually login (see A) or the first deploy creating the Pages project. Fix it, then `setup --from deploy`.

## 4. Re-run one step / see where you are / roll back

See the progress (changes nothing):
```powershell
.venv\Scripts\python.exe -m console --db daybook.sqlite --config daybook.json setup --status
```
Redo exactly one step, then it continues with anything not done:
```powershell
.venv\Scripts\python.exe -m console --db daybook.sqlite --config daybook.json setup --from classes
```
Steps: `db subdomain d1 token worker vapid config classes roster consent rounds publish deploy doctor`.

Roll back:
- **Database**: never deleted or re-made by setup. Step 12 makes a backup on the USB drive
  (`daybook-YYYYMMDD-HHMM.sqlite`); to go back, close the board, copy that file over `daybook.sqlite`.
- **A wrong class / student**: fix it in the Teacher tab (반 수정, 명단), then `setup --from publish`.
- **wrangler.toml** (D1 id): `git checkout worker\wrangler.toml`, then `setup --from d1`.
- **A wrong Worker URL / Pages URL**: `setup --from worker` or `setup --from config`.

## 5. Safety (keep)

- The teacher token is never shown in full (`AbCd…xy`). It lives only in `daybook.json` and the Worker secret.
  Never paste it into a chat, a screenshot or a commit.
- Never commit `daybook.sqlite`, `daybook.json`, `setup_state.json`, `dist\`, a roster CSV.
- Setup refuses the demo database and demo config; it never touches them.

## 6. After setup (tonight / Friday)

1. Print the letters: `.venv\Scripts\python.exe -m console cards parents <class>` (no name, link or QR on paper).
2. Teacher tab → 명단 → 학생 안내 복사 → your own KakaoTalk (parents: only after a yes).
3. Teacher tab → 리모컨 → QR 보기 → open on your iPhone once → 홈 화면에 추가 (never print that QR).
4. From Friday, after each pull: Teacher tab → 학생 리뷰 and 아바타 확인 (승인 / 거절).
