# progress-tracker

A Claude Code plugin that produces a daily build-progress report across your active repositories: overall %
complete with 1-day and 7-day deltas, PR throughput and efficiency, and a per-module table (fixed order M00,
M01, M02 ...) with % complete and frontend / backend / frontend-to-backend wiring status. Everything lands in one
artifact.

It stores nothing between runs: deltas are recomputed from issue `closed_at`, PR `merged_at` and git first-add
dates, so it gives the same answer from whichever Claude account runs it that day.

## Install (any account)

In a terminal Claude Code session:

```
/plugin install progress-tracker --marketplace homeporthealth/app
```

Answer `y` to add the marketplace and pick the user scope. The plugin needs `python3`, `git`, and GitHub read
access (the `gh` CLI, or `GH_TOKEN` / `GITHUB_TOKEN` in the environment).

## Commands

| Command | What it does |
|---|---|
| `/progress-report [--only a/b,c/d] [--skip a/b]` | Builds today's report and publishes it as the `Build progress` artifact |
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
- **% complete (module)**: done tracked issues / all tracked issues. *Done* = closed, or claimed by a merged
  pull request, because many repositories close issues only after QA. A PR claims an issue when `#N` is in its
  title, or in its body after Ref / Closes / Fixes / Resolves / Implements / Issue(s), lists included
  (`Issues #346, #347 and #348`); a bare `#N` elsewhere in the body ("#110 was read for context") does not
  count. `issueRefRule: "any"` per repository relaxes that to every `#N`. With `doneFromCommits: true`
  (wyset, where work lands on main with `Ref #NN` and stories stay open) a default-branch commit citing the
  issue counts too, within `commitLookbackDays`. `trackedIssueLabels` limits the base (`agent:build`, `story`);
  `excludeIssueLabels` drops epics, questions, duplicates and the like.
  Every repository uses the same status labels by default: `qa-passed` plus a merged PR that names the issue
  ("Story #N") counts done, `done` counts done outright, and a merge without `qa-passed` counts for nothing and is
  listed as a warning. A report run first creates the labels where missing and puts `done` on issues that earned it
  (`report --no-write` for a read-only run). `qaLabels` / `doneLabels` rename them or, set to `[]`, opt a repo out;
  `doneFromPRs: true` restores counting a bare merge.
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
| `Velocity-Org/tidyhaus` | epics (`Epic D..G`, plus unlettered `EPIC —` issues) | all but questions, duplicates | closed or merged PR |
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
