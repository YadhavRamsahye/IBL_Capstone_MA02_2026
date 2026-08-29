# Briefing - Individual Contribution Report

> Factual briefing for drafting your Individual Report. Not finished prose. Everything below comes directly from `git log`/`git show`/`git merge-base` output against this repository.

## Identity confirmation

Before proceeding, I checked whether `sahilrughoo30-commits` is really you, rather than assuming it:

- `git log --all --author="sahilrughoo30-commits"` resolves to **exactly one** name+email pair: `sahilrughoo30-commits <sahilrughoo30@gmail.com>` - no aliasing, no second email under a similar name.
- Cross-referenced against in-code attribution: **11 files** carry an `Author:` header naming **"Sahil Singh Rughoo (22414560) - Tech Lead"** (exact spelling varies slightly, e.g. `[Rughoo Sahil Singh]` in the HTML templates vs. `Sahil Singh Rughoo` in the Python docstrings - see §4): [detection/hls_pipeline.py](detection/hls_pipeline.py), [detection/incident_detector.py](detection/incident_detector.py), [detection/pipeline.py](detection/pipeline.py), [detection/trafficwatch.py](detection/trafficwatch.py), [detection/__init__.py](detection/__init__.py), [tests/trafficwatch_test.py](tests/trafficwatch_test.py), [tests/test_dashboard.html](tests/test_dashboard.html), [templates/login.html](templates/login.html), [templates/map.html](templates/map.html), [templates/analytics.html](templates/analytics.html), and [main.py](main.py) (jointly with Yadhav Sharma Ramsahye).
- The git username `sahilrughoo30-commits` and the credited name `Sahil Singh Rughoo` are an obvious match.

**Confirmed**: `sahilrughoo30-commits` is your identity in this repository. Proceeding on that basis.

---

## 1. Full personal commit log

`git log --all --author="sahilrughoo30-commits"`, deduplicated across all branches (this identity's commits are all reachable from the `sahil_tasksdone` / `fix/audit-remediation` branch pair - running with `--all` adds nothing beyond what's on those branches). Repo URL: `https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026`.

| Date | Full Hash | Commit Link |
|---|---|---|
| 2026-02-23 | `66294f8050530e42e75caa4d2a71e9bb1d6a3f58` | [66294f8](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/66294f8050530e42e75caa4d2a71e9bb1d6a3f58) |
| 2026-03-09 | `294e92c54a4e7752b1a6d34a4aab04cf440535dc` | [294e92c](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/294e92c54a4e7752b1a6d34a4aab04cf440535dc) |
| 2026-03-09 | `d6f27deb5d25533478b530ede3d220d356e1a9d8` | [d6f27de](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/d6f27deb5d25533478b530ede3d220d356e1a9d8) |
| 2026-03-23 | `4cfce0f29e2ca419d85f0749ebb276d6cbe27f54` | [4cfce0f](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/4cfce0f29e2ca419d85f0749ebb276d6cbe27f54) |
| 2026-03-23 | `73ebf4d4df09a1e8e44b00f5ecc68759b4cd2c3d` | [73ebf4d](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/73ebf4d4df09a1e8e44b00f5ecc68759b4cd2c3d) |
| 2026-03-23 | `a28e25e647246f236fafb35086cc9648772a4155` | [a28e25e](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/a28e25e647246f236fafb35086cc9648772a4155) |
| 2026-04-27 | `885bd70d263af14629553266ae1a7ecb5d082b00` | [885bd70](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/885bd70d263af14629553266ae1a7ecb5d082b00) |
| 2026-04-27 | `9144380402c0707df74eddfbc6d1356a78f1e6ec` | [9144380](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/9144380402c0707df74eddfbc6d1356a78f1e6ec) |
| 2026-05-06 | `5434f0a2444aa231237336a8f1a769a27ee13fa5` | [5434f0a](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/5434f0a2444aa231237336a8f1a769a27ee13fa5) |
| 2026-05-06 | `857fe36716f25d2bf81156565a7ffc43c280885f` | [857fe36](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/857fe36716f25d2bf81156565a7ffc43c280885f) |
| 2026-05-06 | `6620d008fd5683bf17e52af397527c9027f6cf80` | [6620d00](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/6620d008fd5683bf17e52af397527c9027f6cf80) |
| 2026-05-06 | `e1a96a97b6b4db2bcb53f268312ff466329400fd` | [e1a96a9](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/e1a96a97b6b4db2bcb53f268312ff466329400fd) |
| 2026-05-06 | `9a548f549ab22313b768bd19f9795e98ba6438b0` | [9a548f5](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/9a548f549ab22313b768bd19f9795e98ba6438b0) |
| 2026-08-03 | `2b9df353c5fa1df2b2f19f7bb6898244712cf059` | [2b9df35](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/2b9df353c5fa1df2b2f19f7bb6898244712cf059) |
| 2026-08-03 | `4a512fb7ef6941ada8fe47dd45d47b755ee4bc74` | [4a512fb](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/4a512fb7ef6941ada8fe47dd45d47b755ee4bc74) |
| 2026-08-03 | `78d160c49982912608c0b150352ff33f8c4211bd` | [78d160c](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/78d160c49982912608c0b150352ff33f8c4211bd) |
| 2026-08-05 | `3b7838e04e320ac683c48ea83f9cdc72b0b95744` | [3b7838e](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/3b7838e04e320ac683c48ea83f9cdc72b0b95744) |
| 2026-08-05 | `c2832af36a7a6fc11fac1cdd38442f530b93a55c` | [c2832af](https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026/commit/c2832af36a7a6fc11fac1cdd38442f530b93a55c) |

Exact commit messages, for reference: `Test to Readme` (66294f8) · `First Frontend For Project.` (294e92c) · `Merge branch 'main' of https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026` (d6f27de) · `AI pipeline and n8n automation script.` (4cfce0f) · `Scraping part done.` (73ebf4d) · `Mock pipeline implemented.` (a28e25e) · `final changes` (885bd70) · `Incident detection final commit` (9144380) · `Add team attribution comments to backend and frontend` (5434f0a) · `Merge: resolved conflict in claude_api.py` (857fe36) · `Merge branch 'main' of https://bitbucket.org/curtincomputingprojects/ma02_2026` (6620d00) · `LS` (e1a96a9) · `test` (9a548f5) · `Fix security, bias and data-integrity issues found in codebase audit` (2b9df35) · `Adopt upstream's API kill switch into the merged Claude client` (4a512fb) · `Merge origin/main into fix/audit-remediation` (78d160c) · `Expand to all 38 MYT cameras, add Gemini provider, fix persistence bugs` (3b7838e) · `final chages for yadav to review.` (c2832af, sic - "chages").

**Total: 18 commits. Date range: 2026-02-23 → 2026-08-05 (a span of ~5.5 months, though clustered into a handful of active days rather than steady weekly activity - see §2).**

---

## 2. What each commit actually did

Grouping by natural theme, using `git show --stat` on every commit (not just message text) to verify actual content - several messages undersell or misdescribe what changed, flagged explicitly below.

### Phase 1 - Repo onboarding test (2026-02-23)
- **`66294f8` "Test to Readme"** - touches only `README.md` (+3/-1 lines). This is literally a test of the edit-a-file workflow (the repo's README was Bitbucket's own onboarding tutorial at the time, which walks through exactly this step). Not feature work.

### Phase 2 - First working frontend + backend scaffold (2026-03-09)
- **`294e92c` "First Frontend For Project."** - genuinely the first substantial commit: creates `main.py` (72 lines, an early FastAPI skeleton), `templates/login.html` (205 lines), `templates/map.html` (515 lines), and `requirements.txt`. This is the origin of the login page and map dashboard that the app still uses (in heavily evolved form) today.
- **`d6f27de`** - a merge commit; only content is a 5-line addition to `README.md`. Minor.

### Phase 3 - Detection pipelines built, same day (2026-03-23, 3 commits)
- **`4cfce0f` "AI pipeline and n8n automation script."** - creates `detection/__init__.py`, `detection/mock_pipeline.py` (149 lines - the simulated traffic-cycle generator still used today as a fallback), `detection/pipeline.py` (196 lines - the generic OpenCV/YOLO detection loop), and expands `main.py` by 170 lines to wire them in. No n8n-specific file appears in the diff despite the message - either the automation-script part was external tooling not committed, or it didn't survive into this snapshot; the pipeline/mock-pipeline part of the message is accurate and substantial.
- **`73ebf4d` "Scraping part done."** - creates `detection/hls_pipeline.py` (334 lines, the live HLS stream detection loop), `detection/trafficwatch.py` (221 lines, MYT camera-discovery/scraping logic), `detection/trafficwatch_test.py` (229 lines), and adds the `yolov8n.pt` YOLO model weight (6.5MB binary) to the repo. Message matches content - this is the camera-discovery/scraping foundation.
- **`a28e25e` "Mock pipeline implemented."** - the message is misleading: the mock pipeline was already created in the prior commit (`4cfce0f`). The actual content here is almost entirely `test_dashboard.html` (822 new lines - the standalone API test-monitor page), plus a small 17-line `main.py` tweak. Should be described in your report as "built the API test-monitor dashboard," not "implemented the mock pipeline."

### Phase 4 - Incident detection v1, first database layer, first technical doc (2026-04-27, 2 commits)
- **`885bd70` "final changes"** - creates the **first version of `detection/incident_detector.py`** (402 new lines) and substantially rewrites `templates/map.html` (1,199 lines changed). It also introduces a messy duplicate: a nested `linking codes/detection/hls_pipeline.py` path appears in the diff - a stray copy of part of the project tree, evidence of an accidental duplicate-folder commit that had to be cleaned up next.
- **`9144380` "Incident detection final commit"** - despite the name, the largest and most distinct piece of this commit is cleanup and new infrastructure, not incident detection (which barely changes here, +47/-… lines): it **deletes the entire stray `linking codes/` duplicate directory** introduced in the previous commit (removing `linking codes/main.py`, `linking codes/database.py`, `linking codes/detection/*`, `linking codes/requirements.txt`, etc.), adds the **first version of `database.py`** (71 lines - the SQLAlchemy engine setup), and adds **`TECHNICAL_BRIEFING.md`** (1,281 lines - a full technical document). Also renames `linking codes/check_db.py` to `check_db.py` at the repo root. This commit is better described as "repo cleanup + first database layer + first technical documentation" than "incident detection."

### Phase 5 - Attribution pass and messy May reconciliation (2026-05-06, 5 commits - the least clean stretch of this identity's history)
- **`5434f0a` "Add team attribution comments to backend and frontend"** - the message is accurate for part of the diff (small `Author:` header blocks added to ~15 files: `check_db.py`, `database.py`, `detection/*.py`, `main.py`, `run_schema.py`, `schema.sql`, `seed_users.py`, `templates/*.html`), **but the message says nothing about the fact that this same commit deletes `TECHNICAL_BRIEFING.md` entirely (-1,281 lines)** and trims 5 lines from `README.md`. That deletion is a significant, unexplained side effect worth being aware of if this document's history comes up.
- **`857fe36` "Merge: resolved conflict in claude_api.py"** - a real merge-conflict resolution, message matches content: merges this line of work with a parallel commit from YadhavRamsahye (`cd62ef5`), resolving conflicts in `detection/claude_api.py` (131 lines) and `main.py` (95 lines).
- **`6620d00`** merge from the Bitbucket remote (`https://bitbucket.org/curtincomputingprojects/ma02_2026`) - brings in a `README.md` rewrite (45 lines) but also **reintroduces `main copy.py` (384 lines) and `main.py.bak` (778 lines)** - stale backup files that a separate audit later flagged as clutter (they're still tracked in git today due to a `.gitignore` encoding bug - see the companion `PROJECT_BRIEFING.md` if present).
- **`e1a96a9` "LS"** and **`9a548f5` "test"** - on inspection, these are a short parallel branch off the same `9144380` parent as `5434f0a` (not a continuation of it), meaning they represent **independent, separate work happening at the same time as the attribution commit, not a linear sequence**. `e1a96a9` deletes `TECHNICAL_BRIEFING.md` again (-1,281 lines, on this separate line of history) and makes small additions to `main.py` (+9), `templates/map.html` (+14), and `tests/test_dashboard.html` (+12). `9a548f5` changes exactly one line, in a file literally named `ma02_2026` (an artifact resembling a broken git submodule/gitlink reference, not application code). **Both commit messages are genuinely uninformative and the actual changes are minor** - these should not be characterized as meaningful feature work in your report.

### Phase 6 - August 2026 security audit remediation + AI-provider/camera expansion (2026-08-03 → 08-05, on `fix/audit-remediation`) - by far the largest and most substantial phase
- **`2b9df35` "Fix security, bias and data-integrity issues found in codebase audit"** - a major rewrite, message accurately reflects its scale: **creates `auth.py` from scratch** (224 lines - real bcrypt authentication, session handling, replacing an earlier plaintext-credential approach), **creates `detection/severity.py`** (119 lines - the PCU-weighted congestion classification system), **creates `detection/stationary_tracker.py`** (227 lines - IoU-based stalled-vehicle tracking), and makes large changes across `main.py` (397 lines), `detection/claude_api.py` (232 lines), `schema.sql` (189 lines), `run_schema.py` (207 lines), `seed_users.py` (117 lines), `templates/analytics.html` (160 lines), plus smaller fixes to `database.py`, `detection/hls_pipeline.py`, `detection/incident_detector.py`, `detection/pipeline.py`, `detection/trafficwatch.py`, `templates/login.html`, `.env.example`, `requirements.txt`.
- **`4a512fb` "Adopt upstream's API kill switch into the merged Claude client"** - small and precise, message matches content exactly: 20 lines added to `detection/claude_api.py`.
- **`78d160c` "Merge origin/main into fix/audit-remediation"** - reconciles the audit-remediation line with the `main`/Bitbucket line; brings the `main copy.py`/`main.py.bak` backup files back into this line too, plus `README.md` and several `detection/*.py` files.
- **`3b7838e` "Expand to all 38 MYT cameras, add Gemini provider, fix persistence bugs"** - matches its message: expands `detection/camera_catalogue.py` (104 lines changed) toward the full 38-camera set, extends `detection/claude_api.py` (78 lines, laying groundwork for the Gemini provider), fixes persistence bugs in `detection/incident_detector.py` and `main.py`, adds `tools/fetch_cameras.py` groundwork.
- **`c2832af` "final chages for yadav to review."** *(sic - "chages")* - the message reads like a throwaway note, but this is **the single largest commit in this identity's entire history**: 2,256 insertions across 17 files. Creates **`detection/direction.py`** (162 lines - two-way road/per-direction traffic splitting), **`detection/providers.py`** (255 lines - the pluggable Gemini/Anthropic AI-summary backend), expands `detection/camera_catalogue.py` to its full 420-line, 38-camera form, and adds three new operator tools from scratch: `tools/calibrate_direction.py` (243 lines), `tools/check_gemini.py` (136 lines), `tools/tune_detection.py` (143 lines) - plus substantial extensions to `detection/severity.py`, `detection/stationary_tracker.py`, `detection/hls_pipeline.py`, `detection/trafficwatch.py`, `check_db.py`, `main.py`, `templates/map.html`. **The commit message significantly undersells this - it is the largest single unit of work in the whole personal history and should be described in the report by what it contains, not by its message.**

**Overall pattern**: work is heavily front-loaded into a handful of single-day bursts (3 commits on 2026-03-23, 2 on 2026-04-27, 5 on 2026-05-06, 4 on 2026-08-03/05) rather than spread evenly. The February and May commits (`66294f8`, `d6f27de`, `9a548f5`, `e1a96a9`) are genuinely trivial or low-signal and should not be inflated. The March, April, and especially August commits contain the real substance: the original frontend scaffold, the HLS/mock detection pipelines, the first incident detector and database layer, and - the largest body of work - the August security-audit rewrite (real authentication, PCU severity model, stalled-vehicle tracking) plus the direction-splitting and pluggable AI-provider systems.

---

## 3. Branch-level contribution

Two branches carry this identity's work; both share most of the same commit history (they diverge only slightly - see the companion `BRANCHING_BRIEFING.md` for the full topology).

| Branch | Total commits (all authors) | Commits by you | Date range (yours) | Ancestor of `origin/main`? | Ancestor of `origin/Dev`? |
|---|---:|---:|---|---|---|
| `sahil_tasksdone` (also `origin/sahil_tasksdone`, `all/sahil_tasksdone`) | 30 | 18 | 2026-02-23 → 2026-08-05 | **No** | **Yes** |
| `fix/audit-remediation` (also `origin/fix/audit-remediation`) | 29 | 17 | 2026-02-23 → 2026-08-05 | **No** | **Yes** |

You are the primary/majority author on both branches by a wide margin (18/30 and 17/29 respectively - the next-largest contributor on either branch is YadhavRamsahye with 5).

**What "ancestor of Dev but not main" means concretely**: your work on both branches was pulled into `origin/Dev` via a merge commit (`ef01921`, "Merge remote-tracking branch 'origin/sahil_tasksdone' into Yadhav_Recovery_And_Merge_Aug2026", authored by YadhavRamsahye on 2026-08-06, which was itself later merged into `Dev`). So your commits **are** part of the consolidated `Dev` history. However, as of the last recorded commit, **`Dev` itself has never been merged back into `origin/main`** - this is a project-wide state (`Dev` is 38 commits ahead of `main` with no merge-back yet), not something specific to your branches. If your report needs to state whether your work has "landed," the accurate phrasing is: *merged into the team's `Dev` integration branch, not yet merged into `main`* - see the companion `BRANCHING_BRIEFING.md` §5 for full detail on why.

---

## 4. Anything else attributable to this identity

**Code comment attribution** - an `Author:` header naming you appears in 11 files (see the Identity Confirmation section above for the full list and exact wording). These headers were added in a single commit (`5434f0a`, "Add team attribution comments to backend and frontend," 2026-05-06) and **were never updated after your much larger August rewrite**. Practical consequence: files you wrote substantially or entirely in `2b9df35` and `c2832af` - **`auth.py`, `detection/severity.py`, `detection/stationary_tracker.py`, `detection/direction.py`, `detection/providers.py`** - carry **no attribution comment naming you at all**, despite being your work per the commit diffs in §2. If your Individual Report relies on in-repo comments as evidence of authorship, note explicitly that those comments under-represent your actual contribution - the git history in §1–§2 is the more complete record.

**Documentation you wrote**: the first version of `TECHNICAL_BRIEFING.md` (1,281 lines) was added by you in `9144380` (2026-04-27) - though as noted in §2, it was subsequently deleted on two separate lines of your own later history (`5434f0a` and `e1a96a9`, both 2026-05-06) and only survives today because it was restored on `origin/Dev` by a different contributor's later commit (`f548fa0`, "docs: restore TECHNICAL_BRIEFING.md deleted in 5434f0a", YadhavRamsahye). Your authorship of the document's original content stands regardless of that back-and-forth.

**Pull Request descriptions**: could not be checked. The `gh` CLI is **not installed** in this environment (`gh: command not found` on both `gh --version` and `gh auth status`), matching the finding in the companion `BRANCHING_BRIEFING.md` §3/§8. No PR descriptions, review comments, or PR-level metadata attributable to you could be retrieved here - that would need to be checked directly on `github.com/YadhavRamsahye/IBL_Capstone_MA02_2026`'s Pull Requests tab (filtered to "All," and searched/filtered by you as author) if you need that for the report. Also worth noting from the branching analysis: **zero commits anywhere in this repository's history match GitHub's "Merge pull request" auto-generated message format**, so if you did open PRs, the actual merges were not performed through them (see `BRANCHING_BRIEFING.md` §3 for the full evidence).
