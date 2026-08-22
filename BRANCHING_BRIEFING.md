# Briefing - Branching Plan Sections (3.0–3.3, 7.2, 7.4)

> Factual briefing for drafting the Branching Plan document. Not finished prose. Everything below comes directly from `git log`/`git branch`/`git merge-base` output against this repository as checked out (branch `sahil_tasksdone`, remotes `origin` = `github.com/YadhavRamsahye/IBL_Capstone_MA02_2026`, plus a second remote `all` pointing at the same GitHub repo and - per its push URL - also a Bitbucket URL, and a third remote `bitbucket`). Where actual practice was inconsistent or messy, that is stated plainly rather than smoothed over, per your instructions.
>
> **Important caveat that affects every section below**: the local `main` branch in this checkout is stale relative to `origin/main` (`origin/main` has 8 commits `main` doesn't have; `main` has 2 commits - `9a548f5 test` and `e1a96a9 LS` - that never reached `origin/main`). All "vs. main" analysis below uses **`origin/main`** (the actual GitHub default branch) as the reference point, not the local `main` ref, since that's the authoritative shared branch.

---

## 1. Full branch inventory

| Branch | First commit | Last commit | Commits unique to it (`origin/main..branch`) | Merged into `origin/main`? |
|---|---|---|---:|---|
| `origin/main` | 2026-02-23 | 2026-08-05 | - (reference) | - |
| `origin/Dev` | 2026-02-23 | 2026-08-07 | 38 | **No** |
| `origin/Jehan_Testing` | 2026-02-23 | 2026-05-06 | 0 | Yes |
| `origin/Mokshan_Testing` | 2026-02-23 | 2026-05-06 | 0 | Yes |
| `origin/Testing_repo_Branch` | 2026-02-23 | 2026-02-23 | 0 | Yes |
| `origin/WebSocket` | 2026-02-23 | 2026-03-24 | 0 | Yes |
| `origin/moshan's-part` | 2026-02-23 | 2026-03-29 | 0 | Yes |
| `origin/Whitney_Testing` | 2026-02-23 | 2026-08-03 | 8 | No |
| `origin/Yadhav_Camera_Positions_Aug2026` | 2026-02-23 | 2026-08-06 | 28 | No |
| `origin/Yadhav_Env_Sync_Aug2026` | 2026-02-23 | 2026-08-05 | 1 | No |
| `origin/Yadhav_Fixes_Aug2026` | 2026-02-23 | 2026-08-06 | 19 | No |
| `origin/Yadhav_Incident_Detection_Aug2026` | 2026-02-23 | 2026-08-07 | 32 | No |
| `origin/Yadhav_Live_Camera_View_Aug2026` | 2026-02-23 | 2026-08-06 | 22 | No |
| `origin/Yadhav_Map_Accuracy_Aug2026` | 2026-02-23 | 2026-08-06 | 25 | No |
| `origin/Yadhav_Recovery_And_Merge_Aug2026` | 2026-02-23 | 2026-08-06 | 16 | No |
| `origin/fix/audit-remediation` (= local `fix/audit-remediation`) | 2026-02-23 | 2026-08-05 | 6 | No |
| `origin/sahil_tasksdone` (= local `sahil_tasksdone`, = `all/sahil_tasksdone`) | 2026-02-23 | 2026-08-05 | 7 | No |
| local `main` | 2026-02-23 | 2026-05-06 | (stale - see caveat above; behind `origin/main` by 8 commits, ahead by 2 that never reached the remote) | n/a |

"First commit" is 2026-02-23 for every branch because that's the repository's actual first commit (`Initial commit`, `b809b5598e`) and every branch traces back through it - it does not mean 8+ people started independent work that day.

**11 of 17 branches (65%) are unmerged into `origin/main` as of the last recorded commit** (2026-08-07), including the branch with the single largest amount of unique work (`origin/Dev`, 38 commits) and every branch created in August 2026.

---

## 2. Naming pattern analysis

There is **no single naming convention** applied consistently. At least four distinct patterns coexist, plus outliers:

1. **`Name_Testing`** - a simple personal-name-plus-role pattern, used identically by three people: `Jehan_Testing`, `Mokshan_Testing`, `Whitney_Testing`. Capitalized name, capitalized "Testing", single underscore.
2. **`Yadhav_<Topic(s)>_Aug2026`** - a later, more elaborate pattern used only by one contributor, only in August: `Yadhav_Camera_Positions_Aug2026`, `Yadhav_Env_Sync_Aug2026`, `Yadhav_Fixes_Aug2026`, `Yadhav_Incident_Detection_Aug2026`, `Yadhav_Live_Camera_View_Aug2026`, `Yadhav_Map_Accuracy_Aug2026`, `Yadhav_Recovery_And_Merge_Aug2026`. Multi-word topic, month+year suffix. Nobody else used a dated suffix.
3. **`name_status`, all-lowercase** - `sahil_tasksdone`. Lowercase personal name (unlike the capitalized `Name_Testing` pattern above), and "tasksdone" is a status word, not a topic.
4. **`type/kebab-case`** - `fix/audit-remediation`. The only branch using a slash namespace and a Conventional-Commits-style type prefix (`fix/`). Resembles a formal git-flow/trunk-based convention but appears exactly once in the whole repository, from one contributor, and it isn't picked up anywhere else.

**Outliers that fit none of the above**:
- `Dev` - bare capitalized word, no personal name, no underscore, no topic. Functions as a shared/integration branch name but was created and used by a single person (see §5).
- `WebSocket` - bare CamelCase feature name, no personal-name prefix at all.
- `Testing_repo_Branch` - inconsistent internal capitalization: `Testing` and `Branch` are capitalized but `repo` is lowercase, in the same name.
- `moshan's-part` - contains an apostrophe (an unusual character for a git ref - it works, but it's atypical and awkward to reference on a command line without quoting). Also lowercase throughout, unlike the `Name_Testing`/`Yadhav_...` branches which capitalize the personal name. The name itself also appears to be a misspelling of team member "Mokshan" (the schema.sql / database.py file headers, and the `mm5058` git identity's likely real name, are "Mokshan Mehess") - so this branch's own name doesn't even agree with the spelling used elsewhere in the repo for the same person.
- `main` / `Dev` are the only two branches with no personal name and no slash, but styled completely differently from each other (`main` lowercase-default, `Dev` capitalized).

**Plain-language summary**: roughly half the branches (the three `Name_Testing` ones, plus `main`) follow a simple, consistent early pattern. From August 2026 onward, one contributor (Yadhav) introduced a more elaborate dated multi-word convention that nobody else used, alongside two more one-off styles from the same recent period (`sahil_tasksdone`, `fix/audit-remediation`). There is no single convention that spans the whole project or the whole team.

---

## 3. Was a Pull Request workflow actually used?

**No GitHub PR merges found.**

- `git log --all --grep="Merge pull request"` → **0 matches**. GitHub's auto-generated "Merge pull request #N from owner/branch" message never appears anywhere in this repository's history, on any branch.
- `git log --all --merges --oneline` → **14 merge commits total**, and every single one is a local-style merge message (`Merge branch '...'`, `Merge branch '...' of <url>`, or a custom message on a merge commit) - none carries a PR number or GitHub's PR merge format. Examples, quoted exactly with hashes:
  - `d20e300` (2026-08-07, YadhavRamsahye): `Merge branch 'Yadhav_Incident_Detection_Aug2026' into Dev`
  - `78d160c` (2026-08-03, sahilrughoo30-commits): `Merge origin/main into fix/audit-remediation`
  - `6620d00` (2026-05-06, sahilrughoo30-commits): `Merge branch 'main' of https://bitbucket.org/curtincomputingprojects/ma02_2026`
  - `857fe36` (2026-05-06, sahilrughoo30-commits): `Merge: resolved conflict in claude_api.py` - a hand-written message on what git otherwise generated as a merge commit, i.e. still a local merge, just with the default message overwritten.
  - `89b2ff8` (2026-03-24, Jehan151212): `Merge branch 'YadhavRamsahye:main' into main` - the closest thing to PR-shaped naming (owner:branch syntax, suggesting a fork was involved), but it still lacks a PR number and any PR body text, so it reads as a local merge of a fork's branch rather than a GitHub-mediated PR merge.
- `gh` (GitHub CLI) is **not installed** in this environment (`gh: command not found` on both `gh --version` and `gh auth status`) - `gh pr list` could not be run, so an authoritative PR count/list from GitHub itself could not be obtained here. This needs to be checked directly on github.com (the repo's "Pull requests" tab, including the "Closed" filter) to be certain no PRs exist or were ever opened.

**Plain-language conclusion**: based on git history alone, every merge in this repository's recorded history was performed locally (via `git merge` on someone's machine) and then pushed, not through GitHub's PR merge button or the `gh` CLI. Sections 7.2.5 ("Create Pull Request") and 7.4 **cannot honestly describe PRs as the mechanism that actually got code into `main`/`Dev`** based on what git history shows - if PRs were opened for review/discussion and then merged locally afterward instead of via the "Merge pull request" button, that would still not produce the `git log` signature we're missing, so it's also possible PRs were opened and closed without being the actual merge mechanism. Either way, **this specific point should be verified on github.com's Pull Requests tab** before section 7.4 asserts a PR-based merge workflow - the git history by itself only supports describing a local-merge workflow.

---

## 4. Commit message conventions

20 messages sampled across every author identity and spread from the first commit to the most recent, quoted exactly with hash/date/author:

| Hash | Date | Author | Message |
|---|---|---|---|
| `b809b55` | 2026-02-23 | Yad Kuriboh | `Initial commit` |
| `3c9ec80` | 2026-02-23 | mm5058 | `test 2` |
| `294e92c` | 2026-03-09 | sahilrughoo30-commits | `Test to Readme` |
| `d6f27de` | 2026-03-09 | sahilrughoo30-commits | `Merge branch 'main' of https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026` |
| `73ebf4d` | 2026-03-23 | sahilrughoo30-commits | `Scraping part done.` |
| `89b2ff8` | 2026-03-24 | Jehan151212 | `UI Heat Map Update` |
| `f54a384` | 2026-03-24 | Whitney Ramsamy | `Added database table script` |
| `2e724d5` | 2026-03-29 | YadhavRamsahye | `feat: add Claude API integration for AI-generated traffic summaries` |
| `2e1c3fd` | 2026-03-29 | YadhavRamsahye | `chore: remove pycache from version control` |
| `dac077b` | 2026-03-30 | mm5058 | `linking` |
| `c0e803a` | 2026-04-27 | Software Engineering Projects | `Initial commit` |
| `9144380` | 2026-04-27 | sahilrughoo30-commits | `Incident detection final commit` |
| `cd62ef5` | 2026-05-04 | YadhavRamsahye | `Futher Polish of main.py and claude api fixes` *(sic - "Futher")* |
| `e1a96a9` | 2026-05-06 | sahilrughoo30-commits | `LS` |
| `9a548f5` | 2026-05-06 | sahilrughoo30-commits | `test` |
| `fc15099` | 2026-08-03 | Whitney Ramsamy | `Add vehicle detection and bottleneck analysis tests` |
| `2b9df35` | 2026-08-03 | sahilrughoo30-commits | `Fix security, bias and data-integrity issues found in codebase audit` |
| `3b7838e` | 2026-08-05 | sahilrughoo30-commits | `Expand to all 38 MYT cameras, add Gemini provider, fix persistence bugs` |
| `009f229` | 2026-08-05 | YadhavRamsahye | `chore(deps): add sqlalchemy, asyncpg and bcrypt to requirements` |
| `74e57d2` | 2026-08-07 | YadhavRamsahye | `fix(detection): recalibrate stationary_tracker against measured reality, not assumptions` |

**Was a tag/prefix convention used?** Inconsistently, and it changed over time and by person:

- **No convention at all** for most of the project's history and for most contributors: `Initial commit` (used twice, by two unrelated authors - Yad Kuriboh on 2026-02-23 and Software Engineering Projects on 2026-04-27), `test 2`, `Test to Readme`, `linking`, `LS`, `test`, `UI Heat Map Update`, `Added database table script`, `Scraping part done.` - plain, ad hoc, sentence-style or fragment-style descriptions, several with a trailing period and several without, no prefix of any kind.
- **A loose "type: description" (Conventional Commits–flavoured) style** appears starting 2026-03-29, used only by YadhavRamsahye, and only sometimes even then: `feat: add Claude API integration...`, `chore: remove pycache from version control`. Early uses have no scope (just `feat:`/`chore:`).
- **A stricter `type(scope): description` form** (full Conventional Commits with a scoped component) appears only in the August 2026 commits, again only from YadhavRamsahye: `chore(deps): add sqlalchemy, asyncpg and bcrypt to requirements`, `fix(detection): recalibrate stationary_tracker against measured reality, not assumptions`, `feat(camera): capture annotated frames and serve them via the API`, `docs(security): redact plaintext DB password from TECHNICAL_BRIEFING.md`. This is the most disciplined and consistent stretch of commit messages in the whole history, but it is confined to one contributor's branches in the final ~4 days of recorded history.
- **Whitney Ramsamy's August commits** use their own consistent-but-different pattern - `Add <thing>` / `Fix <thing>` sentence case, no colon, no type tag: `Add vehicle detection and bottleneck analysis tests`, `Add regression tests to prevent fixed bugs`, `Add resilience tests for failure recovery`, `Fix application behavior test suite`.
- **sahilrughoo30-commits** never adopts a tag convention at all, across the entire history from February to August - messages stay free-text throughout (`Fix security, bias and data-integrity issues found in codebase audit`, `final chages for yadav to review.` *(sic - "chages")*, `Expand to all 38 MYT cameras, add Gemini provider, fix persistence bugs`), including some very low-content ones as late as May 2026 (`LS`, `test`).

**Plain-language summary**: there was no team-wide commit message convention. Most history is free-text with no tag. One contributor (Yadhav) introduced Conventional-Commits-style tags partway through and used them consistently only in their final push; nobody else adopted that style. At least two placeholder/test commits (`test`, `test 2`, `LS`) made it into permanent history rather than being squashed or amended out.

---

## 5. Branch relationships - was there a real `main → Dev → personal` hierarchy?

**No - not for most of the project, and even where `Dev` exists, it was never merged back.** The `merge-base` evidence splits branches into two clearly different groups:

**Group A - forked directly from `main`, `Dev` never a factor** (merge-base against `origin/main` and `origin/Dev` is *identical*): `origin/Jehan_Testing`, `origin/Mokshan_Testing`, `origin/Testing_repo_Branch`, `origin/WebSocket`, `origin/Whitney_Testing`, `origin/moshan's-part`. These branches' fork points predate any commit that exists only on `Dev`, so as far as their ancestry is concerned, `Dev` might as well not exist - they branched from `main`.

**Group B - merge-base against `main` and `Dev` *differ*** (`fix/audit-remediation`, `sahil_tasksdone`, and all seven `Yadhav_*_Aug2026` branches). Critically, in every one of these cases the merge-base against `Dev` equals **that same branch's own tip commit** - e.g. `merge-base(origin/Dev, origin/Yadhav_Camera_Positions_Aug2026)` = `ab58546`, which is `Yadhav_Camera_Positions_Aug2026`'s own most recent commit. That pattern means these branches are *ancestors* of `Dev` (i.e., `Dev` later merged them in), **not** that they were forked off `Dev`. Their merge-base against `origin/main` (`fac88ed`, 2026-08-05, itself a `Merge branch 'main' of ...` commit sitting on `main`'s own line) confirms they were forked from `main`, at a point in early August 2026.

**When did `Dev` actually start functioning as a branch?** All of `Dev`'s 38 commits not on `origin/main` are dated **2026-05-06 or later**, and the six merge commits that actually make `Dev` a distinct integration point (`Merge branch 'Yadhav_..._Aug2026' into Dev`, plus `Merge remote-tracking branch 'origin/sahil_tasksdone' into ...`) are all dated **2026-08-06 or 2026-08-07** - the last two days of recorded history - and were all authored by a single person, **YadhavRamsahye**. `origin/main` has **zero commits that aren't also in `origin/Dev`** (`git log origin/Dev..origin/main` = empty), meaning `Dev` is a strict superset of `main` - but the reverse check (`git log origin/main..origin/Dev`) confirms `Dev` is **38 commits ahead and has never been merged back into `main`**.

**Plain-language conclusion**: For nearly the entire observed project timeline (2026-02-23 through early August), the actual model was **`main` + directly-forked personal/testing branches** - a two-tier structure, not three. A `Dev` branch only became active in the final days of recorded history, and when it did, it functioned as **one person's late-stage integration branch** that pulled together several already-`main`-based feature branches (all authored/merged by YadhavRamsahye) rather than as a standing tier that the team branched from throughout. As of the last recorded commit, `Dev`'s consolidated work has **not** been merged into `main` - so if 3.3's diagram shows a clean `main → Dev → personal branch → back to Dev → back to main` cycle, that final step (`Dev → main`) had not actually happened yet in this history. A diagram that shows `main` with personal branches forking directly from it, plus a note that `Dev` emerged late as one contributor's integration point and is still unmerged, would match the evidence; a clean three-tier hierarchy sustained across the whole project would not.

---

## 6. Merge graph

Generated with the exact requested command:

```
git log --graph --oneline --all --decorate --no-color > graph_for_report.txt
```

**Confirmed saved**: `graph_for_report.txt`, 100 lines total, at the repository root.

First 60 lines (representative excerpt - shows the `Dev` consolidation merges at the top of history, down into the `sahil_tasksdone`/`fix/audit-remediation` line and the `main`/Bitbucket merge history):

```
*   d20e300 (origin/Dev) Merge branch 'Yadhav_Incident_Detection_Aug2026' into Dev
|\
| * 1de2471 (origin/Yadhav_Incident_Detection_Aug2026) fix(analytics): surface traffic_snapshots.is_incident instead of leaving it unread
| * 74e57d2 fix(detection): recalibrate stationary_tracker against measured reality, not assumptions
| * 700b02a feat(tools): add incident_harness.py and an admin demo endpoint to prove the chain
| * 1d9a120 feat(incidents): wire stalled-vehicle detection into the incident pipeline
* | 388c102 Merge branch 'Yadhav_Camera_Positions_Aug2026' into Dev
|\|
| * ab58546 (origin/Yadhav_Camera_Positions_Aug2026) feat(tools): gate catalogue regeneration on the road-proximity validator
| * da7b0ce fix(cameras): re-derive casernes/la_chaussee, geocode 5 more, drop fake ring placement
| * a2689c2 feat(tools): add validate_camera_coords.py - a check that can actually fail
* | 4b2c484 Merge branch 'Yadhav_Map_Accuracy_Aug2026' into Dev
|\|
| * 89069d7 (origin/Yadhav_Map_Accuracy_Aug2026) fix(cameras): correct two catalogued positions, audit the rest
| * 3034feb fix(map): scope MJPEG to the popup, event-driven streaming, layout fixes
| * 8db45e6 fix(auth): stop advertising and defaulting-on the demo login backdoor
* | cd95033 Merge branch 'Yadhav_Live_Camera_View_Aug2026' into Dev
|\|
| * 9132acb (origin/Yadhav_Live_Camera_View_Aug2026) feat(map): replace polled camera thumbnails with live MJPEG streaming
| * 9ce2317 feat(map): add a camera overview strip below the map
| * 8184658 feat(map): show the live annotated camera frame in each popup
* | 13356af Merge branch 'Yadhav_Fixes_Aug2026' into Dev
|\|
| * bec3a54 (origin/Yadhav_Fixes_Aug2026) feat(camera): capture annotated frames and serve them via the API
| * 213d96a fix(logging): make "[db] Engine ready" actually appear in the log
| * 37c25b5 fix(db): close the bottleneck-event/incident foreign-key race
* | a83bcf2 Merge branch 'Yadhav_Recovery_And_Merge_Aug2026' into Dev
|\|
| * 7f0b661 (origin/Yadhav_Recovery_And_Merge_Aug2026) docs(security): redact plaintext DB password from TECHNICAL_BRIEFING.md
| * b7c0830 docs: replace Bitbucket onboarding boilerplate with a real README
| * d286850 chore(repo): remove stray gitlink and dead backup files
| * 1bdf0cd fix(repo): rewrite .gitignore as clean UTF-8
| * 0af7535 fix(deps): deduplicate requirements.txt after merging sahil_tasksdone
| *   529fe97 Merge branch 'Yadhav_Env_Sync_Aug2026' into Yadhav_Recovery_And_Merge_Aug2026
| |\
| | * 009f229 (origin/Yadhav_Env_Sync_Aug2026) chore(deps): add sqlalchemy, asyncpg and bcrypt to requirements
| |/
|/|
| *   ef01921 Merge remote-tracking branch 'origin/sahil_tasksdone' into Yadhav_Recovery_And_Merge_Aug2026
| |\
| | * 3b7838e (HEAD -> sahil_tasksdone, origin/sahil_tasksdone, all/sahil_tasksdone) Expand to all 38 MYT cameras, add Gemini provider, fix persistence bugs
| | * c2832af (origin/fix/audit-remediation, fix/audit-remediation) final chages for yadav to review.
| | * 4a512fb Adopt upstream's API kill switch into the merged Claude client
| | *   78d160c Merge origin/main into fix/audit-remediation
| | |\
| | * | 2b9df35 Fix security, bias and data-integrity issues found in codebase audit
| | * | 9a548f5 (main) test
| | * | e1a96a9 LS
| * | | f548fa0 docs: restore TECHNICAL_BRIEFING.md deleted in 5434f0a
|/ / /
* | |   fac88ed (origin/main, origin/HEAD) Merge branch 'main' of https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026
|\ \ \
| | |/
| |/|
* | | e9aa386 Yadhav take over next phase after Sahil
| | | * 5544c26 (origin/Whitney_Testing) Fix application behavior test suite
| | | * fc15099 Add vehicle detection and bottleneck analysis tests
| | | * 80e1957 Add resilience tests for failure recovery
| | | * 903891e Add regression tests to prevent fixed bugs
| | | * 4dfc221 Add integration tests for system components
```

Note the decorated ref `(main)` on `9a548f5 test` - this visually confirms the local `main` staleness described in the caveat at the top of this document: local `main` points at a commit that reads simply `test`, three commits before the branch structure converges back with `origin/main`.

---

## 7. Any committed workflow documentation

**Not found.** No `CONTRIBUTING.md`, no `docs/` directory, and no file describing branching conventions, naming rules, or a merge workflow exists anywhere searched (repo root, up to 3 levels deep, across common naming patterns for such a file).

Two related documents do exist but neither covers branching/git workflow:
- [README.md](README.md) - on the currently checked-out branch (`sahil_tasksdone`), this is still the **generic Bitbucket onboarding boilerplate** (mentions "Branches" only as a generic pointer to Bitbucket's own UI tab, not a project convention). A real, project-specific README (commit `b7c0830`, `docs: replace Bitbucket onboarding boilerplate with a real README`) exists only on `origin/Yadhav_Recovery_And_Merge_Aug2026` / `origin/Dev` - it documents the tech stack and setup steps but contains **no mention of branching** either.
- `TECHNICAL_BRIEFING.md` (1281 lines, exists on `origin/Dev`, not on `sahil_tasksdone`) - a product/technical document; grepped for "branch" and found nothing related to git workflow.

So regardless of which branch is treated as canonical, **no committed source describes the team's intended branching strategy** - sections 3.0–3.3 would be documenting a convention that, if it existed, was never written down anywhere in the repository itself.

---

## 8. What this briefing can't tell us

The following live in GitHub's hosted settings/API, not in local git history, and **could not be checked** in this session because the `gh` CLI is not installed here (confirmed in §3):

- **Branch protection rules** on `main` (or `Dev`) - e.g. whether direct pushes were even blocked, whether status checks were required. Nothing in git history can confirm or rule this out; a branch with no protection and a branch with strict protection can produce identical local-merge history if the protection was never actually configured.
- **Required-reviewer settings** - whether PRs (if any were opened) required approval before merging.
- **CODEOWNERS enforcement** - no `CODEOWNERS` file was found in the repository tree searched, but that file living in the default branch on GitHub vs. being absent entirely is also worth confirming directly, since only certain branches were searched locally.
- **Whether any Pull Requests were ever opened and closed without being merged**, or merged via "Squash and merge"/"Rebase and merge" (which do **not** always leave the literal phrase "Merge pull request" - squash merges in particular can produce a single commit with an arbitrary message that would be indistinguishable from a local commit in this analysis). This is the biggest caveat on §3's conclusion: a squash-merged PR could be hiding inside the plain (non-merge) commits counted in §4 and would not show up as a "merge commit" at all.

**To check these**, either open the repository directly on `github.com/YadhavRamsahye/IBL_Capstone_MA02_2026` → Settings → Branches (for protection rules) and the Pull Requests tab (filtered to "All") → for PR history, or install/authenticate `gh` and run:
```
gh pr list --state all --limit 200
gh api repos/YadhavRamsahye/IBL_Capstone_MA02_2026/branches/main/protection
```
Neither was runnable here since `gh` returned "command not found" for both `gh --version` and `gh auth status`.
