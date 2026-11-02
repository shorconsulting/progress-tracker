---
description: Build-progress report by module across your active repositories (last 7 days at the same time of day), published as one artifact per repository plus an overview
argument-hint: "[--only owner/repo,...] [--skip owner/repo,...]"
allowed-tools: Bash(python3 *), Bash(python *), Bash(git *), Bash(gh *), Read, Write, Agent, Artifact
---

Produce the build-progress report and publish it as one artifact per repository (each with its own stable URL, so it
can be shared with that repository's partner alone), plus one overview artifact when more than one repository ran.
The page follows the build-status layout: a plain-language summary first, then tiles with the 7-day change, the
module table with the bar from 7 days ago above today's bar, Moved most / Furthest behind, Biggest untouched gaps /
Near done, "What changed in the last 7 days, in plain terms", Coming next, How to read this.

The numbers come from a script. The plain-terms words come from one agent and are checked by a different agent before
anything is published; never publish bullets that were not checked. Run unattended except where a step says otherwise.

The window is the last 7 days at the same time of day (David S: "build status = last 7 days at the same time of day"),
never Monday to today. The 1-day change is a secondary figure.

## Steps

1. Run the generator (it reads the repository list from `~/.claude/progress-tracker/repos.json`, creating it from the
   plugin default on first use; `PROGRESS_TRACKER_HOME` overrides that directory):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/progress.py" report $ARGUMENTS
   ```

   Where `python3` is not installed (Windows), use `python` for this and step 4. It prints a markdown summary, the
   paths of the overview `report.html` and `report.json` (under `~/.claude/progress-tracker/out/<date>/`, with a copy
   in `out/latest/`), and one line per repository:
   `repo-html: <owner/repo> | <artifact title> | <path>`. It needs GitHub read access through `gh` or a `GH_TOKEN` /
   `GITHUB_TOKEN`, and a local checkout of each repository for the frontend / backend / wiring columns (the current
   directory when it is a clone, else a blobless clone under `~/.claude/progress-tracker/repos/`). If a repository
   fails, the others still report; the failure is listed at the end.

   Without notes, each page opens with a short factual paragraph generated from the numbers and has no plain-terms
   section. Steps 2 to 4 add the words.

2. **Draft the notes (one agent, Sonnet).** For each repository in `report.json` (`repos[].merged_prs` lists the pull
   requests merged after `repos[].window_start`, with their modules), spawn an agent to write
   `<notes>/<slug>.json`, where `<notes>` is `<out dir>/notes` and `<slug>` is the repository lowercased with
   non-alphanumerics replaced by `-` (`Velocity-Org/ranch-rides` becomes `velocity-org-ranch-rides`). Give it the
   report.json path, the repository, and these rules:
   - Write what a person using the product will notice, in plain words, grouped by module, from the user's side. No
     issue numbers or code names in the text. American spelling; no em dashes.
   - Only pull requests merged after `window_start` count. Read each PR's title, body and diff before describing it.
   - Never say something is built, live or released unless a merged PR shows it; never say live unless the deployed
     commit contains it. A rule with no screen says so. Staff-only behavior is labeled staff-only. A setting that is
     stored but not acted on says so.
   - Every bullet carries `evidence`: the PR numbers it rests on.
   - Shape: `{"intro": "...", "modules": {"<id> <module name>": [{"text": "...", "evidence": ["PR #91"]}]}, "coming": [{"module": "<id> <name>", "text": "...", "evidence": []}]}`
     where `intro` is a plain-language paragraph of the latest progress (2 to 4 sentences).

3. **Fact-check (a different agent, Opus).** Spawn a fresh agent that did not write the draft. It checks every
   sentence of the intro, bullets and coming items against the merged PRs, their commit messages and diffs: merged in
   the window, does no more than the sentence says (screen or only a rule; client-facing or staff-only; stored or
   acted on), not called live unless deployed, counts right, style rules met. It overwrites the notes file with the
   corrected version, adds `"checked": true`, and writes `<notes>/<slug>.factcheck.md` listing every sentence as
   correct / rewritten / removed with the reason. Read that report yourself. If it rewrote more than a third of the
   sentences, run the check once more on its output. (The first Wyset page had 17 over-claims at this step.)

4. Re-render with the checked notes:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/progress.py" render <out dir>/report.json --notes-dir <out dir>/notes
   ```

   A notes file without `"checked": true` is ignored by the renderer.

5. Publish each `repo-html:` page as its own artifact, using the artifact title printed on that line. Call the
   Artifact tool with `action: "list"` once and, for each page, look for an artifact with exactly that title; when one
   exists, publish to its `url` so the link stays the same day to day (read it first if this session has not
   published it). Otherwise, when an artifact is titled `Build progress` (the name versions before 0.8.6 used) and
   reading it shows it holds only this repository, publish to its `url`, which keeps the old link. Otherwise publish
   a new one with `icon: "chart"` and the description
   `Build progress by module for <owner/repo>, last 7 days`. Never put two repositories on one artifact URL, and never
   reuse the overview artifact for a repository page. When more than one repository ran, also publish the overview
   `report.html` under the title `Build progress`, to an artifact holding more than one repository or a new one. With
   one repository, skip the overview. For a test run, use a new title (for example `<Title> build progress (test)`) so no team's existing page is overwritten.

6. Reply with the markdown summary the script printed followed by one artifact link per repository, then the overview
   link if published, plus one line on anything the fact-check removed. Keep the module order as printed. If the
   script reported a warning or a failed repository, say so in one line each.

## Notes

- Started % = requirements with at least one PR opened or merged / all tracked requirements. Done = merged work that
  is also closed or labeled done. "Still to be built" = no PR names the requirement yet. Both bars use today's
  requirement list, so a bar moves only when the work moves.
- State pill: Near done at 85% or more done; Early under 30% started; otherwise In progress.
- Frontend / Backend / Wired columns come from the module probes file named in the repository's config entry
  (`config/modules/<name>.json`); a repository without one reports issue and PR progress only.
- `timezone` in `repos.json` (default `America/Los_Angeles`) sets which clock "the same time of day" means.
- To change which repositories are reported, use `/progress-repos` or pass `--only` / `--skip`.
