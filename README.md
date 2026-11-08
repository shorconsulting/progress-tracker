# progress-tracker

A Claude Code plugin that reports build progress across your active repositories in the build-status layout.
Each repository gets its own page: a plain-language summary of the latest progress first, then tiles with the 7-day
change, a module table showing the bar from 7 days ago above today's bar (percentages, change, and a state label of
Near done / In progress / Early), Moved most and Furthest behind, Biggest untouched gaps and Near done, "What changed
in the last 7 days, in plain terms" (per-module bullets checked against the merged PRs by a second agent), Coming
next, and How to read this. The tracker's own measures stay: % started and % done per module, frontend / backend /
wiring status, PR throughput, and the 1-day change as a secondary figure. Light and dark themes.

The window is the last 7 days at the same time of day (never Monday to today). Published as one artifact per
repository (own URL each, so a repository can be shared with its partner alone) plus an overview when more than one
repository ran.

It stores nothing between runs: figures are recomputed from issue `closed_at`, PR `merged_at` and git first-add
dates, so it gives the same answer from whichever Claude account runs it that day.

## Install (any account)

In a terminal Claude Code session:

```
/plugin install progress-tracker --marketplace homeporthealth/app
```

Answer `y` to add the marketplace and pick the user scope. The plugin needs `python3`, `git`, and GitHub read
access (the `gh` CLI, or `GH_TOKEN` / `GITHUB_TOKEN` in the environment).

Settings in the plugin's `config/repos.default.json` reach every install: on first run it is copied to
`~/.claude/progress-tracker/repos.json`, and on later runs any setting a repository's entry lacks is filled in from
the default entry for the same repository (settings you changed are kept).

## Commands

| Command | What it does |
|---|---|
| `/progress-report [--only a/b,c/d] [--skip a/b]` | Builds the report, drafts and fact-checks the plain-terms notes, and publishes one `<Title> build progress` artifact per repository (stable URLs), plus the `Build progress` overview when more than one repo ran |
| `/progress-repos list` | Shows the repositories in the config and whether each is on |
| `/progress-repos enable owner/repo` / `disable owner/repo` | Turns a repository on or off for the daily run |
| `/progress-repos add owner/repo [--title T] [--modules NAME]` | Adds a repository (enabled) |
| `/progress-repos remove owner/repo` | Removes it from the config |

The config lives at `~/.claude/progress-tracker/repos.json` (override the directory with
`PROGRESS_TRACKER_HOME`). It is per machine / per cloud environment, so toggle repositories on each account you run
the routine from, or pass `--only` / `--skip` in the routine's prompt instead.

## Run it daily as a Routine

On each account you want covered, create a Routine (Claude Code web: Routines, or ask Claude in a cloud session to
create one) with:

- Schedule: daily, e.g. `CRON_TZ=America/Toronto 10 7 * * *`.
- Fresh session per firing, in an environment that has GitHub access to the repositories.
- Prompt:

  ```
  /progress-report
  ```

  or, to pin the set of repositories regardless of that account's config:

  ```
  /progress-report --only homeporthealth/app,owner/other-build
  ```

The prompt runs the command unattended and ends with the artifact link.

## What the numbers mean

- **Modules** come from one of two sources per repository (`moduleSource` in the config):
  - `labels`: an issue label matching `moduleLabelPattern` names the module (`m05` -> M05 in homeporthealth/app,
    `module:9.12` -> 9.12 in ranch-rides). `moduleIdFormat` renders the id (`M{n:02d}`, `9.{n}`).
  - `epics`: issues labelled `epic` are the modules. An issue belongs to the epic whose body lists it (`#41`) or
    whose number its own body cites (`owner/repo#15`). The id comes from `epicIdPattern` over the epic title
    (`[E03]` -> E03 in wyset, `Epic D` -> Epic D in tidyhaus); an epic the pattern misses shows as `#<number>`.
  Issues with no module form **M00 Foundation / unattributed**. Modules are always listed in the same order:
  M00, then ids ascending numerically (9.1, 9.2, 9.10), then lettered, then `#n`.
- **% complete (module)**: done tracked issues / all tracked issues. *Done* = merged work and marked done (closed or
  labelled `done`; see below). A PR claims an issue when `#N` is in its
  title, or in its body after Ref / Closes / Fixes / Resolves / Implements / Issue(s), lists included
  (`Issues #346, #347 and #348`); a bare `#N` elsewhere in the body ("#110 was read for context") does not
  count. `issueRefRule: "any"` per repository relaxes that to every `#N`. With `doneFromCommits: true`
  (wyset, where work lands on main with `Ref #NN` and stories stay open) a default-branch commit citing the
  issue counts too, within `commitLookbackDays`. `trackedIssueLabels` limits the base (`agent:build`, `story`);
  `excludeIssueLabels` drops epics, questions, duplicates and the like.
  **Aliases:** `issueAliases: {"mention": "\\bAT-(\\d+)(?:\\s*(?:-|to)\\s*(?:AT-)?(\\d+))?", "title": "^AT-(\\d+)\\b"}` reads
  "AT-59" or "AT-51 to AT-54" in a PR or commit as naming the issues titled "AT-59: ..." (a plan's own numbering). Use a
  distinctive token: a plain "item 59" also matches other numbered lists and sentences like "not in this change: item 59".
  **Closed is done:** `closedCountsAsDone: true` counts a requirement closed as completed as done even when no PR named
  it; the page says so under How to read this.
  **Inferred links:** `inferPrLinks: true` ties PRs that name no issue to the issue they are about (requirement id, else wording) and lists them in `inferred-links.csv`; nothing is written to GitHub.
  **Unlinked PRs to M00:** `foundationForUnlinkedPrs: true` counts every PR that builds no tracked requirement under M00
  rather than in the module its files point to. `unattributedIsExpected: true` keeps placement by files and only drops
  the unlinked-PR and no-module warnings.
  **Modules from code:** a probes file with `match` globs and `keywords` places PRs by the files they change and issues by
  their PR or their words (`config/modules/tidyhaus.json`).
  **% started** (the meter) = requirements with a PR opened or merged that names them / all tracked requirements; **% done**
  is reported beside it. `startedFromCommits: true` also counts default-branch commits that cite the requirement.
  **Modules:** an issue with no module is placed from a mention in its title or body, then its merged PR(s), then the
  files those PRs changed (`moduleLabelFormat` writes the label back on label-sourced repos); the rest are warned about.
  **Done = merged work AND marked done**, in every repository. Merged work: a merged PR that names the issue, or a
  commit on the default branch that cites it. Marked done: the issue is closed or carries `done`. An issue with merged
  work and `qa-passed` earns `done`, which a report run applies itself after creating any missing status label
  (`report --no-write` for a read-only run). Merged work that is not marked is listed as a warning, as are merged PRs
  that name no issue. `qaLabels` / `doneLabels` rename the labels or, set to `[]`, opt a repo out.
- **Δ 1d / Δ 7d**: the same percentage recomputed as of 24 hours / 7 days ago (issues that existed then, done by
  then), in percentage points. A negative delta means new issues were filed faster than work landed.
- **PRs 1d / 7d / open**: merged pull requests attributed to the module by label, a mention matching
  `moduleMentionPattern` in the title or body (`M05`, `ESS-9.7`, `[E03]`), a referenced issue's module, or the
  conventional-commit scope mapped to a domain; otherwise M00.
- **Efficiency** (7 days): merged PRs per day, percentage points gained per merged PR, issues done per merged PR,
  and the merged-PR count against the previous 7 days.
- **Frontend**: share of the module's expected `[app, route prefix]` pairs that have at least one route file.
- **Backend**: share of the module's middleware domains with a route table (`routes.ts` = done, folder only = half).
- **Wired**: share of the module's api-client hook files that exist (half) and whose exported `useX` hooks are
  referenced from the module's apps (full). ▲ marks a column that improved over the last 7 days.
  These three columns need a probes file with code paths; today only homeporthealth/app has one, the other
  repositories report issue and PR progress.

## Repositories shipped in the default config

| Repository | Modules | Tracked issues | Done signal |
|---|---|---|---|
| `homeporthealth/app` | labels `m01..m28` + code probes from MODULE-MAP | `agent:build` | closed or merged PR |
| `Velocity-Org/ranch-rides` | labels `module:9.x`, names from the test-cases workbook and PRD | all but epics, decisions, questions | closed or merged PR |
| `ShorConsultingInc/tidyhaus` | from the code: `config/modules/tidyhaus.json` (M01..M14), PRs that name no issue placed by their files, without warnings (`unattributedIsExpected`) | all but epics, questions, duplicates | merged work and marked done |
| `Shor-Consulting-Inc/wyset` | epics `[E00]..[E14]` with their stories | `story` | closed, merged PR, or a `Ref #NN` commit on main |

## Adding a repository with code columns

Write `~/.claude/progress-tracker/modules/<name>.json` (or add one under `config/modules/` in this plugin) in the
shape of `config/modules/homeporthealth-app.json`: `paths` (where domains, routes and hooks live; `{app}` is
substituted) and one entry per module with `id`, `name`, `backend` (domain folder names), `frontend`
(`[app, routePrefix]` pairs) and `hooks` (hook file basenames). Then
`/progress-repos add owner/repo --modules <name>`.

## Files

```
.claude-plugin/plugin.json          manifest
commands/progress-report.md         /progress-report
commands/progress-repos.md          /progress-repos
scripts/progress.py                 generator (stdlib only); scripts/test_progress.py unit tests
config/repos.default.json           first-run config (the four repositories above, all enabled)
config/modules/homeporthealth-app.json  module probes derived from knowledge-base/architecture/MODULE-MAP.md
config/modules/ranch-rides.json     module names for 9.x (no code probes)
```

Tests: `python3 -m unittest discover -s tools/claude-plugins/progress-tracker/scripts -p 'test_*.py'` (12 cases).

## Script

```
python3 scripts/progress.py report [--only a/b] [--skip a/b] [--notes-dir DIR] [--out DIR]
python3 scripts/progress.py render report.json --notes-dir DIR     # re-render after the notes are checked
python3 scripts/test_progress.py
```

Notes are `<repo slug>.json` files in `--notes-dir` (`intro`, `modules`, `coming`, `checked`); a file without
`"checked": true` is never rendered.

## Changes

- 0.9.0: report rebuilt in the build-status layout; 7-day window at the same time of day; fact-checked plain-terms
  notes and Coming next; one page per repository (from the HomePort copy, commit 20b9fd83).
