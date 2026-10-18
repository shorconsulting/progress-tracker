# Installing progress-tracker on another Claude account

Two ways; pick one per account.

## A. From the GitHub marketplace (no files needed)

In a terminal Claude Code session on that account:

    /plugin install progress-tracker --marketplace homeporthealth/app

Answer `y` to add the marketplace, choose the user scope. The account needs read access to homeporthealth/app.

## B. From this zip (any account, no repository access needed)

1. Unzip somewhere permanent, e.g. `~/claude-plugins/progress-tracker`.
2. In a terminal:

       claude plugin marketplace add ~/claude-plugins/progress-tracker
       claude plugin install progress-tracker@progress-tracker-local

   (or inside a session: `/plugin marketplace add ~/claude-plugins/progress-tracker`, then
   `/plugin install progress-tracker@progress-tracker-local`.)

   A plugin installed from a folder is read from that folder, so a later unzip over it is picked up by
   `/reload-plugins`.

## Then, on each account

- Check `/progress-repos list`; switch repos on or off with `/progress-repos enable|disable owner/repo`.
- Run `/progress-report` once by hand to confirm GitHub access (it needs `gh` or `GH_TOKEN`).
- Create a daily Routine with the prompt `/progress-report` (fresh session per firing, in an environment with
  GitHub access). Add `--only owner/a,owner/b` to the prompt to pin the repositories regardless of that
  account's config.

See README.md for what the numbers mean and how to add a repository with code columns.
