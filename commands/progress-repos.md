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
- Status labels, the same in every repository by default: `qa-passed` (QA signed off, ready to merge) and `done`
  (passed QA and merged). An issue counts done when it is closed, carries `done`, or carries `qa-passed` and a merged PR
  names it ("Story #N" in the title or body, or any `issueRefRule` form). A merge without `qa-passed` never counts
  and is listed as a warning. Set `qaLabels` / `doneLabels` to change the names, or to `[]` to opt a repository out.
- `doneFromPRs` (default false): also count an issue done on a merged PR alone. Leave it off wherever QA matters.
- `manageLabels` (default true): each report run first creates any missing status label in the repository, then puts
  `done` on every issue that has `qa-passed` and a merged PR, then builds the report from the result. `report --no-write`
  skips the writing and only reads.
