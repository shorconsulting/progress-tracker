"""Unit tests for progress.py on synthetic data (no network, no git)."""
import datetime as dt
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("PROGRESS_TRACKER_HOME", tempfile.mkdtemp(prefix="pt-test-"))
import progress  # noqa: E402

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
PAT = re.compile(r"^m(\d{2})$")


def ts(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def issue(n, labels, created, closed=None):
    return {"number": n, "labels": [{"name": l} for l in labels], "created_at": ts(created),
            "state": "closed" if closed else "open", "closed_at": ts(closed) if closed else None}


class IssueCounts(unittest.TestCase):
    def test_done_counts_closed_and_merged_references_as_of_cutoff(self):
        issues = [
            issue(1, ["agent:build", "m05"], NOW - dt.timedelta(days=10), closed=NOW - dt.timedelta(days=2)),
            issue(2, ["agent:build", "m05"], NOW - dt.timedelta(days=10)),            # merged PR references it
            issue(3, ["agent:build", "m05"], NOW - dt.timedelta(hours=3)),            # created after the day cutoff
            issue(4, ["agent:build"], NOW - dt.timedelta(days=10)),                   # foundation (no module label)
            issue(5, ["question"], NOW - dt.timedelta(days=10)),                      # not tracked
        ]
        merged_for = {2: NOW - dt.timedelta(hours=20)}
        res = progress.ModuleResolver({"trackedIssueLabels": ["agent:build"]}, issues, "o/r")
        now = progress.issue_counts(issues, NOW, res, merged_for)
        self.assertEqual(now["M05"], {"total": 3, "closed": 1, "done": 2, "started": 2})
        self.assertEqual(now[progress.FOUNDATION], {"total": 1, "closed": 0, "done": 0, "started": 0})
        day = progress.issue_counts(issues, NOW - progress.DAY, res, merged_for)
        self.assertEqual(day["M05"], {"total": 2, "closed": 1, "done": 1, "started": 1})  # issue 3 not yet filed, PR not yet merged
        week = progress.issue_counts(issues, NOW - progress.WEEK, res, merged_for)
        self.assertEqual(week["M05"], {"total": 2, "closed": 0, "done": 0, "started": 0})

    def test_not_planned_and_duplicate_closures_leave_the_base(self):
        issues = [issue(1, ["m05"], NOW - dt.timedelta(days=5), closed=NOW - dt.timedelta(days=1)),
                  {**issue(2, ["m05"], NOW - dt.timedelta(days=5), closed=NOW - dt.timedelta(days=1)), "state_reason": "not_planned"},
                  {**issue(3, ["m05"], NOW - dt.timedelta(days=5), closed=NOW - dt.timedelta(days=1)), "state_reason": "duplicate"},
                  {**issue(4, ["m05"], NOW - dt.timedelta(days=5), closed=NOW - dt.timedelta(days=1)), "state_reason": "completed"}]
        c = progress.issue_counts(issues, NOW, progress.ModuleResolver({}, issues, "o/r"))
        self.assertEqual(c["M05"], {"total": 2, "closed": 2, "done": 2, "started": 2})

    def test_multi_module_issue_counts_for_each(self):
        issues = [issue(1, ["m05", "m07"], NOW - dt.timedelta(days=1), closed=NOW - dt.timedelta(hours=1))]
        c = progress.issue_counts(issues, NOW, progress.ModuleResolver({}, issues, "o/r"))
        self.assertEqual(c["M05"]["done"], 1)
        self.assertEqual(c["M07"]["done"], 1)


class AttributePr(unittest.TestCase):
    def test_attribution_order(self):
        issues = [issue(120, ["m09"], NOW - dt.timedelta(days=3))]
        res = progress.ModuleResolver({}, issues, "o/r")
        d2m = {"orders": "M05", "payments": "M05"}
        self.assertEqual(progress.attribute_pr({"labels": [{"name": "m02"}], "title": "x"}, res, d2m), ["M02"])
        self.assertEqual(progress.attribute_pr({"labels": [], "title": "feat: M13 forms builder"}, res, d2m), ["M13"])
        self.assertEqual(progress.attribute_pr({"labels": [], "title": "fix: thing (#120)"}, res, d2m), ["M09"])
        self.assertEqual(progress.attribute_pr({"labels": [], "title": "feat(orders,payments): totals"}, res, d2m), ["M05"])
        self.assertEqual(progress.attribute_pr({"labels": [], "title": "chore: bump deps"}, res, d2m), [])


class Resolvers(unittest.TestCase):
    def test_label_source_with_custom_id_format(self):
        issues = [issue(1, ["module:9.12"], NOW - dt.timedelta(days=3)), issue(2, ["module:9.5", "epic"], NOW - dt.timedelta(days=3))]
        res = progress.ModuleResolver({"moduleLabelPattern": r"^module:9\.(\d+)$", "moduleIdFormat": "9.{n}",
                                       "moduleMentionPattern": r"\b(?:ESS-)?9\.(\d{1,2})\b", "excludeIssueLabels": ["epic"]},
                                      issues, "o/r")
        self.assertEqual(res.of_issue(issues[0]), ["9.12"])
        self.assertFalse(res.is_tracked(issues[1]))
        self.assertEqual(res.from_text("ESS-9.7.3 pricing and 9.40 attribution"), ["9.7", "9.40"])

    def test_epic_source_links_both_directions(self):
        epic_a = {**issue(10, ["epic"], NOW - dt.timedelta(days=9)), "title": "[E03] Tasks and inbox", "body": "Stories:\n- [ ] S30 -- #31\n"}
        epic_b = {**issue(11, ["epic"], NOW - dt.timedelta(days=9)), "title": "Epic D — Organize flow", "body": "### Child issues\n- [ ] #41\n"}
        epic_c = {**issue(12, ["epic"], NOW - dt.timedelta(days=9)), "title": "EPIC — Licence service", "body": ""}
        s31 = {**issue(31, ["story"], NOW - dt.timedelta(days=8)), "body": "Epic: Tasks (o/r#10). Story S30."}
        s32 = {**issue(32, ["story"], NOW - dt.timedelta(days=8)), "body": "Epic: Tasks (o/r#10)."}  # cites the epic; epic does not list it
        i41 = {**issue(41, [], NOW - dt.timedelta(days=8)), "body": "TH-230 folder picker"}
        loose = {**issue(50, ["story"], NOW - dt.timedelta(days=8)), "body": "no epic"}
        issues = [epic_a, epic_b, epic_c, s31, s32, i41, loose]
        res = progress.ModuleResolver({"moduleSource": "epics", "epicIdPattern": r"^(?:\[E|Epic )([A-Z0-9]+)\]?", "moduleIdFormat": "E{n:02d}",
                                       "trackedIssueLabels": []}, issues, "o/r")
        self.assertEqual(res.names, {"E03": "Tasks and inbox", "D": "Organize flow", "#12": "EPIC — Licence service"})
        self.assertEqual(res.of_issue(s31), ["E03"])
        self.assertEqual(res.of_issue(s32), ["E03"])
        self.assertEqual(res.of_issue(i41), ["D"])
        self.assertEqual(res.of_issue(loose), [])
        self.assertFalse(res.is_tracked(epic_a))  # epics are modules, not work items
        counts = progress.issue_counts(issues, NOW, res)
        self.assertEqual(counts["E03"]["total"], 2)
        self.assertEqual(counts[progress.FOUNDATION]["total"], 1)

    def test_sort_key_is_numeric_and_stable(self):
        ids = ["9.10", "Epic E", "M05", "9.2", "#84", "M00", "E07", "Epic D", "9.1"]
        self.assertEqual(sorted(ids, key=progress.sort_key), ["M00", "M05", "E07", "9.1", "9.2", "9.10", "Epic D", "Epic E", "#84"])


class IssueRefs(unittest.TestCase):
    def test_keyword_rule_takes_lists_and_ignores_context_citations(self):
        body = ("Ref #107  The first M15 slice. #106 (functional) and #110 (security) were read for context.\n"
                "Fourteen requirement ids, each on an open Essentials issue (#193, #196, #209) and named.\n"
                "Issues #346, #347 and #348; Closes #12,#13. See #544 for the lost PRs.")
        self.assertEqual(progress.issue_refs("fix: thing (#120)", body),
                         {120, 107, 193, 196, 209, 346, 347, 348, 12, 13})
        self.assertIn(110, progress.issue_refs("x", body, "any"))
        self.assertEqual(progress.issue_refs("Ref #16", "", "keyword"), {16})

class CodeColumns(unittest.TestCase):
    def test_week_snapshot_carries_current_hook_usage(self):
        """A hook wired long ago must not read as improved this week (reviewer finding F1)."""
        class FakeTree:
            files = ["packages/api-client/src/hooks/orders.ts", "apps/admin/src/routes/orders/index.tsx"]
            def exists(self, path): return any(f == path or f.startswith(path.rstrip("/") + "/") for f in self.files)
            def glob_prefix(self, d, p): return [f for f in self.files if f.startswith(d.rstrip("/") + "/" + p)]
            def first_added(self, path): return NOW - dt.timedelta(days=60)
            def grep_files(self, pattern, pathspec): return ["apps/admin/src/routes/orders/index.tsx"]
            def read(self, path): return "export function useOrderList() {}"
        probes = {"paths": {"backendDomains": "services/middleware/src/domains", "frontendRoutes": "apps/{app}/src/routes",
                            "frontendSrc": "apps/{app}/src", "hooks": "packages/api-client/src/hooks"}}
        mod = {"id": "M05", "name": "Orders", "backend": [], "frontend": [["admin", "orders"]], "hooks": ["orders"]}
        used_now = {}
        now = progress.code_status(FakeTree(), probes, mod, None, used_now)
        week = progress.code_status(FakeTree(), probes, mod, NOW - progress.WEEK, used_now)
        self.assertEqual(now["connection"]["state"], "done")
        self.assertEqual(week["connection"]["state"], "done")
        self.assertNotIn("▲", progress.state_chip(now["connection"], week["connection"]))



class Formatting(unittest.TestCase):
    def test_pct_and_deltas(self):
        self.assertIsNone(progress.pct(0, 0))
        self.assertEqual(progress.pct(1, 3), 33.3)
        self.assertEqual(progress.fmt_delta(2.5), "+2.5 pt")
        self.assertEqual(progress.fmt_delta(-0.3, ""), "-0.3")
        self.assertEqual(progress.fmt_delta(0.0), "0 pt")
        self.assertEqual(progress.fmt_delta(None), "–")

    def test_render_keeps_module_order_and_escapes(self):
        rows = [{"id": "M00", "name": "Foundation", "issues": {"total": 2, "closed": 1, "done": 1, "started": 1, "open": 1}, "pct": 50.0,
                 "delta_day": 0.0, "delta_week": 10.0, "closed_day": 0, "closed_week": 1, "prs_merged_day": 0,
                 "prs_merged_week": 2, "prs_open": 1},
                {"id": "M05", "name": "Orders <b>", "issues": {"total": 4, "closed": 0, "done": 2, "started": 3, "open": 2}, "pct": 50.0,
                 "delta_day": 25.0, "delta_week": 50.0, "closed_day": 1, "closed_week": 2, "prs_merged_day": 1,
                 "prs_merged_week": 3, "prs_open": 0,
                 "code": {"frontend": {"ratio": 0.5, "state": "partial", "detail": ["admin/orders (2)"]},
                          "backend": {"ratio": 1.0, "state": "done", "detail": ["orders: routes"]},
                          "connection": {"ratio": 0.0, "state": "none", "detail": ["orders: no hook"]}},
                 "code_week": {"frontend": {"ratio": 0.0, "state": "none", "detail": []},
                               "backend": {"ratio": 1.0, "state": "done", "detail": []},
                               "connection": {"ratio": 0.0, "state": "none", "detail": []}}}]
        summary = {"issues_total": 6, "issues_started": 4, "issues_done": 3, "done_pct": 50.0, "issues_closed": 3, "issues_closed_strict": 1, "pct": 50.0, "delta_day": 12.5,
                   "delta_week": 30.0, "closed_day": 1, "closed_week": 3, "closed_prev_week": 0, "prs_merged_day": 1,
                   "prs_merged_week": 5, "prs_merged_prev_week": 2, "prs_open": 1, "prs_per_day_week": 0.71,
                   "pct_points_per_pr_week": 6.0, "issues_per_pr_week": 0.6}
        report = {"date": "2026-10-12", "generated_at": "2026-10-12T12:00:00Z", "failures": [],
                  "repos": [{"repo": "o/r", "title": "R", "generated_at": "x", "has_code_columns": True, "warnings": ["w1"],
                             "summary": summary, "modules": rows}]}
        rows[1]["id"] = "M05<i>"
        html_out = progress.render_html(report)
        self.assertLess(html_out.index("M00"), html_out.index("M05"))
        self.assertIn("Orders &lt;b&gt;", html_out)
        self.assertIn("M05&lt;i&gt;", html_out)
        self.assertNotIn("M05<i>", html_out)
        rows[1]["id"] = "M05"
        self.assertIn("▲", html_out)  # frontend improved over the week
        self.assertIn("w1", html_out)
        md = progress.render_markdown(report)
        self.assertIn("| M05 Orders <b> | 50.0% | +25.0 | +50.0 | 3/4 | 2/4 | 3 | ◐ | ✓ | ✗ |", md)


class ReposConfig(unittest.TestCase):
    def test_add_toggle_remove(self):
        home = Path(tempfile.mkdtemp(prefix="pt-cfg-"))
        progress.HOME, progress.CONFIG_PATH = home, home / "repos.json"
        progress.main(["repos", "add", "acme/widgets", "--title", "Widgets"])
        cfg = progress.load_config()
        self.assertTrue(progress.find_repo(cfg, "acme/widgets")["enabled"])
        progress.main(["repos", "disable", "acme/widgets"])
        self.assertFalse(progress.find_repo(progress.load_config(), "acme/widgets")["enabled"])
        progress.main(["repos", "remove", "acme/widgets"])
        self.assertIsNone(progress.find_repo(progress.load_config(), "acme/widgets"))
        self.assertEqual(progress.main(["repos", "enable", "not/there"]), 1)
        self.assertEqual(progress.main(["repos", "add", "bad name"]), 2)


class AssignModulesTest(unittest.TestCase):
    def test_infers_from_text_pr_and_files_and_lists_the_rest(self):
        lab = lambda *n: [{"name": x} for x in n]  # noqa: E731
        mk = lambda n, l, title="": {"number": n, "title": title or f"T{n}", "body": "", "state": "open", "labels": lab(*l),  # noqa: E731
                                     "created_at": "2026-09-01T00:00:00Z", "closed_at": None, "updated_at": "2026-10-10T00:00:00Z"}
        issues = [mk(1, ["m01", "story"]), mk(2, ["m02", "story"]), mk(3, ["story"], "Adds M02 export"), mk(4, ["story"]),
                  mk(5, ["story"]), mk(6, ["story"])]
        pr = lambda n, t, l=(): {"number": n, "state": "closed", "merged_at": "2026-10-12T00:00:00Z", "title": t, "body": "", "labels": lab(*l)}  # noqa: E731
        prs = [pr(20, "Ship #4", ["m01"]), pr(21, "Ship #5")]

        class Fake:
            gh = None
            def get(self, path):
                return [{"filename": "apps/api/src/billing/x.ts"}] if "/files" in path else {"default_branch": "main"}
            def paginate(self, path, per_page=100):
                return prs if "/pulls" in path else [] if "/commits" in path else lab("m01", "m02") if "/labels" in path else issues

        entry = {"repo": "o/r", "moduleSource": "labels", "moduleLabelPattern": r"^m(\d{2})$", "moduleIdFormat": "M{n:02d}",
                 "trackedIssueLabels": ["story"], "qaLabels": [], "doneLabels": []}
        old = progress.load_probes
        progress.load_probes = lambda name: {"modules": [{"id": "M01", "name": "Core", "backend": ["apps/api/src/core"]},
                                                         {"id": "M02", "name": "Billing", "backend": ["apps/api/src/billing"]}], "paths": {}}
        try:
            r = progress.analyze_repo(entry, Fake(), NOW, False)
        finally:
            progress.load_probes = old
        totals = {m["id"]: m["issues"]["total"] for m in r["modules"]}
        self.assertEqual(totals, {"M00": 1, "M01": 2, "M02": 3})
        self.assertTrue(any("#6" in w and "no module" in w for w in r["warnings"]))


if __name__ == "__main__":
    unittest.main()
