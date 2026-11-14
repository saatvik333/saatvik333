"""Live GitHub data for the profile, and the rules that sort repos into sections.

One GraphQL query fetches every public repo with its topics, languages, latest
commit and dependency manifests; one REST call per visible repo adds its file
list. Classification, the stack and the hero all read this one snapshot.

A rule matches a repo by any of: topics, file patterns, dependencies, or primary
language. Categories are tried in profile.json order and the first match wins, so
put the most specific category first. `overrides` beats every rule.

File patterns follow gitignore conventions:
  "SKILL.md"        basename, at any depth
  "/manifest.json"  repo root only
  "app/*.json"      full path (fnmatch)
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

API = "https://api.github.com"

QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    pinnedItems(first: 6, types: REPOSITORY) { nodes { ... on Repository { name } } }
    repositories(first: 100, ownerAffiliations: OWNER, privacy: PUBLIC,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      nodes {
        name description url stargazerCount pushedAt isFork isArchived
        repositoryTopics(first: 30) { nodes { topic { name } } }
        primaryLanguage { name }
        languages(first: 30, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
        defaultBranchRef {
          name
          target { ... on Commit { history(first: 1) { nodes { messageHeadline committedDate } } } }
        }
        pkg: object(expression: "HEAD:package.json") { ... on Blob { text } }
        cargo: object(expression: "HEAD:Cargo.toml") { ... on Blob { text } }
        pyproject: object(expression: "HEAD:pyproject.toml") { ... on Blob { text } }
      }
    }
  }
}
"""


def request(url: str, body: dict | None = None, attempts: int = 4):
    """GET (or POST `body` as JSON). GitHub's 5xx and dropped connections are transient
    on a heavy GraphQL query, so those retry with backoff; 4xx fails at once."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/vnd.github+json"})
    if token := os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {token}")
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
        time.sleep(2 ** attempt * 2)


@dataclass
class Repo:
    name: str
    description: str
    url: str
    stars: int
    pushed: datetime
    fork: bool
    archived: bool
    topics: set[str]
    language: str | None
    languages: dict[str, tuple[int, str]]  # name -> (bytes, colour)
    branch: str
    commit: str
    committed: datetime | None
    deps: set[str]
    files: list[str] = field(default_factory=list)
    pinned: int | None = None  # position among pins, None if not pinned


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _blob(node: dict, key: str) -> str:
    return (node.get(key) or {}).get("text") or ""


def parse_deps(pkg: str, cargo: str, pyproject: str) -> set[str]:
    """Dependency names from root manifests. A broken manifest yields nothing, not a crash."""
    deps: set[str] = set()
    try:
        p = json.loads(pkg) if pkg else {}
        for k in ("dependencies", "devDependencies", "peerDependencies"):
            deps.update(p.get(k) or {})
    except (json.JSONDecodeError, AttributeError):
        pass
    try:
        c = tomllib.loads(cargo) if cargo else {}
        for k in ("dependencies", "dev-dependencies", "build-dependencies"):
            deps.update(c.get(k) or {})
        deps.update((c.get("workspace") or {}).get("dependencies") or {})
    except tomllib.TOMLDecodeError:
        pass
    try:
        t = tomllib.loads(pyproject) if pyproject else {}
        for spec in (t.get("project") or {}).get("dependencies") or []:
            if m := re.match(r"[A-Za-z0-9_.\-]+", spec):
                deps.add(m.group(0).lower())
        deps.update(((t.get("tool") or {}).get("poetry") or {}).get("dependencies") or {})
    except tomllib.TOMLDecodeError:
        pass
    return deps


def fetch(user: str) -> tuple[dict, list[Repo]]:
    """(public profile from REST, repos newest push first). GraphQL's email field
    needs a user scope that the Actions token lacks; REST serves it to anyone."""
    data = request(f"{API}/graphql", {"query": QUERY, "variables": {"login": user}})
    if errors := data.get("errors"):
        raise RuntimeError(f"GraphQL: {errors[0].get('message')}")
    g = data["data"]["user"]
    profile = request(f"{API}/users/{user}")
    pins = [n["name"] for n in g["pinnedItems"]["nodes"] if n]
    repos = []
    for n in g["repositories"]["nodes"]:
        ref = n.get("defaultBranchRef") or {}
        hist = (((ref.get("target") or {}).get("history") or {}).get("nodes") or [None])[0]
        repos.append(Repo(
            name=n["name"],
            description=(n["description"] or "").strip().rstrip("."),
            url=n["url"],
            stars=n["stargazerCount"],
            pushed=_iso(n["pushedAt"]),
            fork=n["isFork"],
            archived=n["isArchived"],
            topics={t["topic"]["name"] for t in n["repositoryTopics"]["nodes"]},
            language=(n.get("primaryLanguage") or {}).get("name"),
            languages={e["node"]["name"]: (e["size"], e["node"]["color"] or "#8b949e")
                       for e in n["languages"]["edges"]},
            branch=ref.get("name") or "main",
            commit=(hist or {}).get("messageHeadline", ""),
            committed=_iso(hist["committedDate"]) if hist else None,
            deps=parse_deps(_blob(n, "pkg"), _blob(n, "cargo"), _blob(n, "pyproject")),
            pinned=pins.index(n["name"]) if n["name"] in pins else None,
        ))
    return profile, repos


def fetch_files(repo: Repo, user: str) -> None:
    try:
        tree = request(f"{API}/repos/{user}/{repo.name}/git/trees/{repo.branch}?recursive=1")
    except urllib.error.HTTPError as e:  # empty repo: 409; nothing to match against
        if e.code in (404, 409):
            return
        raise
    repo.files = [t["path"] for t in tree["tree"] if t["type"] == "blob"]


# ── rules ────────────────────────────────────────────────────────────────────


def file_matches(pattern: str, paths: list[str]) -> str | None:
    if pattern.startswith("/"):
        p = pattern[1:]
        hit = next((f for f in paths if "/" not in f and fnmatch.fnmatchcase(f, p)), None)
    elif "/" in pattern:
        hit = next((f for f in paths if fnmatch.fnmatchcase(f, pattern)), None)
    else:
        hit = next((f for f in paths if fnmatch.fnmatchcase(f.rsplit("/", 1)[-1], pattern)), None)
    return hit


def match(rule: dict, repo: Repo) -> str | None:
    """Why `repo` satisfies `rule`, or None. The reason names the first evidence found."""
    if hit := sorted(repo.topics & set(rule.get("topics", ()))):
        return f"topic {hit[0]}"
    for pattern in rule.get("files", ()):
        if f := file_matches(pattern, repo.files):
            return f"file {f}"
    if hit := sorted(repo.deps & set(rule.get("deps", ()))):
        return f"dependency {hit[0]}"
    if repo.language and repo.language in rule.get("languages", ()):
        return f"language {repo.language}"
    return None


def hidden(repo: Repo, cfg: dict, user: str, now: datetime) -> str | None:
    """Why a repo stays off the profile, or None if it is shown."""
    if repo.name == user:
        return "profile repo"
    if repo.fork:
        return "fork"
    if repo.archived:
        return "archived"
    if cfg.get("overrides", {}).get(repo.name) == "hidden":
        return "override"
    if not repo.description:
        return "no description"
    stale = cfg.get("stale", {})
    if (now - repo.pushed > timedelta(days=30 * stale.get("months", 18))
            and repo.stars < stale.get("min_stars", 10)):
        return "stale"
    return None


@dataclass
class Placement:
    repo: Repo
    category: str | None
    reason: str


def classify(repos: list[Repo], cfg: dict, user: str, now: datetime) -> list[Placement]:
    out = []
    ids = {c["id"] for c in cfg["categories"]}
    for repo in repos:
        if why := hidden(repo, cfg, user, now):
            out.append(Placement(repo, None, f"hidden: {why}"))
            continue
        forced = cfg.get("overrides", {}).get(repo.name)
        if forced in ids:
            out.append(Placement(repo, forced, "override"))
            continue
        for cat in cfg["categories"]:
            if why := match(cat, repo):
                out.append(Placement(repo, cat["id"], why))
                break
        else:
            out.append(Placement(repo, None, "unclassified: add a topic or an override"))
    return out


def rank(placements: list[Placement]) -> list[Placement]:
    """Pinned first in pin order, then stars, then most recent push."""
    return sorted(placements, key=lambda p: (
        p.repo.pinned if p.repo.pinned is not None else 99,
        -p.repo.stars,
        -p.repo.pushed.timestamp(),
    ))


def stack(repos: list[Repo], cfg: dict, now: datetime) -> tuple[list, list[str]]:
    """Languages by bytes and detected tools, across own repos pushed in the window."""
    s = cfg["stack"]
    since = now - timedelta(days=s.get("days", 365))
    mine = [r for r in repos if not r.fork and r.pushed > since]
    sizes: dict[str, list] = {}
    for r in mine:
        for name, (size, colour) in r.languages.items():
            if name in s.get("ignore_languages", ()):
                continue
            entry = sizes.setdefault(name, [0, colour])
            entry[0] += size
    total = sum(v[0] for v in sizes.values()) or 1
    langs = sorted(((n, v[0] / total, v[1]) for n, v in sizes.items()), key=lambda x: -x[1])
    shown = [l for l in langs if l[1] >= s.get("min_share", 0.02)][: s.get("max_languages", 8)]

    counts: dict[str, int] = {}
    for tool, rule in s["tools"].items():
        if n := sum(1 for r in mine if match(rule, r)):
            counts[tool] = n
    tools = sorted(counts, key=lambda t: (-counts[t], t))
    return shown, tools


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
