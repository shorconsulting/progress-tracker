#!/usr/bin/env python3
"""progress-tracker: daily build-progress report by module across repositories.

Standard library only. Data sources:
  * GitHub REST (issues, pull requests) through `gh api`, or urllib with GH_TOKEN / GITHUB_TOKEN.
  * A local git checkout of each repository (code presence per module and the date each path first appeared).

History needs no stored state: the 1-day and 7-day comparison points are recomputed from `created_at`,
`closed_at`, `merged_at` and git first-add dates, so any account can run the report on any day.

Usage:
  progress.py report [--only owner/repo,...] [--skip owner/repo,...] [--out DIR] [--no-clone] [--json]
  progress.py repos list|enable|disable|add|remove [owner/repo] [--title T] [--modules NAME]
  progress.py config-path
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("PROGRESS_TRACKER_HOME") or (Path.home() / ".claude" / "progress-tracker"))
CONFIG_PATH = HOME / "repos.json"
CACHE_DIR = HOME / "repos"
DEFAULT_CONFIG = PLUGIN_ROOT / "config" / "repos.default.json"
MODULES_DIRS = [HOME / "modules", PLUGIN_ROOT / "config" / "modules"]
DAY = dt.timedelta(days=1)
WEEK = dt.timedelta(days=7)
UTC = dt.timezone.utc


def log(msg: str) -> None:
    print(f"[progress-tracker] {msg}", file=sys.stderr)


def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


# ----------------------------------------------------------------------------- config

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        HOME.mkdir(parents=True, exist_ok=True)
        shutil.copy(DEFAULT_CONFIG, CONFIG_PATH)
        log(f"created {CONFIG_PATH} from the plugin default")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("repos", [])
    return cfg


def save_config(cfg: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def find_repo(cfg: dict, full: str) -> dict | None:
    return next((r for r in cfg["repos"] if r["repo"].lower() == full.lower()), None)


def cmd_repos(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.action == "list":
        for r in cfg["repos"]:
            flag = "on " if r.get("enabled", True) else "off"
            probes = r.get("modules") or "-"
            print(f"[{flag}] {r['repo']:<40} title={r.get('title') or r['repo']!r} modules={probes}")
        if not cfg["repos"]:
            print("(no repositories configured)")
        print(f"config: {CONFIG_PATH}")
        return 0
    if not args.repo:
        log("owner/repo is required")
        return 2
    full = args.repo.strip()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", full):
        log(f"not an owner/repo: {full}")
        return 2
    entry = find_repo(cfg, full)
    if args.action == "add":
        if entry is None:
            entry = {"repo": full, "enabled": True, "moduleSource": "labels", "moduleLabelPattern": r"^m(\d{2})$",
                     "trackedIssueLabels": [], "excludeIssueLabels": []}
            cfg["repos"].append(entry)
        entry["enabled"] = True
        if args.title:
            entry["title"] = args.title
        if args.modules:
            entry["modules"] = args.modules
        save_config(cfg)
        print(f"added/enabled {full}")
        return 0
    if entry is None:
        log(f"{full} is not configured; use `repos add {full}`")
        return 1
    if args.action in ("enable", "disable"):
        entry["enabled"] = args.action == "enable"
    elif args.action == "remove":
        cfg["repos"] = [r for r in cfg["repos"] if r is not entry]
    save_config(cfg)
    print(f"{args.action}d {full}")
    return 0


# ----------------------------------------------------------------------------- github

class GitHub:
    def __init__(self) -> None:
        self.gh = shutil.which("gh")
        self.token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self.mode = None

    def _gh(self, path: str) -> list | dict:
        p = subprocess.run([self.gh, "api", path], capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip()[:300])
        return json.loads(p.stdout)

    def _http(self, path: str) -> list | dict:
        req = urllib.request.Request("https://api.github.com/" + path,
                                     headers={"Accept": "application/vnd.github+json",
                                              "User-Agent": "progress-tracker",
                                              **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))

    def get(self, path: str) -> list | dict:
        errors = []
        if self.gh and self.mode in (None, "gh"):
            try:
                out = self._gh(path)
                self.mode = "gh"
                return out
            except RuntimeError as e:
                errors.append(f"gh: {e}")
        try:
            out = self._http(path)
            self.mode = "http"
            return out
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            errors.append(f"http: {e}")
        raise RuntimeError("GitHub API unavailable: " + "; ".join(errors))

    def paginate(self, path: str, per_page: int = 100) -> list:
        items: list = []
        page = 1
        sep = "&" if "?" in path else "?"
        while True:
            chunk = self.get(f"{path}{sep}per_page={per_page}&page={page}")
            if not isinstance(chunk, list):
                raise RuntimeError(f"unexpected response for {path}: {str(chunk)[:200]}")
            items.extend(chunk)
            if len(chunk) < per_page:
                return items
            page += 1


# ----------------------------------------------------------------------------- local checkout

def run_git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:300])
    return p.stdout


def remote_matches(path: Path, full: str) -> bool:
    try:
        url = run_git(path, "remote", "get-url", "origin").strip()
    except RuntimeError:
        return False
    return url.lower().rstrip("/").removesuffix(".git").endswith(full.lower())


def locate_checkout(full: str, allow_clone: bool, local_path: str | None = None) -> Path | None:
    """`localPath` from the config when it is a clone of `full`, else the cwd (or a parent) when it is one,
    else a partial clone kept in the cache."""
    if local_path:
        lp = Path(os.path.expanduser(local_path))
        if (lp / ".git").exists() and remote_matches(lp, full):
            return lp
        log(f"{full}: localPath {lp} is not a clone of it; falling back")
    cur = Path.cwd()
    for cand in [cur, *cur.parents]:
        if (cand / ".git").exists() and remote_matches(cand, full):
            return cand
    cache = CACHE_DIR / full.replace("/", "__")
    if (cache / ".git").exists():
        try:
            run_git(cache, "fetch", "--quiet", "--filter=blob:none", "origin")
            run_git(cache, "reset", "--quiet", "--hard", "origin/HEAD")
        except RuntimeError as e:
            log(f"{full}: fetch failed ({e}); using the cached checkout as is")
        return cache
    if not allow_clone:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{full}.git"
    try:
        subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", url, str(cache)],
                       check=True, capture_output=True, text=True)
        return cache
    except subprocess.CalledProcessError as e:
        log(f"{full}: clone failed: {e.stderr.strip()[:200]}")
        return None


def ensure_history(repo: Path) -> None:
    try:
        if run_git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
            log(f"{repo}: shallow clone; fetching full history (blobless)")
            run_git(repo, "fetch", "--quiet", "--unshallow", "--filter=blob:none", "origin")
    except RuntimeError as e:
        log(f"{repo}: could not deepen history ({e}); first-add dates may be too recent")


class Tree:
    """Read-only view of the default branch's tip (`origin/<branch>` after a fetch), whatever is checked out."""

    def __init__(self, repo: Path, branch: str = "main") -> None:
        self.repo = repo
        self.ref = f"origin/{branch}"
        try:
            run_git(repo, "fetch", "--quiet", "origin", branch)
        except RuntimeError as e:
            log(f"{repo}: fetch of {branch} failed ({e}); reading the last fetched tip")
        try:
            run_git(repo, "rev-parse", "--verify", "--quiet", self.ref)
        except RuntimeError:
            self.ref = "HEAD"
            log(f"{repo}: {branch} is not fetched; reading HEAD")
        self.files = run_git(repo, "ls-tree", "-r", "--name-only", self.ref).splitlines()
        self._first: dict[str, dt.datetime | None] = {}

    def exists(self, path: str) -> bool:
        return any(f == path or f.startswith(path.rstrip("/") + "/") for f in self.files)

    def glob_prefix(self, directory: str, prefix: str) -> list[str]:
        base = directory.rstrip("/") + "/"
        return [f for f in self.files if f.startswith(base + prefix)]

    def first_added(self, path: str) -> dt.datetime | None:
        if path not in self._first:
            try:
                out = run_git(self.repo, "log", "--diff-filter=A", "--format=%ct", "--reverse", self.ref, "--", path)
                lines = [ln for ln in out.splitlines() if ln.strip()]
                self._first[path] = dt.datetime.fromtimestamp(int(lines[0]), UTC) if lines else None
            except RuntimeError:
                self._first[path] = None
        return self._first[path]

    def grep_files(self, pattern: str, pathspec: str) -> list[str]:
        p = subprocess.run(["git", "-C", str(self.repo), "grep", "-l", "-E", pattern, self.ref, "--", pathspec],
                           capture_output=True, text=True)
        return [ln.split(":", 1)[1] for ln in p.stdout.splitlines() if ":" in ln] if p.returncode == 0 else []

    def read(self, path: str) -> str:
        try:
            return run_git(self.repo, "show", f"{self.ref}:{path}")
        except RuntimeError:
            return ""


# ----------------------------------------------------------------------------- metrics

def label_names(item: dict) -> list[str]:
    return [l["name"] if isinstance(l, dict) else str(l) for l in item.get("labels", [])]


FOUNDATION = "M00"


def sort_key(mid: str) -> tuple:
    """Fixed module order: foundation first, then numeric ids ascending (M01, 9.2, E07), then letters, then #n."""
    if mid == FOUNDATION:
        return (0, 0, "")
    if mid.startswith("#"):
        return (3, (int(mid[1:]),), "")
    nums = tuple(int(n) for n in re.findall(r"\d+", mid))
    if nums:
        return (1, nums, mid)
    letters = re.sub(r"[^A-Za-z]", "", mid)
    return (2, tuple(ord(c) for c in letters.upper()), mid)


class ModuleResolver:
    """Maps issues and pull requests to module ids for one repository.

    moduleSource "labels" (default): a label matching `moduleLabelPattern` (group 1 = number) names the module.
    moduleSource "epics": issues labelled `epicLabel` are the modules; an issue belongs to the epic whose body
    lists it (`#123`), or whose number its own body cites (`#15`, `owner/repo#15`). The epic's id comes from
    `epicIdPattern` over its title (group 1 = number or letter); an epic the pattern misses is `#<number>`.
    `moduleIdFormat` renders a numeric id (default `M{n:02d}`; `9.{n}`, `E{n:02d}` ...).
    """

    def __init__(self, entry: dict, issues: list[dict], full: str) -> None:
        self.source = entry.get("moduleSource") or "labels"
        self.label_pattern = re.compile(entry.get("moduleLabelPattern") or r"^m(\d{2})$", re.I)
        self.mention_pattern = re.compile(entry.get("moduleMentionPattern") or r"\bM(\d{2})\b")
        self.id_format = entry.get("moduleIdFormat") or "M{n:02d}"
        self.exclude = set(entry.get("excludeIssueLabels") or [])
        self.tracked = entry.get("trackedIssueLabels") or []
        self.names: dict[str, str] = {}
        self.by_issue: dict[int, list[str]] = {}
        self.epic_numbers: set[int] = set()
        if self.source == "epics":
            self._index_epics(entry, issues, full)
        for it in issues:
            self.by_issue.setdefault(it["number"], self.from_labels(label_names(it)))

    def format_id(self, raw: str) -> str:
        try:
            return self.id_format.format(n=int(raw) if raw.isdigit() else raw)
        except (ValueError, TypeError):
            return raw

    def from_labels(self, labels: list[str]) -> list[str]:
        ids = []
        for l in labels:
            m = self.label_pattern.match(l)
            if m:
                ids.append(self.format_id(m.group(1)))
        return sorted(set(ids), key=sort_key)

    def from_text(self, text: str) -> list[str]:
        return sorted({self.format_id(m.group(1)) for m in self.mention_pattern.finditer(text)}, key=sort_key)

    def _index_epics(self, entry: dict, issues: list[dict], full: str) -> None:
        epic_label = entry.get("epicLabel") or "epic"
        id_pattern = re.compile(entry.get("epicIdPattern") or r"^\[?E(\d+)\]?")
        epics = [i for i in issues if epic_label in label_names(i)]
        epic_id: dict[int, str] = {}
        for e in epics:
            m = id_pattern.search(e["title"])
            mid = self.format_id(m.group(1)) if m else f"#{e['number']}"
            epic_id[e["number"]] = mid
            title = e["title"][m.end():] if m else e["title"]
            self.names[mid] = html.unescape(re.sub(r"^[\s\-–—:·]+", "", title).strip()) or mid
            for n in re.findall(r"#(\d+)", e.get("body") or ""):
                self.by_issue.setdefault(int(n), [])
                if mid not in self.by_issue[int(n)]:
                    self.by_issue[int(n)].append(mid)
        self.epic_numbers = set(epic_id)
        cite = re.compile(r"(?:" + re.escape(full) + r")?#(\d+)\b", re.I)
        for it in issues:
            if it["number"] in epic_id:
                continue
            for n in cite.findall(it.get("body") or ""):
                if int(n) in epic_id:
                    self.by_issue.setdefault(it["number"], [])
                    if epic_id[int(n)] not in self.by_issue[it["number"]]:
                        self.by_issue[it["number"]].append(epic_id[int(n)])

    def is_tracked(self, issue: dict) -> bool:
        labels = label_names(issue)
        if self.source == "epics" and issue["number"] in self.epic_numbers:
            return False
        if self.exclude and any(l in self.exclude for l in labels):
            return False
        return not self.tracked or any(t in labels for t in self.tracked)

    def of_issue(self, issue: dict) -> list[str]:
        return self.by_issue.get(issue["number"]) or []


def issue_counts(issues: list[dict], cutoff: dt.datetime, resolver: ModuleResolver,
                 merged_for: dict[int, dt.datetime] | None = None, strict: bool = False) -> dict[str, dict]:
    """Per module: total, closed and done issues as they stood at `cutoff`.

    Default: an issue is *done* when it is closed, or when `merged_for` has it. With `strict`, `merged_for` alone
    decides, and the caller puts in it only issues with merged work that are also closed or labelled done.
    """
    merged_for = merged_for or {}
    out: dict[str, dict] = {}
    for it in issues:
        created = parse_ts(it["created_at"])
        if created is None or created > cutoff or not resolver.is_tracked(it):
            continue
        mods = resolver.of_issue(it) or [FOUNDATION]
        closed_at = parse_ts(it.get("closed_at"))
        # an issue closed as not planned or as a duplicate was not done; it leaves the base instead
        if it.get("state") == "closed" and it.get("state_reason") in ("not_planned", "duplicate"):
            continue
        closed = it.get("state") == "closed" and closed_at is not None and closed_at <= cutoff
        merged_at = merged_for.get(it["number"])
        done = (merged_at is not None and merged_at <= cutoff) if strict else (closed or (merged_at is not None and merged_at <= cutoff))
        for m in mods:
            row = out.setdefault(m, {"total": 0, "closed": 0, "done": 0})
            row["total"] += 1
            row["closed"] += int(closed)
            row["done"] += int(done)
    return out


def pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


REF_KEYWORDS = re.compile(
    r"\b(?:ref|refs|references|closes?|closed|closing|fix(?:es|ed)?|resolves?|resolved|implements?|builds?|issues?|stor(?:y|ies))\b"
    r"\s*:?\s*\(?\s*((?:#\d+\s*(?:[,;+/]|and|&)?\s*)+)", re.I)


def issue_refs(title: str, body: str, rule: str = "keyword") -> set[int]:
    """Issue numbers a pull request or commit claims to build.

    "keyword" (default): any `#N` in the title, plus `#N` lists in the body introduced by Ref / Closes / Fixes /
    Resolves / Implements / Issue(s) (`Ref #107`, `Closes #12, #13`, `Issues #346, #347 and #348`). A bare `#N`
    elsewhere in the body ("#110 was read for context") does not count.
    "any": every `#N` in title or body."""
    text_title = title or ""
    text_body = body or ""
    if rule == "any":
        return {int(n) for n in re.findall(r"#(\d+)", text_title + "\n" + text_body)}
    refs = {int(n) for n in re.findall(r"#(\d+)", text_title)}
    for m in REF_KEYWORDS.finditer(text_body):
        refs.update(int(n) for n in re.findall(r"#(\d+)", m.group(1)))
    return refs


def attribute_pr(pr: dict, resolver: ModuleResolver, domain_to_module: dict[str, str]) -> list[str]:
    """Module(s) of a pull request: its labels, a module mention in title/body, a referenced issue's module,
    or the conventional-commit scope mapped to a domain; else none (counted under M00)."""
    mods = resolver.from_labels(label_names(pr))
    text = (pr.get("title") or "") + "\n" + (pr.get("body") or "")
    if not mods:
        mods = resolver.from_text(text)
    if not mods:
        for n in issue_refs(pr.get("title") or "", pr.get("body") or "", "any"):
            mods.extend(resolver.by_issue.get(n, []))
        mods = sorted(set(mods), key=sort_key)
    if not mods:
        m = re.match(r"^\w+\(([^)]+)\)", pr.get("title") or "")
        if m:
            for scope in re.split(r"[,/ ]+", m.group(1)):
                if scope in domain_to_module:
                    mods.append(domain_to_module[scope])
            mods = sorted(set(mods), key=sort_key)
    return mods


def code_status(tree: Tree, probes: dict, module: dict, cutoff: dt.datetime | None,
                used_now: dict[str, bool] | None = None) -> dict:
    """frontend / backend / connection for one module, as of `cutoff` (None = now).

    Whether a hook is *used* from the apps is only observable on the current tree; a snapshot at `cutoff`
    takes `used_now` (the current answer per hook) for a hook file that already existed then, so a hook
    wired months ago never reads as "improved this week"."""
    paths = probes["paths"]
    used_now = used_now if used_now is not None else {}

    def present(path: str) -> bool:
        if not tree.exists(path):
            return False
        if cutoff is None:
            return True
        added = tree.first_added(path)
        return added is not None and added <= cutoff

    # backend: each domain scores 1 for the done file, 0.5 for the folder alone
    be_score, be_detail = 0.0, []
    for dom in module.get("backend", []):
        folder = f"{paths['backendDomains']}/{dom}"
        done = f"{folder}/{paths.get('backendDoneFile', 'routes.ts')}"
        if present(done):
            be_score += 1
            be_detail.append(f"{dom}: routes")
        elif present(folder):
            be_score += 0.5
            be_detail.append(f"{dom}: scaffold")
        else:
            be_detail.append(f"{dom}: missing")
    be_n = len(module.get("backend", []))

    # frontend: each [app, prefix] pair scores 1 when a route file exists under it
    fe_score, fe_detail = 0.0, []
    for app, prefix in module.get("frontend", []):
        directory = paths["frontendRoutes"].replace("{app}", app)
        files = [f for f in tree.glob_prefix(directory, prefix) if cutoff is None or present(f)]
        if files:
            fe_score += 1
            fe_detail.append(f"{app}/{prefix} ({len(files)})")
        else:
            fe_detail.append(f"{app}/{prefix}: missing")
    fe_n = len(module.get("frontend", []))

    # connection: hook file exists (0.5) and one of its exported useX hooks is used from the module's apps (1)
    cx_score, cx_detail = 0.0, []
    apps = sorted({app for app, _ in module.get("frontend", [])})
    for hook in module.get("hooks", []):
        hook_file = f"{paths['hooks']}/{hook}.ts"
        if not present(hook_file):
            cx_detail.append(f"{hook}: no hook")
            continue
        if cutoff is None:
            names = re.findall(r"export\s+(?:function|const)\s+(use\w+)", tree.read(hook_file))
            used = False
            if names:
                pattern = r"\b(" + "|".join(names) + r")\b"
                for app in apps:
                    if tree.grep_files(pattern, paths["frontendSrc"].replace("{app}", app)):
                        used = True
                        break
            used_now[hook] = used
        else:
            used = bool(used_now.get(hook, False))
        if used:
            cx_score += 1
            cx_detail.append(f"{hook}: wired")
        else:
            cx_score += 0.5
            cx_detail.append(f"{hook}: hook only")
    cx_n = len(module.get("hooks", []))

    def cell(score: float, n: int, detail: list[str]) -> dict:
        ratio = (score / n) if n else None
        state = "n/a" if n == 0 else "done" if ratio >= 0.999 else "partial" if ratio > 0 else "none"
        return {"ratio": None if ratio is None else round(ratio, 3), "state": state, "detail": detail}

    return {"frontend": cell(fe_score, fe_n, fe_detail), "backend": cell(be_score, be_n, be_detail),
            "connection": cell(cx_score, cx_n, cx_detail)}


def load_probes(name: str | None) -> dict | None:
    if not name:
        return None
    for d in MODULES_DIRS:
        p = d / f"{name}.json"
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    log(f"module probes '{name}' not found in {[str(d) for d in MODULES_DIRS]}")
    return None


def default_branch(entry: dict, gh: GitHub, full: str) -> str:
    if not entry.get("_default_branch"):
        entry["_default_branch"] = entry.get("defaultBranch") or gh.get(f"repos/{full}").get("default_branch") or "main"
    return entry["_default_branch"]


WRITE_LABELS = True  # --no-write turns the label pass into a read-only check

LABEL_DEFAULTS = {
    "qa-passed": ("0E8A16", "Passed QA and ready to merge"),
    "done": ("1D76DB", "Passed QA and merged to main"),
}


def ensure_labels(gh: GitHub, full: str, names: list[str], warnings: list[str]) -> None:
    """Create the status labels a repository's config relies on, so the report never reads a label that does not exist."""
    try:
        have = {l["name"] for l in gh.paginate(f"repos/{full}/labels")}
    except RuntimeError as e:
        warnings.append(f"could not list labels ({e})")
        return
    for name in names:
        if name in have:
            continue
        color, desc = LABEL_DEFAULTS.get(name, ("ededed", "Progress-tracker status label"))
        if not gh.gh:
            warnings.append(f"label '{name}' is missing and `gh` is not installed to create it")
            continue
        r = subprocess.run([gh.gh, "label", "create", name, "-R", full, "--color", color, "--description", desc],
                           capture_output=True, text=True)
        if r.returncode == 0:
            log(f"{full}: created label '{name}'")
        else:
            warnings.append(f"label '{name}' is missing and could not be created: {r.stderr.strip()[:120]}")


def apply_label(gh: GitHub, full: str, issue: dict, name: str, warnings: list[str]) -> bool:
    """Put `name` on the issue (and on the in-memory copy, so this run's report already reflects it)."""
    if not WRITE_LABELS or not gh.gh:
        return False
    r = subprocess.run([gh.gh, "issue", "edit", str(issue["number"]), "-R", full, "--add-label", name],
                       capture_output=True, text=True)
    if r.returncode != 0:
        warnings.append(f"could not label #{issue['number']} '{name}': {r.stderr.strip()[:100]}")
        return False
    issue.setdefault("labels", []).append({"name": name})
    return True


def label_applied_at(gh: GitHub, full: str, issue: dict, names: set[str]) -> dt.datetime | None:
    """When one of `names` was last applied to the issue (falls back to the issue's updated_at)."""
    events = gh.paginate(f"repos/{full}/issues/{issue['number']}/events")
    stamps = [parse_ts(e.get("created_at")) for e in events
              if e.get("event") == "labeled" and (e.get("label") or {}).get("name") in names]
    return max((x for x in stamps if x), default=None) or parse_ts(issue.get("updated_at"))


def _plurality(votes: list[str]) -> str | None:
    """The single most common value, or None on an empty list or a tie."""
    if not votes:
        return None
    top = collections.Counter(votes).most_common(2)
    return top[0][0] if len(top) == 1 or top[0][1] > top[1][1] else None


def assign_modules(entry: dict, gh: GitHub, full: str, issues: list[dict], resolver: "ModuleResolver",
                   prs_for: dict[int, list[dict]], domain_to_module: dict[str, str],
                   module_names: dict[str, str], warnings: list[str]) -> None:
    """Give every tracked issue without a module one, inferred in this order: a module mention in its title or body;
    the module of the merged pull request(s) that name it; the module that owns the files those pull requests changed.
    A label-sourced repository with `moduleLabelFormat` (for example `m{n:02d}`) also gets the label written on the
    issue; an epic-sourced one keeps the inferred module for the report only. What cannot be inferred is listed."""
    module_ids = set(module_names) | {m for v in resolver.by_issue.values() for m in v}
    by_number = {i["number"]: i for i in issues}
    todo = sorted(n for n, it in by_number.items() if resolver.is_tracked(it) and not resolver.by_issue.get(n))
    fmt = entry.get("moduleLabelFormat")
    writing = bool(fmt) and resolver.source == "labels" and WRITE_LABELS and entry.get("manageLabels", True)
    assigned: dict[int, str] = {}
    for n in todo:
        it = by_number[n]
        text_mods = [m for m in resolver.from_text((it.get("title") or "") + "\n" + (it.get("body") or "")) if m in module_ids]
        mid = text_mods[0] if len(text_mods) == 1 else None
        if mid is None:
            mid = _plurality([m for p in prs_for.get(n, []) for m in attribute_pr(p, resolver, domain_to_module) if m in module_ids])
        if mid is None and domain_to_module:
            votes = []
            for p in prs_for.get(n, [])[:3]:
                try:
                    files = gh.get(f"repos/{full}/pulls/{p['number']}/files?per_page=100")
                except RuntimeError:
                    continue
                for f in files if isinstance(files, list) else []:
                    votes += [domain_to_module[seg] for seg in f.get("filename", "").split("/") if seg in domain_to_module]
            mid = _plurality(votes)
        if mid is None:
            continue
        resolver.by_issue[n] = [mid]
        assigned[n] = mid
        if writing:
            digits = re.search(r"(\d+)$", mid)
            if digits:
                apply_label(gh, full, it, fmt.format(n=int(digits.group(1))), warnings)
    if assigned:
        log(f"{full}: assigned a module to {len(assigned)} issue(s)" + (" and labelled them" if writing else " (report only)"))
    left = [n for n in todo if n not in assigned]
    if left:
        warnings.append(f"{len(left)} tracked issue(s) have no module and none could be inferred (counted under M00): "
                        + ", ".join(f"#{n}" for n in left[:15]) + (" ..." if len(left) > 15 else ""))


def analyze_repo(entry: dict, gh: GitHub, now: dt.datetime, allow_clone: bool) -> dict:
    full = entry["repo"]
    probes = load_probes(entry.get("modules"))
    warnings: list[str] = []

    log(f"{full}: fetching issues and pull requests")
    raw = gh.paginate(f"repos/{full}/issues?state=all&sort=created&direction=asc")
    issues = [i for i in raw if "pull_request" not in i]
    prs = gh.paginate(f"repos/{full}/pulls?state=all&sort=created&direction=asc")
    log(f"{full}: {len(issues)} issues, {len(prs)} pull requests")

    # every repository gets the same status labels unless it sets either list (an empty list opts out)
    qa_labels = set(entry["qaLabels"] if "qaLabels" in entry else ["qa-passed"])
    done_labels = set(entry["doneLabels"] if "doneLabels" in entry else ["done"])
    if (qa_labels or done_labels) and entry.get("manageLabels", True) and WRITE_LABELS:
        ensure_labels(gh, full, sorted(qa_labels | done_labels), warnings)

    resolver = ModuleResolver(entry, issues, full)
    domain_to_module: dict[str, str] = {}
    module_names: dict[str, str] = dict(resolver.names)
    has_probes = bool(probes and probes.get("code", True) and probes.get("paths"))
    if probes:
        for m in probes["modules"]:
            module_names.setdefault(m["id"], html.unescape(m["name"]))
            for d in m.get("backend", []) + m.get("hooks", []):
                domain_to_module.setdefault(d.split("/")[-1], m["id"])

    # pull requests
    merged = [p for p in prs if p.get("merged_at")]
    ref_rule = entry.get("issueRefRule") or "keyword"
    pr_merge: dict[int, dt.datetime] = {}
    unlinked = 0
    prs_for: dict[int, list[dict]] = {}
    for p in merged:
        t = parse_ts(p["merged_at"])
        refs = [n for n in issue_refs(p.get("title") or "", p.get("body") or "", ref_rule) if n in resolver.by_issue]
        unlinked += not refs
        for n in refs:
            prs_for.setdefault(n, []).append(p)
            if t is not None and (n not in pr_merge or t < pr_merge[n]):
                pr_merge[n] = t
    merged_for: dict[int, dt.datetime] = {}
    merged_commit: dict[int, dt.datetime] = {}

    # optional third signal: a commit on the default branch that cites the issue (`Ref #68`)
    if entry.get("doneFromCommits", True):
        lookback = int(entry.get("commitLookbackDays") or 180)
        since = (now - dt.timedelta(days=lookback)).strftime("%Y-%m-%dT%H:%M:%SZ")
        branch = default_branch(entry, gh, full)
        log(f"{full}: scanning commits on {branch} since {since[:10]} for issue references")
        commits = gh.paginate(f"repos/{full}/commits?sha={branch}&since={since}")
        for c in commits:
            t = parse_ts(((c.get("commit") or {}).get("committer") or {}).get("date"))
            message = (c.get("commit") or {}).get("message") or ""
            subject, _, rest = message.partition("\n")
            for n in issue_refs(subject, rest, ref_rule):
                if n in resolver.by_issue and t is not None and (n not in merged_commit or t < merged_commit[n]):
                    merged_commit[n] = t

    # What "done" means: merged work AND marked done. Merged work is a merged PR that names the issue, or (with
    # doneFromCommits, the default) a commit on the default branch that cites it. Marked done is the issue being
    # closed or carrying a done label. `qaLabels` + merged work earns the done label, which the pass below applies.
    merged_work: dict[int, dt.datetime] = dict(pr_merge)
    for n, t in merged_commit.items():
        if n not in merged_work or t < merged_work[n]:
            merged_work[n] = t
    labelled: list[int] = []
    by_number = {i["number"]: i for i in issues}
    for n, it in by_number.items():
        if n not in resolver.by_issue or n not in merged_work:
            continue
        names = set(label_names(it))
        if qa_labels & names and done_labels and not done_labels & names and entry.get("manageLabels", True):
            if apply_label(gh, full, it, sorted(done_labels)[0], warnings):
                labelled.append(n)
                names = set(label_names(it))
        marked = []
        if it.get("state") == "closed" and it.get("state_reason") not in ("not_planned", "duplicate"):
            marked.append(parse_ts(it.get("closed_at")))
        if done_labels & names:
            marked.append(label_applied_at(gh, full, it, done_labels) if n not in labelled else now)
        marked = [x for x in marked if x]
        if marked:
            merged_for[n] = max(merged_work[n], min(marked))
    if labelled:
        log(f"{full}: labelled done: " + ", ".join(f"#{n}" for n in labelled))
    stuck = sorted(n for n in merged_work if n in by_number and n not in merged_for and resolver.is_tracked(by_number[n]))
    if stuck:
        warnings.append(f"{len(stuck)} issue(s) have merged work but are not closed or labelled done: "
                        + ", ".join(f"#{n}" for n in stuck[:15]) + (" ..." if len(stuck) > 15 else ""))
    if unlinked:
        warnings.append(f"{unlinked} merged PR(s) name no tracked issue (put 'Story #N' in the title or body so merges can be matched)")

    assign_modules(entry, gh, full, issues, resolver, prs_for, domain_to_module, module_names, warnings)

    points = {"now": now, "day": now - DAY, "week": now - WEEK, "two_weeks": now - 2 * WEEK}
    counts = {k: issue_counts(issues, t, resolver, merged_for, strict=True) for k, t in points.items()}

    open_prs = [p for p in prs if p.get("state") == "open"]
    pr_mods = {p["number"]: attribute_pr(p, resolver, domain_to_module) for p in prs}

    def merged_between(a: dt.datetime, b: dt.datetime, module: str | None = None) -> int:
        n = 0
        for p in merged:
            t = parse_ts(p["merged_at"])
            if t is None or not (a < t <= b):
                continue
            if module is None or module in pr_mods[p["number"]] or (module == FOUNDATION and not pr_mods[p["number"]]):
                n += 1
        return n

    # code columns
    tree = None
    if has_probes:
        checkout = locate_checkout(full, allow_clone, entry.get("localPath"))
        if checkout is None:
            warnings.append("no local checkout available; frontend/backend/connection columns skipped")
        else:
            ensure_history(checkout)
            tree = Tree(checkout, default_branch(entry, gh, full))

    all_ids = set(counts["now"]) | set(module_names)
    rows = []
    for mid in sorted(all_ids, key=sort_key):
        empty = {"total": 0, "closed": 0, "done": 0}
        c_now, c_day, c_week = (counts[k].get(mid, empty) for k in ("now", "day", "week"))
        p_now, p_day, p_week = (pct(c["done"], c["total"]) for c in (c_now, c_day, c_week))
        row = {
            "id": mid,
            "name": "Foundation / unattributed" if mid == FOUNDATION else module_names.get(mid, mid),
            "issues": {"total": c_now["total"], "closed": c_now["closed"], "done": c_now["done"],
                       "open": c_now["total"] - c_now["done"]},
            "pct": p_now,
            "delta_day": None if p_now is None or p_day is None else round(p_now - p_day, 1),
            "delta_week": None if p_now is None or p_week is None else round(p_now - p_week, 1),
            "closed_day": c_now["done"] - c_day["done"],
            "closed_week": c_now["done"] - c_week["done"],
            "prs_merged_day": merged_between(points["day"], now, mid),
            "prs_merged_week": merged_between(points["week"], now, mid),
            "prs_open": sum(1 for p in open_prs if mid in pr_mods[p["number"]]),
        }
        if tree and probes and mid != FOUNDATION:
            mod = next((m for m in probes["modules"] if m["id"] == mid), None)
            if mod:
                used_now: dict[str, bool] = {}
                row["code"] = code_status(tree, probes, mod, None, used_now)
                row["code_week"] = code_status(tree, probes, mod, points["week"], used_now)
        rows.append(row)

    def totals(c: dict[str, dict]) -> tuple[int, int]:
        return sum(v["total"] for v in c.values()), sum(v["done"] for v in c.values())

    t_now, t_day, t_week = totals(counts["now"]), totals(counts["day"]), totals(counts["week"])
    p_now, p_day, p_week = pct(t_now[1], t_now[0]), pct(t_day[1], t_day[0]), pct(t_week[1], t_week[0])
    m_day, m_week, m_prev_week = (merged_between(points["day"], now), merged_between(points["week"], now),
                                  merged_between(points["two_weeks"], points["week"]))
    closed_week = t_now[1] - t_week[1]
    closed_prev_week = t_week[1] - totals(counts["two_weeks"])[1]
    summary = {
        "issues_total": t_now[0], "issues_closed": t_now[1],
        "issues_closed_strict": sum(v["closed"] for v in counts["now"].values()), "pct": p_now,
        "delta_day": None if p_now is None or p_day is None else round(p_now - p_day, 1),
        "delta_week": None if p_now is None or p_week is None else round(p_now - p_week, 1),
        "closed_day": t_now[1] - t_day[1], "closed_week": closed_week, "closed_prev_week": closed_prev_week,
        "prs_merged_day": m_day, "prs_merged_week": m_week, "prs_merged_prev_week": m_prev_week,
        "prs_open": len(open_prs),
        "prs_per_day_week": round(m_week / 7, 2),
        "pct_points_per_pr_week": None if not m_week or p_now is None or p_week is None else round((p_now - p_week) / m_week, 2),
        "issues_per_pr_week": None if not m_week else round(closed_week / m_week, 2),
    }
    return {"repo": full, "title": entry.get("title") or full, "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "has_code_columns": tree is not None, "warnings": warnings, "summary": summary, "modules": rows}


# ----------------------------------------------------------------------------- rendering

def fmt_pct(v: float | None) -> str:
    return "–" if v is None else f"{v:.1f}%"


def fmt_delta(v: float | None, unit: str = " pt") -> str:
    if v is None:
        return "–"
    if abs(v) < 0.05:
        return "0" + unit
    return f"{v:+.1f}{unit}"


def fmt_int_delta(v: int | None) -> str:
    return "–" if v is None else (f"+{v}" if v > 0 else str(v))


def render_markdown(report: dict) -> str:
    lines = [f"# Build progress — {report['date']}", ""]
    for r in report["repos"]:
        s = r["summary"]
        lines += [f"## {r['title']} (`{r['repo']}`)", "",
                  f"- Overall: **{fmt_pct(s['pct'])}** ({s['issues_closed']}/{s['issues_total']} issues done, {s['issues_closed_strict']} closed), "
                  f"{fmt_delta(s['delta_day'])} vs yesterday, {fmt_delta(s['delta_week'])} vs last week",
                  f"- PRs merged: {s['prs_merged_day']} in the last 24h, {s['prs_merged_week']} in the last 7 days "
                  f"(prev week {s['prs_merged_prev_week']}); {s['prs_open']} open",
                  f"- Efficiency (7d): {s['prs_per_day_week']} PRs/day, "
                  f"{s['pct_points_per_pr_week'] if s['pct_points_per_pr_week'] is not None else '–'} pt/PR, "
                  f"{s['issues_per_pr_week'] if s['issues_per_pr_week'] is not None else '–'} issues/PR", ""]
        for w in r["warnings"]:
            lines.append(f"- Warning: {w}")
        hdr = "| Module | % | Δ1d | Δ7d | Done/total | PRs 7d | FE | BE | Wired |"
        lines += [hdr, "|---|---:|---:|---:|---:|---:|:--:|:--:|:--:|"]
        for m in r["modules"]:
            code = m.get("code") or {}
            cell = lambda k: {"done": "✓", "partial": "◐", "none": "✗", "n/a": "–"}.get(code.get(k, {}).get("state", "n/a"), "–")  # noqa: E731
            lines.append(f"| {m['id']} {m['name']} | {fmt_pct(m['pct'])} | {fmt_delta(m['delta_day'], '')} | "
                         f"{fmt_delta(m['delta_week'], '')} | {m['issues']['done']}/{m['issues']['total']} | "
                         f"{m['prs_merged_week']} | {cell('frontend')} | {cell('backend')} | {cell('connection')} |")
        lines.append("")
    return "\n".join(lines)


HTML_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Build progress</title>
<style>
:root{--bg:#f7f6f2;--card:#ffffff;--ink:#1d1f1b;--muted:#6b6f66;--line:#e2e0d8;--accent:#1f6f5f;--good:#2b7a4b;--warn:#b7791f;--bad:#b23a3a;--bar:#d9e8e2;--barfill:#1f6f5f;--chip:#eef0ea}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15170f;--card:#1e2117;--ink:#ecebe3;--muted:#9ea294;--line:#2d3127;--accent:#7cc4ad;--good:#7fcf9a;--warn:#e0b05a;--bad:#e07a7a;--bar:#2a3a33;--barfill:#7cc4ad;--chip:#262a1f}}
:root[data-theme="dark"]{--bg:#15170f;--card:#1e2117;--ink:#ecebe3;--muted:#9ea294;--line:#2d3127;--accent:#7cc4ad;--good:#7fcf9a;--warn:#e0b05a;--bad:#e07a7a;--bar:#2a3a33;--barfill:#7cc4ad;--chip:#262a1f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 12px}
.sub{color:var(--muted);margin:0 0 20px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:12px 0 20px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.tile .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.tile .v{font-size:24px;font-weight:600;margin-top:2px}.tile .d{font-size:13px;color:var(--muted)}.up{color:var(--good)}.down{color:var(--bad)}.flat{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:auto}table{border-collapse:collapse;width:100%;min-width:760px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;position:sticky;top:0;background:var(--card)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}tr:last-child td{border-bottom:0}
.bar{display:inline-block;width:90px;height:8px;background:var(--bar);border-radius:4px;vertical-align:middle;margin-right:8px;overflow:hidden}.bar i{display:block;height:100%;background:var(--barfill)}
.state{display:inline-block;min-width:64px;text-align:center;border-radius:999px;padding:1px 8px;font-size:12px;background:var(--chip)}.s-done{color:var(--good)}.s-partial{color:var(--warn)}.s-none{color:var(--bad)}.s-na{color:var(--muted)}
.warn{color:var(--warn);font-size:13px;margin:0 0 10px}details{margin-top:8px;color:var(--muted);font-size:13px}code{font-size:13px;background:var(--chip);padding:1px 5px;border-radius:4px}
.legend{color:var(--muted);font-size:13px;margin:10px 0 0}
</style></head><body><main>
"""


def cls_delta(v: float | None) -> str:
    if v is None or abs(v) < 0.05:
        return "flat"
    return "up" if v > 0 else "down"


def tile(k: str, v: str, d: str = "", dcls: str = "flat") -> str:
    return f'<div class="tile"><div class="k">{html.escape(k)}</div><div class="v">{html.escape(v)}</div><div class="d {dcls}">{html.escape(d)}</div></div>'


def state_chip(cell: dict | None, prev: dict | None = None) -> str:
    if not cell:
        return '<span class="state s-na">–</span>'
    st = cell["state"]
    label = {"done": "done", "partial": "partial", "none": "none", "n/a": "n/a"}[st]
    if cell.get("ratio") is not None and st == "partial":
        label = f"{round(cell['ratio'] * 100)}%"
    arrow = ""
    if prev and prev.get("ratio") is not None and cell.get("ratio") is not None and cell["ratio"] > prev["ratio"] + 1e-9:
        arrow = ' <span class="up" title="improved over the last 7 days">▲</span>'
    title = html.escape("; ".join(cell.get("detail", [])))
    return f'<span class="state s-{st.replace("/", "")}" title="{title}">{label}</span>{arrow}'


def render_html(report: dict) -> str:
    parts = [HTML_HEAD, f"<h1>Build progress</h1><p class=\"sub\">{html.escape(report['date'])} · "
             f"{len(report['repos'])} active {'repository' if len(report['repos']) == 1 else 'repositories'} · "
             "% complete = done tracked issues / all tracked issues per module (done = merged work that is also closed or labelled done); deltas are recomputed from GitHub timestamps.</p>"]
    if len(report["repos"]) > 1:
        parts.append("<h2>All active builds</h2><div class=\"card\"><table><thead><tr><th>Repository</th><th class=num>% complete</th>"
                     "<th class=num>Δ 1d</th><th class=num>Δ 7d</th><th class=num>Issues closed 7d</th><th class=num>PRs merged 7d</th>"
                     "<th class=num>PRs/day</th><th class=num>pt / PR</th></tr></thead><tbody>")
        for r in report["repos"]:
            s = r["summary"]
            parts.append(f"<tr><td>{html.escape(r['title'])}<br><code>{html.escape(r['repo'])}</code></td><td class=num>{fmt_pct(s['pct'])}</td>"
                         f"<td class='num {cls_delta(s['delta_day'])}'>{fmt_delta(s['delta_day'])}</td><td class='num {cls_delta(s['delta_week'])}'>{fmt_delta(s['delta_week'])}</td>"
                         f"<td class=num>{s['closed_week']}</td><td class=num>{s['prs_merged_week']}</td><td class=num>{s['prs_per_day_week']}</td>"
                         f"<td class=num>{s['pct_points_per_pr_week'] if s['pct_points_per_pr_week'] is not None else '–'}</td></tr>")
        parts.append("</tbody></table></div>")
    for r in report["repos"]:
        s = r["summary"]
        parts.append(f"<h2>{html.escape(r['title'])} <code>{html.escape(r['repo'])}</code></h2>")
        for w in r["warnings"]:
            parts.append(f"<p class=warn>{html.escape(w)}</p>")
        wk_trend = s["prs_merged_week"] - s["prs_merged_prev_week"]
        parts.append('<div class="tiles">' +
                     tile("Complete", fmt_pct(s["pct"]), f"{s['issues_closed']} of {s['issues_total']} issues done ({s['issues_closed_strict']} closed)") +
                     tile("Δ 1 day", fmt_delta(s["delta_day"]), f"{s['closed_day']} issues closed", cls_delta(s["delta_day"])) +
                     tile("Δ 7 days", fmt_delta(s["delta_week"]), f"{s['closed_week']} closed (prev week {s['closed_prev_week']})", cls_delta(s["delta_week"])) +
                     tile("PRs merged 7d", str(s["prs_merged_week"]), f"{fmt_int_delta(wk_trend)} vs previous week · {s['prs_merged_day']} in 24h", cls_delta(wk_trend)) +
                     tile("Throughput", f"{s['prs_per_day_week']} PR/day", f"{s['issues_per_pr_week'] if s['issues_per_pr_week'] is not None else '–'} issues per PR") +
                     tile("Yield", f"{s['pct_points_per_pr_week'] if s['pct_points_per_pr_week'] is not None else '–'} pt/PR",
                          f"{s['prs_open']} PRs open") + "</div>")
        code_cols = r["has_code_columns"]
        parts.append("<div class=\"card\"><table><thead><tr><th>Module</th><th>% complete</th><th class=num>Δ 1d</th><th class=num>Δ 7d</th>"
                     "<th class=num>Done / total</th><th class=num>Done 7d</th><th class=num>PRs 1d</th><th class=num>PRs 7d</th><th class=num>Open PRs</th>"
                     + ("<th>Frontend</th><th>Backend</th><th>FE → BE wired</th>" if code_cols else "") + "</tr></thead><tbody>")
        for m in r["modules"]:
            p = m["pct"] or 0
            parts.append(f"<tr><td><strong>{html.escape(m['id'])}</strong> {html.escape(m['name'])}</td>"
                         f"<td><span class=bar><i style=\"width:{p:.0f}%\"></i></span>{fmt_pct(m['pct'])}</td>"
                         f"<td class='num {cls_delta(m['delta_day'])}'>{fmt_delta(m['delta_day'], '')}</td>"
                         f"<td class='num {cls_delta(m['delta_week'])}'>{fmt_delta(m['delta_week'], '')}</td>"
                         f"<td class=num>{m['issues']['done']} / {m['issues']['total']}</td><td class=num>{m['closed_week']}</td>"
                         f"<td class=num>{m['prs_merged_day']}</td><td class=num>{m['prs_merged_week']}</td><td class=num>{m['prs_open']}</td>")
            if code_cols:
                c, cw = m.get("code"), m.get("code_week")
                for k in ("frontend", "backend", "connection"):
                    parts.append(f"<td>{state_chip(c.get(k) if c else None, cw.get(k) if cw else None)}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table></div>")
        if code_cols:
            parts.append("<p class=legend>Frontend: route files present per expected app/route. Backend: middleware domain with a route table (scaffold alone counts half). "
                         "Wired: an api-client hook exists (half) and is used from the module's apps (full). ▲ marks a column that improved in the last 7 days. Hover a chip for detail.</p>")
    parts.append(f"<details><summary>About this report</summary>Generated {html.escape(report['generated_at'])} by the progress-tracker plugin. "
                 "Module order is fixed (M00 = foundation and unattributed work, then module ids ascending). Deltas compare against the same metric recomputed at now−24h and now−7d from issue and PR timestamps and git first-add dates; "
                 "they do not depend on earlier runs.</details></main></body></html>")
    return "".join(parts)


# ----------------------------------------------------------------------------- report command

def cmd_report(args: argparse.Namespace) -> int:
    cfg = load_config()
    only = {s.strip().lower() for s in (args.only or "").split(",") if s.strip()}
    skip = {s.strip().lower() for s in (args.skip or "").split(",") if s.strip()}
    repos = [r for r in cfg["repos"]
             if (r.get("enabled", True) or r["repo"].lower() in only)
             and (not only or r["repo"].lower() in only) and r["repo"].lower() not in skip]
    if not repos:
        log("no active repositories; use `repos add owner/repo` or `repos enable owner/repo`")
        return 1
    global WRITE_LABELS
    WRITE_LABELS = not getattr(args, "no_write", False)
    now = dt.datetime.now(UTC).replace(microsecond=0)
    gh = GitHub()
    results = []
    failures = []
    for entry in repos:
        try:
            results.append(analyze_repo(entry, gh, now, allow_clone=not args.no_clone))
        except Exception as e:  # keep the other repos' report
            log(f"{entry['repo']}: FAILED: {e}")
            failures.append({"repo": entry["repo"], "error": str(e)})
    report = {"date": now.strftime("%Y-%m-%d"), "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "repos": results, "failures": failures}
    out = Path(args.out) if args.out else HOME / "out" / report["date"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "report.html").write_text(render_html(report), encoding="utf-8")
    (out / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    latest = HOME / "out" / "latest"
    if not args.out:
        latest.mkdir(parents=True, exist_ok=True)
        for name in ("report.json", "report.html", "summary.md"):
            shutil.copy(out / name, latest / name)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print((out / "summary.md").read_text(encoding="utf-8"))
        print(f"\nhtml: {out / 'report.html'}\njson: {out / 'report.json'}")
        for f in failures:
            print(f"FAILED {f['repo']}: {f['error']}")
    return 1 if failures and not results else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="progress.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report", help="build the report for every enabled repository")
    rp.add_argument("--only", help="comma-separated owner/repo list to include (overrides enabled flags)")
    rp.add_argument("--skip", help="comma-separated owner/repo list to exclude")
    rp.add_argument("--out", help="output directory (default ~/.claude/progress-tracker/out/<date>)")
    rp.add_argument("--no-clone", action="store_true", help="never clone; skip code columns when no checkout is at hand")
    rp.add_argument("--no-write", action="store_true", help="read-only: never create labels or label issues")
    rp.add_argument("--json", action="store_true", help="print the JSON report instead of the markdown summary")
    rp.set_defaults(fn=cmd_report)
    rr = sub.add_parser("repos", help="list or toggle tracked repositories")
    rr.add_argument("action", choices=["list", "enable", "disable", "add", "remove"])
    rr.add_argument("repo", nargs="?")
    rr.add_argument("--title")
    rr.add_argument("--modules", help="name of a module probes file in config/modules/ or ~/.claude/progress-tracker/modules/")
    rr.set_defaults(fn=cmd_repos)
    cp = sub.add_parser("config-path", help="print the config file path")
    cp.set_defaults(fn=lambda a: (print(CONFIG_PATH), 0)[1])
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
