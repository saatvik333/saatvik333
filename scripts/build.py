#!/usr/bin/env python3
"""Build the profile: README.md, llms.txt and the SVG cards, from live GitHub data.

  python3 scripts/build.py [--cards DIR] [--explain]

--cards   where the SVGs go (default: dist/), published to the `cards` branch by CI
--explain print every repo's placement and the evidence behind it

Stdlib only (Python 3.11+). GITHUB_TOKEN is required: GraphQL rejects anonymous calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cards
import projects
import readme

ROOT = Path(__file__).resolve().parent.parent


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


def summary(placements: list[projects.Placement]) -> str:
    """Markdown report for the Actions job summary: every decision, problems first."""
    rows = sorted(placements, key=lambda p: (p.category is not None, not p.reason.startswith("unclassified"),
                                             p.category or "", p.repo.name))
    out = ["| repo | section | why |", "|---|---|---|"]
    out += [f"| {p.repo.name} | {p.category or '—'} | {p.reason} |" for p in rows if p.reason != "hidden: fork"]
    todo = [p.repo.name for p in placements
            if p.reason.startswith("unclassified") or p.reason == "hidden: no description"]
    head = (f"**Needs attention:** {', '.join(todo)} (add a description, a topic, or an override in profile.json)\n\n"
            if todo else "")
    return f"## profile build\n\n{head}" + "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", type=Path, default=ROOT / "dist")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args()
    if not os.environ.get("GITHUB_TOKEN"):
        sys.exit("build.py: set GITHUB_TOKEN (GraphQL rejects anonymous requests)")

    cfg = json.loads((ROOT / "profile.json").read_text())
    user, now = cfg["user"], projects.now_utc()
    profile, repos = projects.fetch(user)

    since = now - projects.timedelta(days=cfg["stack"]["days"])
    for r in repos:  # file lists feed classification (visible repos) and tool detection (recent repos)
        if not projects.hidden(r, cfg, user, now) or (not r.fork and r.pushed > since):
            projects.fetch_files(r, user)
    placements = projects.classify(repos, cfg, user, now)
    langs, tools = projects.stack(repos, cfg, now)

    own = [r for r in repos if not r.fork and r.name != user and r.committed]
    latest = max(own, key=lambda r: r.committed)
    activity = {
        "name": profile.get("name") or user,
        "repo": latest.name,
        "message": latest.commit,
        "at": latest.committed,
        "stars": sum(r.stars for r in repos if not r.fork),
        "asleep": (now - latest.committed).total_seconds() > cfg["asleep_after_hours"] * 3600,
    }
    if os.environ.get("PROFILE_FORCE_ASLEEP"):
        activity["asleep"] = True

    args.cards.mkdir(parents=True, exist_ok=True)
    for theme in cards.THEMES:
        (args.cards / f"hero-{theme}.svg").write_text(cards.hero(theme, activity, cfg, now))
        (args.cards / f"stack-{theme}.svg").write_text(
            cards.stack(theme, langs, tools[: cfg["stack"].get("max_tools", 8)],
                        cfg["stack"]["daily"], cfg["stack"]["days"]))

    tmpl = (ROOT / "README.tmpl.md").read_text()
    changed = [name for name, text in (
        ("README.md", readme.render(tmpl, profile, placements, cfg)),
        ("llms.txt", readme.llms(profile, placements, cfg)),
    ) if write_if_changed(ROOT / name, text)]

    if args.explain:
        for p in placements:
            print(f"{p.repo.name:30} {p.category or '-':9} {p.reason}")
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as f:
            f.write(summary(placements))
    print(f"latest: {latest.name} ({cards.ago(latest.committed, now)}), asleep={activity['asleep']}; "
          f"updated: {', '.join(changed) or 'nothing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
