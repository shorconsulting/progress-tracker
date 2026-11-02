"""Report page for progress-tracker, in the build-status layout (summary first, then was/now bars per module).

Standard library only. `render_html(report, title, notes)` returns a full page; `notes` maps a repo slug to the
fact-checked week file (see skills in commands/progress-report.md). Notes that are not marked `"checked": true`
are never rendered.
"""
from __future__ import annotations

import datetime as dt
import html
import re
from zoneinfo import ZoneInfo

e = html.escape
NEAR_DONE, EARLY_BELOW = 85, 30
STATE = {"near": "Near done", "mid": "In progress", "early": "Early"}


def pct(n: int, d: int) -> int:
    return round(100 * n / d) if d else 0


def clean(t: str) -> str:
    """Page copy has no em dashes (David's house rule)."""
    return re.sub(r"\s*—\s*", ", ", str(t))


def state(done_pct: int, started_pct: int) -> str:
    if done_pct >= NEAR_DONE:
        return "near"
    return "early" if started_pct < EARLY_BELOW else "mid"


def when(iso: str, tz: str) -> tuple[str, str]:
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(tz))
    return f"{t:%a %b} {t.day}", f"{t.hour % 12 or 12}:{t:%M} {'AM' if t.hour < 12 else 'PM'}"


def mod_key(m: dict) -> str:
    return f"{m['id']} {m['name']}"


def facts(m: dict) -> dict:
    """Per-module numbers on today's requirement list, for the bar 7 days ago and the bar now."""
    i, w = m["issues"], m.get("week") or {"started": 0, "done": 0}
    n = i["total"]
    s0, s1, d0, d1 = pct(w["started"], n), pct(i["started"], n), pct(w["done"], n), pct(i["done"], n)
    return {"n": n, "s0": s0, "s1": s1, "d0": d0, "d1": d1, "w": w, "left": n - i["started"],
            "state": state(d1, s1), "moved": i["done"] - w["done"], "started_moved": i["started"] - w["started"]}


def bar(label: str, started: int, done: int, n: int, cls: str, pc: int) -> str:
    w = lambda k: f"{100 * k / n:.2f}%" if n else "0%"
    title = f"{label}: {done} done, {started - done} started only, {n - started} still to be built of {n}"
    return (f'<div class="pb {cls}" title="{e(title)}"><span class="pl">{e(label)}</span><div class="bar" role="img" aria-label="{e(title)}">'
            f'<i class="t" style="width:{w(done)}"></i><i class="b" style="width:{w(started - done)}"></i><i class="n" style="width:{w(n - started)}"></i></div>'
            f'<span class="pv">{pc}%</span></div>')


def chg(a: int, b: int) -> str:
    """Green when it went up, red when it did not (David S, 31 Oct: above 0 green, 0 or below red)."""
    d = b - a
    sign = "+" if d > 0 else "\u2212" if d < 0 else ""
    return f'<span class="{"up" if d > 0 else "down"}">{sign}{abs(d)}%</span>'


def dpt(v) -> str:
    if v is None:
        return '<span class="muted">\u2013</span>'
    r = round(v)
    return f'<span class="{"up" if r > 0 else "down"}">{"+" if r > 0 else "\u2212" if r < 0 else ""}{abs(r)}%</span>'


def colored(text: str, v) -> str:
    return text if v is None else f'<span class="{"up" if round(v) > 0 else "down"}">{text}</span>'


def chip(cell, prev) -> str:
    if not cell:
        return '<span class="muted">–</span>'
    st = cell["state"]
    label = {"done": "Done", "partial": "Partial", "none": "To build", "n/a": "n/a"}[st]
    if st == "partial" and cell.get("ratio") is not None:
        label = f"{round(cell['ratio'] * 100)}%"
    up = ' <span class="up" title="improved in the last 7 days">▲</span>' if prev and prev.get("ratio") is not None and cell.get("ratio") is not None and cell["ratio"] > prev["ratio"] + 1e-9 else ""
    return f'<span class="cs c-{st.replace("/", "")}" title="{e("; ".join(cell.get("detail", [])))}">{label}</span>{up}'


def label_of(m: dict) -> str:
    return f'<span class="mid">{e(m["id"])}</span> {e(m["name"])}'


def totals(mods: list[dict]) -> dict:
    t = {"n": 0, "s0": 0, "s1": 0, "d0": 0, "d1": 0}
    for m in mods:
        i, w = m["issues"], m.get("week") or {"started": 0, "done": 0}
        t["n"] += i["total"]; t["s1"] += i["started"]; t["d1"] += i["done"]; t["s0"] += w["started"]; t["d0"] += w["done"]
    return t


def auto_intro(r: dict, t: dict, a: str) -> str:
    s = r["summary"]
    moved = sum(1 for m in r["modules"] if facts(m)["moved"] > 0 or facts(m)["started_moved"] > 0)
    return (f"Since {a}, {r['title']} went from {pct(t['s0'], t['n'])}% of its requirements started to {pct(t['s1'], t['n'])}%, and from "
            f"{pct(t['d0'], t['n'])}% done to {pct(t['d1'], t['n'])}%. {s['prs_merged_week']} pull requests were merged and {moved} "
            f"of {len(r['modules'])} modules moved. \"Done\" means merged and marked done; nothing here is called live.")


def repo_notes(notes: dict | None, r: dict) -> dict:
    n = (notes or {}).get(slug(r["repo"]))
    return n if n and n.get("checked") is True else {}


def slug(full: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", full.lower()).strip("-")


def summary_block(r: dict, win: dict, notes: dict | None) -> tuple[str, str]:
    """(intro paragraph, tiles) for one repository."""
    a, _ = when(win["start"], win["tz"])
    t, s, wk = totals(r["modules"]), r["summary"], repo_notes(notes, r)
    left = t["n"] - t["s1"]
    left0 = t["n"] - t["s0"]
    trend = s["prs_merged_week"] - s["prs_merged_prev_week"]
    intro = clean(wk.get("intro") or auto_intro(r, t, a))
    kp = lambda k, l, c: f'<div class="kpi"><b>{k}</b><span>{l}</span><span class="chg">{c}</span></div>'
    tiles = ('<div class="kpis">'
             + kp(f"{pct(t['s1'], t['n'])}%", f"{t['s1']:,} of {t['n']:,} requirements started (a PR opened or merged)",
                  f"{chg(pct(t['s0'], t['n']), pct(t['s1'], t['n']))} · {pct(t['s0'], t['n'])}% on {e(a)} · 1 day {colored(e(fmt_pt(s['delta_day'])), s['delta_day'])}")
             + kp(f"{pct(t['d1'], t['n'])}%", f"{t['d1']:,} of {t['n']:,} done (merged and marked done)",
                  f"{chg(pct(t['d0'], t['n']), pct(t['d1'], t['n']))} · {pct(t['d0'], t['n'])}% on {e(a)} · 1 day {colored(e(fmt_pt(s.get('done_delta_day'))), s.get('done_delta_day'))}")
             + kp(f"{left:,}", f"requirements still to be built ({pct(left, t['n'])}%)",
                  (f'<span class="up">−{left0 - left}</span>' if left < left0 else '<span class="muted">0</span>') + f" since {e(a)}")
             + kp(f"{s['prs_merged_week']:,}", "pull requests merged in 7 days",
                  f"<span class=\"{'up' if trend > 0 else 'down'}\">{trend:+d}</span> vs the week before · {s['prs_per_day_week']} per day · {s['pct_points_per_pr_week'] if s['pct_points_per_pr_week'] is not None else '–'} % per PR")
             + "</div>")
    return intro, tiles


def fmt_pt(v) -> str:
    return "–" if v is None else (f"{round(v):+d}%" if round(v) else "0%")


def repo_sections(r: dict, win: dict, notes: dict | None) -> str:
    mods = r["modules"]
    a, at = when(win["start"], win["tz"])
    b, _ = when(win["end"], win["tz"])
    fx = {m["id"]: facts(m) for m in mods}
    code = r["has_code_columns"]
    sm = lambda m: f'<li>{label_of(m)} '
    moved = sorted((m for m in mods if fx[m["id"]]["moved"] > 0 or fx[m["id"]]["started_moved"] > 0),
                   key=lambda m: (fx[m["id"]]["d1"] - fx[m["id"]]["d0"], fx[m["id"]]["s1"] - fx[m["id"]]["s0"]), reverse=True)[:8]
    moved_h = "".join(f'{sm(m)}<span class="muted">· {fx[m["id"]]["s0"]}% → {fx[m["id"]]["s1"]}% started · {fx[m["id"]]["d0"]}% → {fx[m["id"]]["d1"]}% done</span></li>'
                      for m in moved) or '<li class="muted">No module moved.</li>'
    sized = [m for m in mods if fx[m["id"]]["n"]]
    behind = sorted(sized, key=lambda m: (fx[m["id"]]["d1"], fx[m["id"]]["s1"], -fx[m["id"]]["n"]))[:8]
    behind_h = "".join(f'{sm(m)}<span class="muted">· {fx[m["id"]]["d1"]}% done, {fx[m["id"]]["left"]} of {fx[m["id"]]["n"]} still to be built</span></li>' for m in behind)
    gaps = sorted((m for m in sized if fx[m["id"]]["left"]), key=lambda m: -fx[m["id"]]["left"])[:6]
    gaps_h = "".join(f'{sm(m)}<span class="muted">· {fx[m["id"]]["left"]} requirement{"s" if fx[m["id"]]["left"] != 1 else ""} still to be built ({pct(fx[m["id"]]["left"], fx[m["id"]]["n"])}%)</span></li>' for m in gaps) \
        or '<li class="muted">No requirement is still to be built.</li>'
    near = [m for m in sized if fx[m["id"]]["state"] == "near"]
    near_h = f"{NEAR_DONE}% or more done: " + (", ".join(label_of(m) for m in near) or "none yet") + "."

    rows = []
    for m in mods:
        f = fx[m["id"]]
        if not f["n"]:
            rows.append(f'<tr><td class="mid">{e(m["id"])}</td><th scope="row" class="mt">{e(m["name"])}</th><td colspan="{9 if code else 6}" class="muted">No tracked requirements. {m["prs_merged_week"]} PRs merged in 7 days.</td></tr>')
            continue
        arrow = lambda x, y: f"({x}% → {y}%)" if x != y else f"({y}%)"
        cells = (f'<td class="num">{m["issues"]["started"]:,}/{f["n"]:,} <span class="muted">{arrow(f["s0"], f["s1"])}</span></td>'
                 f'<td class="barcell"><div class="pair">{bar(a, f["w"]["started"], f["w"]["done"], f["n"], "was", f["d0"])}{bar(b, m["issues"]["started"], m["issues"]["done"], f["n"], "now", f["d1"])}</div></td>'
                 f'<td class="num">{m["issues"]["done"]:,} <span class="strong">{arrow(f["d0"], f["d1"])}</span></td>'
                 f'<td class="num dcol">{chg(f["d0"], f["d1"])}</td><td class="num dcol">{dpt(m.get("delta_day"))}</td>'
                 f'<td class="num">{m["prs_merged_week"]}</td>')
        if code:
            c, cw = m.get("code") or {}, m.get("code_week") or {}
            cells += "".join(f"<td>{chip(c.get(k), cw.get(k))}</td>" for k in ("frontend", "backend", "connection"))
        rows.append(f'<tr><td class="mid">{e(m["id"])}</td><th scope="row" class="mt">{e(m["name"])}</th>{cells}<td><span class="pill {f["state"]}">{STATE[f["state"]]}</span></td></tr>')
    head = ('<th>Module</th><th>Title</th><th class="num">Started/total</th>'
            f'<th>{e(a)} above, {e(b)} below</th><th class="num">Done</th><th class="num dcol" title="Change in percent done since {e(a)}">Δ 7 days</th>'
            '<th class="num dcol" title="Change in percent started in the last 24 hours">Δ 1 day</th><th class="num">PRs 7d</th>'
            + ("<th>Frontend</th><th>Backend</th><th>Wired</th>" if code else "") + "<th>State</th>")
    legend = (f'<div class="legend"><span><i style="background:var(--tested)"></i>Done (merged and marked done)</span><span><i style="background:var(--built)"></i>Started, not done</span>'
              f'<span><i style="background:var(--none);outline:1px solid var(--line)"></i>Still to be built</span><span>Each row: <b>{e(a)}</b> bar above, <b>{e(b)}</b> bar below</span></div>')
    ledger = f'<section>\n<h2>Module by module</h2>\n{legend}\n<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>\n</section>'

    wk = repo_notes(notes, r)
    order = {mod_key(m): i for i, m in enumerate(mods)}
    name_to_key = {m["name"]: mod_key(m) for m in mods}
    items = sorted(wk.get("modules", {}).items(), key=lambda kv: order.get(name_to_key.get(kv[0], kv[0]), 999))
    li = lambda x: f"<li>{e(clean(x['text'] if isinstance(x, dict) else x))}</li>"
    plain = "".join(f"<div><h3>{e(k)}</h3><ul>{''.join(li(x) for x in v)}</ul></div>" for k, v in items if v)
    plain = (f'<section>\n<h2>What changed in the last 7 days, in plain terms</h2>\n<div class="three">{plain}</div>\n</section>' if plain else "")
    coming = "".join(f"<li><b>{e(c['module'])}.</b> {e(clean(c['text']))}</li>" for c in wk.get("coming", []))
    coming = f'<section>\n<h2>Coming next</h2>\n<ul>{coming}</ul>\n</section>' if coming else ""
    warn = "".join(f'<p>{e(w.replace("labelled", "labeled"))}</p>' for w in r["warnings"])
    warn = f'<section class="note warn warns">{warn}</section>' if warn else ""
    return (f'{ledger}\n{warn}\n'
            f'<section class="two"><div><h2>Moved most since {e(a)}</h2><ul>{moved_h}</ul></div><div><h2>Furthest behind</h2><ul>{behind_h}</ul></div></section>\n'
            f'<section class="two"><div><h2>Biggest untouched gaps</h2><ul>{gaps_h}</ul></div><div><h2>Near done</h2><p class="note">{near_h}</p></div></section>\n'
            f'{plain}\n{coming}')


def how_to_read(win: dict, code: bool) -> str:
    a, at = when(win["start"], win["tz"])
    b, bt = when(win["end"], win["tz"])
    return (f'<p>The window is the last 7 days at the same time of day: {e(a)}, {e(at)} to {e(b)}, {e(bt)} ({e(win["tz"])}), never Monday to today. '
            'Every figure is recomputed from GitHub timestamps and git history, so the page needs no earlier run.</p>'
            '<p><b>Started</b> means a pull request has been opened or merged for the requirement. <b>Done</b> means its work is merged and the requirement is closed or labeled done. '
            '<b>Still to be built</b> means no pull request names it yet. Both bars use today\'s requirement list, so a bar moves only when the work moves. '
            '"Δ 7 days" is the change in percent done since the earlier date; "Δ 1 day" is the change in percent started in the last 24 hours.</p>'
            f'<p>State pills: Near done is {NEAR_DONE}% or more done; Early is under {EARLY_BELOW}% started; everything else is In progress.'
            + (' Frontend, Backend and Wired come from the code in the repository: route files present, a backend route table, and an API client used by the app. A triangle marks a column that improved in 7 days.' if code else '')
            + '</p><p>The plain-terms notes are drafted from the pull requests merged in the window and checked, sentence by sentence, by a second reviewer before the page is published.</p>')


def render_html(report: dict, title: str = "Build progress", notes: dict | None = None) -> str:
    win = report["window"]
    a, _ = when(win["start"], win["tz"])
    b, bt = when(win["end"], win["tz"])
    repos = report["repos"]
    body = []
    if len(repos) > 1:
        rows = ""
        for r in repos:
            t, s = totals(r["modules"]), r["summary"]
            rows += (f'<tr><th scope="row">{e(r["title"])}<br><code>{e(r["repo"])}</code></th><td class="num">{pct(t["s1"], t["n"])}% <span class="muted">({pct(t["s0"], t["n"])}%)</span></td>'
                     f'<td class="num">{pct(t["d1"], t["n"])}% <span class="muted">({pct(t["d0"], t["n"])}%)</span></td><td class="num dcol">{chg(pct(t["d0"], t["n"]), pct(t["d1"], t["n"]))}</td>'
                     f'<td class="num">{s["prs_merged_week"]}</td><td class="num">{s["prs_per_day_week"]}</td></tr>')
        body.append('<section><h2>All active builds</h2><div class="scroll"><table><thead><tr><th>Repository</th><th class="num">Started (7 days ago)</th>'
                    '<th class="num">Done (7 days ago)</th><th class="num dcol">Δ 7 days</th><th class="num">PRs 7d</th><th class="num">PRs/day</th></tr></thead>'
                    f'<tbody>{rows}</tbody></table></div></section>')
        for r in repos:
            intro, tiles = summary_block(r, win, notes)
            body.append(f'<section><h2>{e(r["title"])}</h2><p class="context">{e(intro)}</p>{tiles}</section>')
        lede = f"{len(repos)} active repositories, each with its own page for sharing."
        first_intro = ""
    else:
        r = repos[0] if repos else None
        lede = ""
        first_intro = ""
        if r:
            intro, tiles = summary_block(r, win, notes)
            first_intro = f'<p class="context">{e(intro)}</p>'
            body.append(tiles)
            body.append(repo_sections(r, win, notes))
    fails = "".join(f'<p class="note warn">{e(f["repo"])} failed: {e(f["error"])}</p>' for f in report.get("failures", []))
    eyebrow = f'{e(report["date"])} · last 7 days, {e(a)} to {e(b)}, {e(bt)}'
    lede = lede or ("Where the build stands, module by module. Each module shows the bar from 7 days earlier above today's bar, so the change is visible at a glance. "
                    "Measured over the last 7 days at the same time of day.")
    # a fragment, not a document: the Artifact tool wraps it in its own skeleton at publish time
    return (f'<title>{e(title)}</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n'
            f'<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n<link rel="stylesheet" href="{FONTS}">\n'
            f'<style>{CSS}</style>\n<div class="wrap">\n<header><div class="eyebrow">{eyebrow}</div><h1>{e(title)}</h1><p>{e(lede)}</p>{first_intro}</header>\n'
            f'{fails}{"".join(body)}\n<section class="note"><h2>How to read this</h2>{how_to_read(win, any(r["has_code_columns"] for r in repos))}'
            f'<p class="muted">Generated {e(report["generated_at"])} by the progress-tracker plugin.</p></section>\n</div>\n')


FONTS = "https://fonts.googleapis.com/css2?family=Bitter:wght@500;700&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap"
# The build-status page's look (Ranch Rides Build Status, 15 Oct 2026), light and dark.
CSS = r'''
:root{--bg:#f6f7f5;--surface:#ffffff;--fg:#1d2524;--muted:#5d6967;--line:#dde2df;--accent:#0f6e6a;
--tested:#0f6e6a;--built:#9cc9c3;--none:#e3e6e4;--near:#1f7a4d;--mid:#8a6400;--early:#a3452f;
--near-bg:#e3f2ea;--mid-bg:#f7efd9;--early-bg:#f8e6e1;
--display:"Bitter",Georgia,serif;--body:"Source Sans 3",system-ui,sans-serif;--mono:"JetBrains Mono",ui-monospace,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121716;--surface:#1a2120;--fg:#e6ecea;--muted:#9aa7a4;--line:#2c3533;--accent:#5cc4bb;--tested:#4fb8ae;--built:#2f6460;--none:#2a3230;--near:#7fd3a4;--mid:#e4c26a;--early:#f0a08c;--near-bg:#1d3328;--mid-bg:#352d17;--early-bg:#3a231d;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121716;--surface:#1a2120;--fg:#e6ecea;--muted:#9aa7a4;--line:#2c3533;--accent:#5cc4bb;--tested:#4fb8ae;--built:#2f6460;--none:#2a3230;--near:#7fd3a4;--mid:#e4c26a;--early:#f0a08c;--near-bg:#1d3328;--mid-bg:#352d17;--early-bg:#3a231d;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 var(--body)}
.wrap{max-width:1040px;margin:0 auto;padding-block:40px 64px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:36px}
h1,h2{font-family:var(--display);text-wrap:balance;margin:0}h1{font-size:2rem;line-height:1.15}h2{font-size:1.3rem}h3{margin:0}
.eyebrow{font:600 .75rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
header{display:grid;gap:10px}header p{margin:0;max-width:68ch;color:var(--muted)}
header p.context,p.context{color:var(--fg);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:10px 14px;max-width:none;margin:0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px 16px;display:grid;gap:2px;align-content:start}
.kpi b{font:600 1.8rem var(--display);font-variant-numeric:tabular-nums}.kpi span{color:var(--muted);font-size:.9rem}
.kpi .chg{font:600 .78rem var(--mono);color:var(--muted)}.up{color:var(--near)}.down{color:var(--early)}
section{display:grid;gap:12px}
.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:.85rem;color:var(--muted)}
.legend i{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.scroll{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:6px}
table{border-collapse:collapse;width:100%;min-width:760px;font-size:.92rem}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}
thead th{font:600 .72rem var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
th[scope=row]{font-weight:600}.mt{font-weight:400!important}
.num{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--mono);font-size:.85rem;white-space:nowrap}
.strong{font-weight:600}.muted{color:var(--muted)}
.mid{font-family:var(--mono);font-size:.82rem;color:var(--muted);white-space:nowrap}
th.dcol{text-transform:none;letter-spacing:0}.dcol{width:1%;white-space:nowrap}
.barcell{width:36%;min-width:230px}
.bar{display:flex;height:12px;border-radius:3px;overflow:hidden;gap:2px}
.bar i{display:block;height:100%}.bar .t{background:var(--tested)}.bar .b{background:var(--built)}.bar .n{background:var(--none)}
.pair{display:grid;gap:4px}.pb{display:grid;grid-template-columns:5.2em 1fr 2.6em;align-items:center;gap:8px}
.pl{font:600 .68rem var(--mono);color:var(--muted);white-space:nowrap}
.pv{font:.75rem var(--mono);text-align:right;font-variant-numeric:tabular-nums}
.pb.was .bar{opacity:.55;height:8px}.pb.was .pv{color:var(--muted)}.pb.now .pv{font-weight:600}
.pill{font:600 .72rem var(--body);padding:2px 8px;border-radius:999px;white-space:nowrap}
.pill.near{color:var(--near);background:var(--near-bg)}.pill.mid{color:var(--mid);background:var(--mid-bg)}.pill.early{color:var(--early);background:var(--early-bg)}
.cs{font:600 .72rem var(--body);padding:1px 8px;border-radius:999px;background:var(--none)}
.cs.c-done{color:var(--near);background:var(--near-bg)}.cs.c-partial{color:var(--mid);background:var(--mid-bg)}.cs.c-none{color:var(--early);background:var(--early-bg)}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:24px}.two>div{min-width:0;display:grid;gap:8px;align-content:start}
ul{margin:0;padding-left:18px;display:grid;gap:6px}
.warns{gap:4px}.note{font-size:.88rem;color:var(--muted);max-width:75ch;display:grid;gap:8px}.note p{margin:0}.warn{color:var(--mid)}
code{font-family:var(--mono);font-size:.85em}
.three{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px 24px}
.three>div{min-width:0;display:grid;gap:6px;align-content:start}.three h3{font-size:1rem}
@media (max-width:600px){.wrap{padding-block:24px 48px}}
'''
