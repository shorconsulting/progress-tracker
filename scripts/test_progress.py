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


page_chip = progress.page.chip
WINDOW = {"days": 7, "tz": "America/Los_Angeles", "start": "2026-10-05T19:00:00Z", "end": "2026-10-12T19:00:00Z"}


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
        self.assertNotIn("▲", page_chip(now["connection"], week["connection"]))



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
        report["window"] = WINDOW
        rows[0]["week"] = {"started": 0, "done": 0, "total": 2}
        rows[1]["week"] = {"started": 1, "done": 0, "total": 4}
        html_out = progress.page.render_html(report)
        self.assertLess(html_out.index("M00"), html_out.index("M05"))
        self.assertIn("Orders &lt;b&gt;", html_out)
        self.assertIn("M05&lt;i&gt;", html_out)
        self.assertNotIn("M05<i>", html_out)
        rows[1]["id"] = "M05"
        self.assertIn("\u25b2", html_out)  # frontend improved over the week
        self.assertIn("w1", html_out)
        md = progress.render_markdown(report)
        self.assertIn("| M05 Orders <b> | 50.0% | +25.0 | +50.0 | 3/4 | 2/4 | 0 | 3 | ◐ | ✓ | ✗ |", md)


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

    def test_default_settings_reach_an_existing_install_without_overwriting(self):
        home = Path(tempfile.mkdtemp(prefix="pt-cfg-"))
        default = home / "default.json"
        default.write_text('{"repos": [{"repo": "acme/widgets", "title": "Default", "foundationForUnlinkedPrs": true}]}',
                           encoding="utf-8")
        (home / "repos.json").write_text('{"repos": [{"repo": "Acme/Widgets", "title": "Mine", "enabled": false}]}',
                                         encoding="utf-8")
        progress.HOME, progress.CONFIG_PATH, saved = home, home / "repos.json", progress.DEFAULT_CONFIG
        progress.DEFAULT_CONFIG = default
        try:
            r = progress.find_repo(progress.load_config(), "acme/widgets")
        finally:
            progress.DEFAULT_CONFIG = saved
        self.assertTrue(r["foundationForUnlinkedPrs"])
        self.assertEqual((r["title"], r["enabled"]), ("Mine", False))


class RepoPages(unittest.TestCase):
    def test_each_repository_gets_a_page_titled_after_it(self):
        self.assertEqual(progress.repo_page_title({"title": "Homeport Health platform"}), "Homeport Health platform build progress")
        report = {"date": "2026-10-12", "generated_at": "2026-10-12T12:00:00Z", "failures": [], "repos": [],
                  "window": {"days": 7, "tz": "UTC", "start": "2026-10-05T12:00:00Z", "end": "2026-10-12T12:00:00Z"}}
        html_page = progress.page.render_html(report, "R & D build progress")
        self.assertIn("<title>R &amp; D build progress</title>", html_page)
        self.assertIn("<title>Build progress</title>", progress.page.render_html(report))


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


class DoneDateTest(unittest.TestCase):
    def test_a_late_done_label_does_not_make_old_work_finish_today(self):
        lab = lambda *n: [{"name": x} for x in n]  # noqa: E731
        issue = lambda n, l: {"number": n, "title": f"T{n}", "body": "", "state": "open", "labels": lab(*l),  # noqa: E731
                              "created_at": "2026-09-01T00:00:00Z", "closed_at": None, "updated_at": "2026-10-18T00:00:00Z"}
        epic = issue(1, ["epic"]); epic["title"] = "[E01] Core"; epic["body"] = "#2"
        issues = [epic, issue(2, ["story", "done"])]
        prs = [{"number": 9, "state": "closed", "merged_at": "2026-10-01T00:00:00Z", "created_at": "2026-09-30T00:00:00Z",
                "title": "Story #2", "body": "", "labels": []}]

        class Fake:
            gh = None
            def get(self, path):
                return {"default_branch": "main"}
            def paginate(self, path, per_page=100):
                if path.endswith("/events"):  # the label went on long after the merge
                    return [{"event": "labeled", "created_at": "2026-10-18T00:00:00Z", "label": {"name": "done"}}]
                return prs if "/pulls" in path else [] if "/commits" in path else lab("done") if "/labels" in path else issues

        entry = {"repo": "o/r", "moduleSource": "epics", "epicLabel": "epic", "epicIdPattern": r"^\[E(\d+)\]",
                 "moduleIdFormat": "E{n:02d}", "trackedIssueLabels": ["story"], "manageLabels": False}
        r = progress.analyze_repo(entry, Fake(), NOW, False)
        self.assertEqual(r["summary"]["issues_done"], 1)
        self.assertEqual(r["summary"]["delta_week"], 0.0)  # done a fortnight ago on the merge date, not this week

    def test_closing_today_counts_as_done_today_by_close_date(self):
        closed_at = (NOW - progress.DAY / 2).strftime("%Y-%m-%dT%H:%M:%SZ")
        issue = lambda n, state, closed: {"number": n, "title": f"T{n}", "body": "", "state": state, "state_reason": None,  # noqa: E731
                                          "labels": [{"name": "story"}], "created_at": "2026-09-01T00:00:00Z",
                                          "closed_at": closed, "updated_at": "2026-10-18T00:00:00Z"}
        issues = [issue(2, "closed", closed_at), issue(3, "closed", "2026-10-02T00:00:00Z"), issue(4, "open", None)]
        prs = [{"number": 9, "state": "closed", "merged_at": "2026-10-01T00:00:00Z", "created_at": "2026-09-30T00:00:00Z",
                "title": "Story #2", "body": "", "labels": []},
               {"number": 10, "state": "closed", "merged_at": "2026-10-01T00:00:00Z", "created_at": "2026-09-30T00:00:00Z",
                "title": "Story #3", "body": "", "labels": []}]

        class Fake:
            gh = None
            def get(self, path):
                return {"default_branch": "main"}
            def paginate(self, path, per_page=100):
                return prs if "/pulls" in path else [] if "/commits" in path or "/events" in path or "/labels" in path else issues

        entry = {"repo": "o/r", "moduleSource": "labels", "moduleLabelPattern": r"^(?!)(\d+)$", "moduleIdFormat": "M{n:02d}",
                 "trackedIssueLabels": ["story"], "manageLabels": False}
        s = progress.analyze_repo(entry, Fake(), NOW, False)["summary"]
        self.assertEqual(s["delta_day"], 0.0)  # started weeks ago: the started meter does not move
        self.assertEqual(s["done_day"], 1)  # but #2 was closed today, so it is done today by close date
        self.assertEqual(s["done_delta_day"], 33.4)  # 66.7 - 33.3, each rounded first like the other deltas


class PathRulesTest(unittest.TestCase):
    PROBES = {"modules": [
        {"id": "M01", "name": "Files", "match": ["app/core/src/organize/*", "app/ui/src/screens/pff-*"], "keywords": ["organize"]},
        {"id": "M02", "name": "Buying", "match": ["app/ui/src/screens/gift-*", "backend/src/modules/commerce/*", "backend/*"], "keywords": ["checkout"]}]}

    def test_longest_literal_glob_wins_and_a_pr_goes_where_most_files_are(self):
        r = progress.PathRules(self.PROBES)
        self.assertEqual(r.for_path("backend/src/modules/commerce/x.ts"), "M02")
        self.assertEqual(r.for_path("app/ui/src/screens/pff-results/a.tsx"), "M01")
        self.assertIsNone(r.for_path("docs/readme.md"))
        self.assertEqual(r.for_files(["app/core/src/organize/a.ts", "app/core/src/organize/b.ts", "backend/x.ts"]), "M01")
        self.assertIsNone(r.for_files(["app/core/src/organize/a.ts", "backend/x.ts"]))  # a tie places nothing

    def test_screen_names_and_keywords_place_an_issue_by_its_text(self):
        r = progress.PathRules(self.PROBES)
        self.assertEqual(r.for_text("gift-redeem: redemption fills the cart"), "M02")
        self.assertEqual(r.for_text("Wire the checkout to organize"), None)  # one keyword each: no winner
        self.assertEqual(r.for_text("Production organize flow"), "M01")


class InferLinksTest(unittest.TestCase):
    def test_links_by_leading_requirement_id_then_by_topic_and_never_to_an_epic(self):
        issue = lambda n, title, body="", labels=(): {"number": n, "title": title, "body": body, "labels": [{"name": x} for x in labels]}  # noqa: E731
        issues = [issue(1, "TH-230 — Production organize: folder picker"), issue(2, "Cloud sync of the journal across devices", "journal sync across devices over the cloud"),
                  issue(3, "EPIC — Cloud sync", "journal sync cloud devices epic", ["epic"]), issue(4, "Billing page lists invoices", "invoices billing")]
        pr = lambda n, title, body="": {"number": n, "title": title, "body": body}  # noqa: E731
        links = progress.infer_pr_links([pr(10, "Folder picker", "implements TH-230 on mac"), pr(11, "The journal now syncs across devices through the cloud"),
                                         pr(12, "Rename a variable")], issues)
        self.assertEqual(links[10][0::2], (1, "id"))
        self.assertEqual((links[11][0], links[11][2]), (2, "topic"))
        self.assertNotIn(12, links)
        self.assertNotEqual(links[11][0], 3)


class FoundationForUnlinkedPrsTest(unittest.TestCase):
    def test_prs_naming_no_tracked_issue_count_under_m00_merged_and_open(self):
        lab = lambda *n: [{"name": x} for x in n]  # noqa: E731
        issues = [{"number": 1, "title": "T1", "body": "", "state": "open", "labels": lab("m01", "story"),
                   "created_at": "2026-09-01T00:00:00Z", "closed_at": None, "updated_at": "2026-10-10T00:00:00Z"}]
        pr = lambda n, t, state="closed": {"number": n, "state": state, "title": t, "body": "", "labels": lab("m01"),  # noqa: E731
                                           "created_at": "2026-10-11T00:00:00Z", "merged_at": "2026-10-12T00:00:00Z" if state == "closed" else None}
        prs = [pr(20, "Story #1"), pr(21, "Tidy the build"), pr(22, "Rename things", "open")]

        class Fake:
            gh = None
            def get(self, path):
                return [] if "/files" in path else {"default_branch": "main"}
            def paginate(self, path, per_page=100):
                return prs if "/pulls" in path else [] if "/commits" in path else lab("m01") if "/labels" in path else issues

        entry = {"repo": "o/r", "moduleSource": "labels", "moduleLabelPattern": r"^m(\d{2})$", "moduleIdFormat": "M{n:02d}",
                 "trackedIssueLabels": ["story"], "qaLabels": [], "doneLabels": []}
        rows = lambda r: {m["id"]: (m["prs_merged_day"], m["prs_open"]) for m in r["modules"]}  # noqa: E731
        self.assertEqual(rows(progress.analyze_repo(dict(entry), Fake(), NOW, False)), {"M01": (2, 1)})
        r = progress.analyze_repo(dict(entry, foundationForUnlinkedPrs=True), Fake(), NOW, False)
        self.assertEqual(rows(r), {"M00": (1, 1), "M01": (1, 0)})
        self.assertFalse(any("name no tracked issue" in w for w in r["warnings"]))  # handled: M00 holds them, no warning

    def test_unattributed_is_expected_keeps_placement_and_drops_the_warning(self):
        lab = lambda *n: [{"name": x} for x in n]  # noqa: E731
        issues = [{"number": 1, "title": "T1", "body": "", "state": "open", "labels": lab("m01", "story"),
                   "created_at": "2026-09-01T00:00:00Z", "closed_at": None, "updated_at": "2026-10-10T00:00:00Z"},
                  {"number": 2, "title": "T2", "body": "", "state": "open", "labels": lab("story"),
                   "created_at": "2026-09-01T00:00:00Z", "closed_at": None, "updated_at": "2026-10-10T00:00:00Z"}]
        prs = [{"number": 21, "state": "closed", "title": "Tidy the build", "body": "", "labels": lab("m01"),
                "created_at": "2026-10-11T00:00:00Z", "merged_at": "2026-10-12T00:00:00Z"}]

        class Fake:
            gh = None
            def get(self, path):
                return [] if "/files" in path else {"default_branch": "main"}
            def paginate(self, path, per_page=100):
                return prs if "/pulls" in path else [] if "/commits" in path else lab("m01") if "/labels" in path else issues

        entry = {"repo": "o/r", "moduleSource": "labels", "moduleLabelPattern": r"^m(\d{2})$", "moduleIdFormat": "M{n:02d}",
                 "trackedIssueLabels": ["story"], "qaLabels": [], "doneLabels": []}
        loud = progress.analyze_repo(dict(entry), Fake(), NOW, False)
        self.assertTrue(any("name no tracked issue" in w for w in loud["warnings"]))
        self.assertTrue(any("no module" in w for w in loud["warnings"]))
        r = progress.analyze_repo(dict(entry, unattributedIsExpected=True), Fake(), NOW, False)
        self.assertEqual({m["id"]: m["prs_merged_day"] for m in r["modules"]}, {"M00": 0, "M01": 1})  # the PR stays in its module
        self.assertEqual(r["warnings"], [])


class IssueAliasesTest(unittest.TestCase):
    def test_item_mentions_name_item_issues_and_closed_issues_count_as_done(self):
        lab = lambda *n: [{"name": x} for x in n]  # noqa: E731
        iss = lambda n, t, state="open": {"number": n, "title": t, "body": "", "state": state, "labels": lab("story"),  # noqa: E731
                                          "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-10-10T00:00:00Z",
                                          "closed_at": "2026-09-20T00:00:00Z" if state == "closed" else None, "state_reason": None}
        issues = [iss(100, "AT-24: Write the guard"), iss(101, "AT-25: Doorways"), iss(102, "AT-26: Handles"),
                  iss(7, "Old requirement nobody named", "closed")]
        pr = lambda n, t, body="": {"number": n, "state": "closed", "title": t, "body": body, "labels": [],  # noqa: E731
                                    "created_at": "2026-10-11T00:00:00Z", "merged_at": "2026-10-12T00:00:00Z"}
        prs = [pr(20, "The guard (AT-24)"), pr(21, "Doorways", "agent-team step 1, AT-25 to AT-26"), pr(22, "Worklist item 24, not AT-3")]

        class Fake:
            gh = None
            def get(self, path):
                return [] if "/files" in path else {"default_branch": "main"}
            def paginate(self, path, per_page=100):
                return prs if "/pulls" in path else [] if "/commits" in path or "/labels" in path else issues

        entry = {"repo": "o/r", "moduleSource": "labels", "moduleLabelPattern": r"^(?!)(\d+)$", "moduleIdFormat": "M{n:02d}",
                 "trackedIssueLabels": ["story"], "qaLabels": [], "doneLabels": [], "unattributedIsExpected": True}
        plain = progress.analyze_repo(dict(entry), Fake(), NOW, False)["summary"]
        self.assertEqual((plain["issues_started"], plain["issues_done"]), (0, 0))
        on = dict(entry, closedCountsAsDone=True,
                  issueAliases={"mention": r"\bAT-(\d+)(?:\s*(?:-|to)\s*(?:AT-)?(\d+))?", "title": r"^AT-(\d+)\b"})
        r = progress.analyze_repo(on, Fake(), NOW, False)
        self.assertEqual((r["summary"]["issues_started"], r["summary"]["issues_done"]), (4, 1))  # 24, 25, 26 started; #7 done
        self.assertTrue(r["closed_counts_as_done"])


class BackdateAndBacklinkTest(unittest.TestCase):
    def test_planned_moves_created_back_and_built_by_names_the_issue(self):
        issues = [{"number": 9, "created_at": "2026-11-05T00:00:00Z", "body": "x\nPlanned: 2026-09-22\nBuilt by: #911, #914 and #3\n"},
                  {"number": 8, "created_at": "2026-09-01T00:00:00Z", "body": "Planned: 2026-10-01"}]
        prs = [{"number": 911, "body": "the guard"}, {"number": 914, "body": None}, {"number": 5, "body": "other"}]
        progress.backdate_and_backlink(issues, prs)
        self.assertEqual(issues[0]["created_at"], "2026-09-22T00:00:00Z")
        self.assertEqual(issues[1]["created_at"], "2026-09-01T00:00:00Z")  # never moved later
        self.assertEqual([progress.issue_refs("", p["body"] or "") for p in prs], [{9}, {9}, set()])


class WindowTest(unittest.TestCase):
    def test_seven_days_same_time_of_day(self):
        now = dt.datetime(2026, 10, 15, 18, 45, tzinfo=UTC)  # Thursday 11:45 PDT
        start = progress.window_start(now)
        self.assertEqual(start, dt.datetime(2026, 10, 8, 18, 45, tzinfo=UTC))
        self.assertEqual(now - start, dt.timedelta(days=7))

    def test_window_keeps_clock_time_across_dst_change(self):
        now = dt.datetime(2026, 11, 2, 19, 0, tzinfo=UTC)  # Mon 11:00 PST, after the 1 Nov change
        start = progress.window_start(now)
        local = start.astimezone(progress.ZoneInfo("America/Los_Angeles"))
        self.assertEqual((local.month, local.day, local.hour), (10, 26, 11))  # 11:00 PDT, not Monday-to-today

    def test_two_windows_do_not_overlap(self):
        now = dt.datetime(2026, 10, 15, 18, 45, tzinfo=UTC)
        w1 = progress.window_start(now)
        self.assertEqual(progress.window_start(w1), dt.datetime(2026, 10, 1, 18, 45, tzinfo=UTC))


def mod(i, n, started, done, w_started, w_done, name=None):
    return {"id": i, "name": name or f"Mod {i}", "issues": {"total": n, "closed": done, "done": done, "started": started, "open": n - done},
            "week": {"started": w_started, "done": w_done, "total": n}, "pct": 0, "delta_day": 1.0, "delta_week": 2.0, "prs_merged_week": 2}


def report_for(mods, has_code=False):
    summary = {"pct": 50.0, "done_pct": 25.0, "delta_day": 1.0, "delta_week": 2.0, "done_delta_day": 0.0, "prs_merged_week": 7,
               "prs_merged_prev_week": 4, "prs_merged_day": 1, "prs_open": 0, "prs_per_day_week": 1.0, "pct_points_per_pr_week": 0.5}
    return {"date": "2026-10-12", "generated_at": "2026-10-12T19:00:00Z", "window": WINDOW, "failures": [],
            "repos": [{"repo": "o/r", "title": "Ranch Rides", "has_code_columns": has_code, "warnings": [], "summary": summary, "modules": mods}]}


class LayoutTest(unittest.TestCase):
    MODS = [mod("9.1", 10, 9, 9, 5, 4, "Accounts"), mod("9.2", 10, 4, 1, 2, 0, "Orders"), mod("9.3", 10, 0, 0, 0, 0, "Reports")]

    def page(self, notes=None):
        return progress.page.render_html(report_for(self.MODS), "Ranch Rides build progress", notes)

    def test_sections_in_order(self):
        h = self.page()
        marks = ["class=\"kpis\"", "Module by module", "Moved most since", "Furthest behind", "Biggest untouched gaps", "<h2>Near done", "How to read this"]
        pos = [h.index(m) for m in marks]
        self.assertEqual(pos, sorted(pos))
        self.assertLess(h.index("context"), h.index("class=\"kpis\""))  # plain-language paragraph before the tiles

    def test_warnings_between_modules_and_notes_and_tight(self):
        rep = report_for(self.MODS)
        rep["repos"][0]["warnings"] = ["3 issue(s) labelled x", "second"]
        notes = {"o-r": {"checked": True, "intro": "i", "modules": {"Accounts": [{"text": "t", "evidence": []}]}}}
        h = progress.page.render_html(rep, "T", notes)
        self.assertLess(h.index("Module by module"), h.index("labeled x"))
        self.assertLess(h.index("labeled x"), h.index("in plain terms"))
        self.assertNotIn("labelled", h)
        self.assertEqual(h.count('class="note warn warns"'), 1)  # one tight block, not a page-level gap per line

    def test_change_colors_green_above_zero_red_otherwise(self):
        P = progress.page
        self.assertIn('class="up">+5%', P.chg(10, 15))
        self.assertIn('class="down">0%', P.chg(10, 10))
        self.assertIn('class="down">\u22122%', P.chg(10, 8))
        self.assertIn('class="up">+1%', P.dpt(0.6))
        self.assertIn('class="down">0%', P.dpt(0.0))
        self.assertIn('class="down">\u22121%', P.dpt(-1.0))
        self.assertNotIn('up', P.dpt(0.4))  # rounds to 0, so red

    def test_row_has_was_bar_above_now_bar_and_state(self):
        h = self.page()
        self.assertLess(h.index("pb was"), h.index("pb now"))
        self.assertIn("Near done</span>", h)   # Accounts: 90% done
        self.assertIn("Early</span>", h)       # Reports: nothing started
        self.assertIn("In progress</span>", h) # Orders
        self.assertIn("+50%", h)

    def test_still_to_be_built_wording(self):
        h = self.page()
        self.assertIn("still to be built (", h)
        self.assertNotIn("not found", h)

    def test_light_and_dark_and_no_em_dash(self):
        h = self.page({"o-r": {"checked": True, "intro": "Accounts moved \u2014 a lot.", "modules": {"Accounts": [{"text": "Sign in \u2014 now works", "evidence": ["PR #1"]}]},
                               "coming": [{"module": "Orders", "text": "Checkout", "evidence": []}]}})
        self.assertIn("prefers-color-scheme:dark", h)
        self.assertIn('data-theme="dark"', h)
        self.assertNotIn("\u2014", h)

    def test_unchecked_notes_never_render(self):
        h = self.page({"o-r": {"checked": False, "intro": "UNCHECKED CLAIM", "modules": {"Accounts": [{"text": "X", "evidence": []}]}}})
        self.assertNotIn("UNCHECKED CLAIM", h)
        self.assertNotIn("in plain terms", h)

    def test_checked_notes_and_coming_render(self):
        h = self.page({"o-r": {"checked": True, "intro": "Riders can sign in.", "modules": {"Accounts": [{"text": "Sign in works.", "evidence": ["PR #1"]}]},
                               "coming": [{"module": "Orders", "text": "Checkout.", "evidence": []}]}})
        self.assertIn("What changed in the last 7 days, in plain terms", h)
        self.assertIn("Riders can sign in.", h)
        self.assertIn("Coming next", h)

    def test_window_label_in_page(self):
        h = self.page()
        self.assertIn("last 7 days at the same time of day", h)
        self.assertIn("never Monday to today", h)

    def test_per_repo_pages_written(self):
        with tempfile.TemporaryDirectory() as d:
            pages = progress.write_pages(report_for(self.MODS), Path(d), {})
            self.assertEqual(pages[0][0], "Ranch Rides build progress")
            self.assertTrue(pages[0][2].is_file())
            self.assertIn("<title>Ranch Rides build progress</title>", pages[0][2].read_text())


if __name__ == "__main__":
    unittest.main()
