"""Two made-up projects with common tracker layouts (made-up names, IDs and text).

make_a: open tracker plus archive, feature folders, twin tickets (numbers differ), an old board
        script, an old board page, and `.githooks/` with Git LFS chaining.
make_b: all ticket files in one folder, several ID prefixes (some without a hyphen), extra
        columns, state names the plugin does not know, a separate integration branch, no old board.

`planted=True` adds differences that no setting can fix (a file without a row, a row without a
file, a broken link, a twin that does not exist, a state with no obvious meaning); `planted=False`
leaves only differences the suggested settings can map, so `dev-harness-board --check` must pass
once they are applied.
"""
import json
import os
import subprocess

CLASSIFY = [("in progress", "In progress"), ("in review", "In review"), ("fixing", "In review"),
            ("owner check", "Owner check"), ("integrated", "Owner check"), ("planned", "Planned"),
            ("done", "Done"), ("blocked", "Blocked"), ("waiting", "Blocked")]


def put(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def ticket(tid, title, status, extra=""):
    return f"# {tid}: {title}\n\n## Status\n\n{status}\n\n## Scope\n\nMade-up scope for {tid}.\n{extra}"


def git_init(root, env, hooks_path=None):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, env=env, check=True)
    if hooks_path:
        subprocess.run(["git", "config", "--local", "core.hooksPath", hooks_path], cwd=root, env=env, check=True)


def old_board_html(states):
    """A page shaped like a script-built board: the ticket data sits in <script id="data">."""
    cols = [["planned", "Planned"], ["progress", "In progress"], ["review", "In review"], ["owner", "Owner check"],
            ["blocked", "Blocked / Waiting / Deferred"], ["done", "Done"]]
    key = {"Planned": "planned", "In progress": "progress", "In review": "review", "Owner check": "owner",
           "Blocked": "blocked", "Done": "done"}
    tickets = [{"id": tid, "state": key[col]} for tid, col in sorted(states.items())]
    data = json.dumps({"tickets": tickets, "columns": cols})
    return f'<!doctype html><html><body><div id="app"></div><script id="data" type="application/json">{data}</script></body></html>\n'


def classify(text):
    t = text.lower()
    return next((col for prefix, col in CLASSIFY if t.startswith(prefix)), None)


def make_a(root, env, planted=True):
    """Returns {ticket id: column on the old board}."""
    git_init(root, env, ".githooks")
    put(root, "CLAUDE.md", "# Shop app\n\n## Harness\n\n- **Verify**: make test\n")
    put(root, "docs/tickets/TEMPLATE.md", "# <ID>: <title>\n\n## Status\n\nplanned\n")
    rows = [  # id, issue, review, state, note, file status, folder
        ("DESK-UI-12", "Sign-in screen spacing", "standard", "In progress", "", "in progress", "docs/tickets"),
        ("WEB-UI-9", "Sign-in screen spacing (web)", "standard", "Integrated", "see `a|b` and `c|d` for forms",
         "integrated", "docs/tickets"),
        ("WEB-UI-10", "Web only crash", "high-risk", "Waiting on the owner (decision)", "", "blocked: waiting",
         "docs/tickets"),
        ("SVC-3", "Rate limit", "self", "Planned", "", "planned", "docs/tickets"),
        ("FEAT-WEB-01", "Feature one, step one", "standard", "In review", "", "in review", "docs/features/alpha/tickets"),
        ("FEAT-WEB-02", "Feature two, step one", "standard", "Owner check", "", "owner check", "docs/features/beta/tickets"),
    ]
    archived = [("WIKI-1", "Write the guide", "self", "Done", "done", "docs/tickets/archive"),
                ("DESK-UI-4", "Old fix", "standard", "Done", "done", "docs/tickets/archive")]
    twin = {"DESK-UI-12": "Twin: WEB-UI-9", "WEB-UI-9": "Twin: DESK-UI-12", "WEB-UI-10": "Twin: none (web only)",
            "FEAT-WEB-01": "Twin: none (first step)", "FEAT-WEB-02": "Twin: none (first step)"}
    states = {}
    example = ["Example of a table in prose, not the tracker:", "", "```", "| ID | State |", "|---|---|",
               "| EX-1 | Done |", "```", ""] if planted else ["A legend comes later.", "", "", "", "", "", "", ""]
    lines = ["# Ticket tracker", ""] + example + [
             "## Status (open tickets)", "", "| ID | Summary | Impact | Review | State | Commit | Note |",
             "|---|---|---|---|---|---|---|"]
    for tid, issue, review, state, note, fstatus, folder in rows:
        name = f"{tid}-short-title.md" if tid == "WEB-UI-9" else f"{tid}.md"   # a file name with a suffix
        extra = f"\n## Links\n\n{twin[tid]}\n" if tid in twin else ""
        put(root, f"{folder}/{name}", ticket(tid, issue, fstatus, extra))
        link = f" ([ticket file]({'../features/' + folder.split('/')[2] + '/tickets/' + name if 'features' in folder else name}))"
        lines.append(f"| {tid} | {issue}{link} | No | {review} | {state} | | {note} |")
        states[tid] = classify(state)
    put(root, "docs/tickets/tracker.md", "\n".join(lines) + "\n")
    done = ["# Closed tickets", "", "| ID | Summary | Impact | Review | State | Commit | Note |", "|---|---|---|---|---|---|---|"]
    for tid, issue, review, state, fstatus, folder in archived:
        put(root, f"{folder}/{tid}.md", ticket(tid, issue, fstatus, "\n## Links\n\nTwin: none (old)\n"))
        done.append(f"| {tid} | {issue} | No | {review} | {state} | `abc1234` | |")
        states[tid] = classify(state)
    put(root, "docs/tickets/archive/tracker-closed.md", "\n".join(done) + "\n")
    put(root, "docs/tickets/archive/notes.md", "# Notes\n\nNotes, not a ticket.\n")
    if planted:
        put(root, "docs/tickets/SVC-7.md", ticket("SVC-7", "File with no row", "planned"))      # file without a row
        put(root, "docs/tickets/tracker.md", read(root, "docs/tickets/tracker.md")
            + "| SVC-8 | Row with no file | No | self | Planned | | |\n"
            + "| SVC-9 | [Broken](missing-file.md) | No | self | Planned | | |\n")
        put(root, "docs/tickets/SVC-9.md", ticket("SVC-9", "Broken link row", "planned"))
        put(root, "docs/tickets/DESK-UI-30.md", ticket("DESK-UI-30", "Twin that is not there", "planned", "\nTwin: WEB-UI-99\n"))
        put(root, "docs/tickets/tracker.md", read(root, "docs/tickets/tracker.md")
            + "| DESK-UI-30 | Twin that is not there | No | self | Frozen | | |\n")
        states.update({"SVC-8": "Planned", "SVC-9": "Planned", "DESK-UI-30": None})
        states["DESK-UI-30"] = "Planned"
    put(root, "docs/tickets/board.html", old_board_html(states))
    put(root, "scripts/tracker_board.py", "# made-up old board script\n")
    put(root, "scripts/test_tracker_board.py", "# made-up test of the old board script\n")
    put(root, ".githooks/rebuild-board.sh", "#!/bin/sh\npython3 scripts/tracker_board.py\n")
    put(root, ".githooks/post-commit", "#!/bin/sh\n.githooks/rebuild-board.sh\n")
    put(root, ".githooks/pre-push", "#!/bin/sh\ngit lfs pre-push \"$@\"\n")
    return states


def make_b(root, env, planted=True):
    git_init(root, env)
    put(root, "CLAUDE.md", "# Example reader\n\n## Harness\n\n- **Verify**: make test\n")
    put(root, "docs/tickets/TEMPLATE.md", "# <ID>: <title>\n\n## Status\n\nplanned\n")
    rows = [("K1", "Alpha task", "High", "self (test-only)", "Integrated", "integrated"),
            ("K2", "Beta task", "High", "self", "Done", "done"),
            ("Q1", "Gamma task", "Medium", "high-risk", "Blocked", "blocked: owner to accept"),
            ("Q2", "Delta task", "Low", "standard", "Planned", "planned"),
            ("Z1", "Epsilon idea", "Low", "standard", "Proposed", "proposed"),
            ("ZED-01", "Zeta one", "High", "standard", "Owner check", "owner check"),
            ("ZED-02", "Zeta two", "High", "standard", "In progress", "in progress"),
            ("OPS-01", "Omega check", "Low", "self", "Owner check", "owner check")]
    lines = ["# Ticket tracker", "", "Update a row in the same commit as the ticket.", "",
             "## Status", "", "| ID | Title | Weight | Review | State | Commit | Note |", "|---|---|---|---|---|---|---|"]
    for tid, title, prio, review, state, fstatus in rows:
        put(root, f"docs/tickets/{tid}.md", ticket(tid, title, fstatus))
        lines.append(f"| [{tid}](./{tid}.md) | {title} | {prio} | {review} | {state} | `1a2b3c4` | note |")
    if planted:
        put(root, "docs/tickets/Q9.md", ticket("Q9", "File with no row", "planned"))
        lines.append("| [Q8](Q8.md) | Row with no file | Low | self | Planned | | |")
        lines.append("| [K9](K9-missing.md) | Link goes nowhere | Low | self | Planned | | |")
        put(root, "docs/tickets/K9.md", ticket("K9", "Link goes nowhere", "planned"))
        lines.append("| [Q7](Q7.md) | Strange state | Low | self | Frozen solid | | |")
        put(root, "docs/tickets/Q7.md", ticket("Q7", "Strange state", "planned"))
    put(root, "docs/tickets/tracker.md", "\n".join(lines) + "\n")
    return rows


def read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()
