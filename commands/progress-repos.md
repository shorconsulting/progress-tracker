---
description: List or toggle the repositories the daily progress report covers
argument-hint: "list | enable owner/repo | disable owner/repo | add owner/repo [--title T] [--modules NAME] | remove owner/repo"
allowed-tools: Bash(python3 *)
---

Manage the repository list the progress report reads (`~/.claude/progress-tracker/repos.json`). Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/progress.py" repos $ARGUMENTS
```

With no arguments, run `repos list`. Report the command's output as is. Each entry carries:

- `enabled`: whether the daily report includes it (`enable` / `disable` flip it; `--only` on the report overrides).
- `title`: the heading used in the report.
- `modules`: the name of a probes file (`<plugin>/config/modules/<name>.json`, or
  `~/.claude/progress-tracker/modules/<name>.json` for a private one) that maps module ids to backend domain
  folders, frontend route prefixes and api-client hooks. Without it the frontend / backend / wiring columns are
  omitted for that repository.
- `moduleSource`: `labels` (default; a label names the module) or `epics` (issues labelled `epicLabel`, default
  `epic`, are the modules and their bodies or their children's bodies link them).
- `moduleLabelPattern`: regex over issue labels whose first group is the module number (default `^m(\d{2})$`);
  `epicIdPattern` is the same over an epic's title; `moduleIdFormat` renders the id (`M{n:02d}`, `9.{n}`, `E{n:02d}`);
  `moduleMentionPattern` finds a module mention in a PR title or body.
- `trackedIssueLabels`: when set, only issues carrying one of these labels count (for example `agent:build`);
  `excludeIssueLabels` drops issues carrying any of these (epics, questions, duplicates).
- `localPath`: a checkout on this machine to read code columns from (`~/claude/_Projects/Wyset/wyset`); when it
  is absent or not a clone of the repo, the cwd or a cached blobless clone is used instead.
- `issueRefRule`: `keyword` (default: `#N` in a PR title, or after Ref / Closes / Fixes / Resolves / Implements /
  Issue(s) in its body) or `any` (every `#N`).
- `doneFromCommits` (+ `commitLookbackDays`): also count an issue done when a default-branch commit cites it.
- What *done* means, the same in every repository: **merged work AND marked done**. Merged work is a merged PR that names
  the issue ("Story #N" or `#N` in the title, `Ref` / `Closes` in the body) or a commit on the default branch that cites
  it (`doneFromCommits`, default true, within `commitLookbackDays`). Marked done is the issue being closed (not as
  not-planned or duplicate) or carrying the `done` label. Closed with no merged work, or merged with no mark, does not
  count; the report warns about merged work that is not marked.
- Status labels: `qa-passed` (QA signed off, ready to merge) and `done` (passed QA and merged). An issue with merged work
  and `qa-passed` earns `done`, which the report run applies itself. `qaLabels` / `doneLabels` rename them, or `[]` opts out.
- `moduleLabelFormat` (for example `m{n:02d}`, `module:9.{n}`): turns a module id into its label. Every report run gives
  each tracked issue that has no module one, inferred from a module mention in its title or body, then from the module of
  the merged PR(s) naming it, then from the module owning the files those PRs changed. With this setting a label-sourced
  repo gets the label written on the issue; an epic-sourced repo keeps the module for the report only. Issues that
  cannot be placed are listed in a warning and counted under M00. `--no-write` only reads.
- The meter (**% started**) is the share of tracked requirements (issues) with at least one PR opened or merged that names
  them; **% done** is shown beside it (merged work that is also closed or labelled done). `startedFromCommits` (default
  false) also counts a default-branch commit that cites the requirement, for work that lands without a PR.
- `manageLabels` (default true): each report run first creates any missing status label, then puts `done` on issues that
  have merged work and `qa-passed`, then builds the report from the result. `report --no-write` only reads.
