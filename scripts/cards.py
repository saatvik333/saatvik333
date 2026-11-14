"""SVG cards for the profile: the live terminal (hero) and the stack.

GitHub serves README images through a proxy that strips scripts, so every motion
here is CSS keyframes. Each animation shares one loop length per card; times are
written in seconds, then converted to percentages of that loop.

Every card renders at three widths. The README's <picture> picks one by viewport
so text stays 10-15px wherever GitHub puts the README column (see readme.py).
"""

from __future__ import annotations

import re
import textwrap
from datetime import datetime
from html import escape
from pathlib import Path

FRAMES = Path(__file__).resolve().parent.parent / "assets" / "bongo"

FONT = 'ui-monospace,"JetBrainsMono Nerd Font","JetBrains Mono","SF Mono",Menlo,Consolas,monospace'
CHAR_W = 8.4  # every line is stretched to len*CHAR_W so clip steps land on glyphs in any font
LAYOUTS = {"wide": 840, "medium": 600, "compact": 380}  # viewBox widths
THEMES = {  # text pairs pass WCAG AA (4.5:1) on `panel`
    "dark": {"panel": "#161b22", "border": "#3d444d", "fg": "#e6edf3", "muted": "#9198a1",
             "accent": "#88c0d0", "ok": "#a3be8c", "track": "#262c36"},
    "light": {"panel": "#f6f8fa", "border": "#d1d9e0", "fg": "#1f2328", "muted": "#59636e",
              "accent": "#4c6f99", "ok": "#3f6a2c", "track": "#e3e8ee"},
}
BADGE = "4c6f99"  # profile-views badge: the light accent, white text 5.3:1

# hero timing (seconds)
TYPE_S, OUT_PAUSE_S, START_S, HOLD_S = 0.055, 0.45, 0.6, 6.0
HAPPY_S, BLINK_S = 0.7, 0.14
PROMPT = "~ ❯ "

# bongocat frames share one 500x500 canvas; ink bounds measured from a Chrome render
# of every frame: x 77-427, ear tips y 145, table line y 292, lowest paw y 348
CAT_X0, CAT_X1, CAT_TOP, CAT_TABLE, CAT_BOTTOM = 77, 427, 145, 292, 348


def ago(ts: datetime, now: datetime) -> str:
    s = int((now - ts).total_seconds())
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def wrap(s: str, cols: int, max_lines: int = 3) -> list[str]:
    lines = textwrap.wrap(s, cols, break_on_hyphens=False) or [""]
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [clip(" ".join(lines[max_lines - 1:]), cols)]
    return lines


def frame(name: str) -> str:
    """Inner drawing of one bongocat frame, minus editor metadata and raster leftovers."""
    svg = (FRAMES / f"{name}.svg").read_text()
    body = svg[svg.index(">", svg.index("<svg")) + 1 : svg.rindex("</svg>")]
    body = re.sub(r"<defs>.*?</defs>|<title>.*?</title>|<image\b[^>]*/>", "", body, flags=re.S)
    return re.sub(r'\s(?:id|bx:\w+)="[^"]*"', "", body).strip()


def windows(name: str, shown: list[tuple[float, float]], total: float) -> str:
    """Keyframes: opacity 1 inside each (start, end) window, 0 elsewhere. Use with step-end.
    Each offset appears once: a window ending where the next begins merges into it, and
    a window at 0 replaces the opening stop, so no two keyframes share an offset."""
    stops: dict[str, int] = {"0.000": 0}
    for a, b in shown:
        start, end = f"{a / total * 100:.3f}", f"{b / total * 100:.3f}"
        if start == end:  # float residue from window arithmetic: nothing to show
            continue
        stops[start] = 1
        stops.setdefault(end, 0)
    stops.setdefault("100.000", 0)
    return f"@keyframes {name}{{{''.join(f'{k}%{{opacity:{v}}}' for k, v in stops.items())}}}"


def faces_timeline(typing: list[tuple[float, float]], outputs: list[float], end: float,
                   total: float) -> dict[str, list[tuple[float, float]]]:
    """Which face shows when: focused while typing, happy when output lands, then a
    look around with a blink while the session idles. Returns face -> windows."""
    # (time, rank, face): at equal times the higher rank is applied last and wins,
    # so typing always beats the happy face it may touch
    marks: list[tuple[float, int, str]] = [(0.0, 0, "<_>")]
    for t in outputs:
        marks += [(t, 1, "^_^"), (t + HAPPY_S, 0, "<_>")]
    for a, b in typing:
        marks += [(a, 2, ">_<"), (b, 0, "<_>")]
    look = [(">_>", 1.1), ("<_>", 0.5), ("<_<", 1.1), ("<_>", 0.9), ("-_-", BLINK_S), ("<_>", 0.6),
            ("-_-", BLINK_S)]
    t = end + 0.6
    for face, dur in look:
        if t >= total:
            break
        marks.append((t, 0, face))
        t += dur
    marks.append((min(t, total), 0, "<_>"))
    marks.sort(key=lambda m: m[:2])
    # a happy face that would run into typing is cut: drop marks that land inside a typing window
    marks = [m for m in marks if m[1] == 2 or not any(a < m[0] < b for a, b in typing)]
    out: dict[str, list[tuple[float, float]]] = {}
    for (a, _, face), (b, _, _) in zip(marks, marks[1:] + [(total, 0, "")]):
        if b > a:
            out.setdefault(face, []).append((a, b))
    return out


def session(a: dict, cfg: dict, now: datetime, cols: int) -> list[tuple[str, str, str]]:
    """The terminal transcript, fitted to `cols` characters: (kind, text, colour-role).
    Commands that don't fit split in two; outputs word-wrap."""
    prompt_cols = cols - len(PROMPT)
    lines = [("cmd", "whoami", "fg")]
    who = f"{a['name'].lower()} · {cfg['tagline']}"
    for ln in ([who] if len(who) <= cols else [a["name"].lower(), *wrap(cfg["tagline"], cols, 2)]):
        lines.append(("out", ln, "muted"))
    git = f"git -C {a['repo']} log -1 --format=%s"
    if len(git) <= prompt_cols:
        lines.append(("cmd", git, "fg"))
    else:
        lines += [("cmd", clip(f"cd {a['repo']}", prompt_cols), "fg"),
                  ("cmd", "git log -1 --format=%s", "fg")]
    lines += [("out", ln, "ok") for ln in wrap(a["message"], cols, 2)]
    lines.append(("cmd", "uptime", "fg"))
    up = f"last push {ago(a['at'], now)} · ★ {a['stars']:,} across my repos"
    lines += [("out", ln, "muted") for ln in wrap(up, cols, 2)]
    return lines


def hero(theme: str, layout: str, a: dict, cfg: dict, now: datetime) -> str:
    c, W = THEMES[theme], LAYOUTS[layout]
    user, asleep = cfg["user"], a["asleep"]
    x0 = 24 if W >= 600 else 18
    cols = int((W - 2 * x0) / CHAR_W)
    lines = session(a, cfg, now, cols)
    prompt_w = len(PROMPT) * CHAR_W
    y0, lh = 74, 26
    css, rows, typing, outputs, extents = [], [], [], [], []
    t = START_S
    for i, (kind, text, role) in enumerate(lines):
        y, cid = y0 + i * lh, f"l{i}"
        if kind == "cmd":
            width, dur = prompt_w + len(text) * CHAR_W, len(text) * TYPE_S
            typing.append((t, t + dur))
            css.append(f"#{cid}{{animation:{cid}o {{T}}s step-end infinite}}"
                       f"@keyframes {cid}o{{0%{{opacity:0}}{t:.3f}%,100%{{opacity:1}}}}")
            css.append(f"#{cid} rect{{animation:{cid} {{T}}s linear infinite}}"
                       f"@keyframes {cid}{{0%,{t:.3f}%{{width:{prompt_w:.1f}px}}"
                       f"{t + dur:.3f}%,100%{{width:{width:.1f}px}}}}")
            t += dur + OUT_PAUSE_S
            body = (f'<tspan fill="{c["accent"]}">{escape(PROMPT)}</tspan>'
                    f'<tspan fill="{c[role]}">{escape(text)}</tspan>')
        else:
            width = len(text) * CHAR_W
            # a wrapped output lands in one step with its first line
            if i and lines[i - 1][0] == "out":
                at = outputs[-1]
            else:
                at = t
                outputs.append(t)
                t += OUT_PAUSE_S
            css.append(f"#{cid}{{animation:{cid} {{T}}s step-end infinite}}"
                       f"@keyframes {cid}{{0%{{opacity:0}}{at:.3f}%,100%{{opacity:1}}}}")
            body = escape(text)
        extents.append((y, x0 + width))
        rows.append(
            f'<g id="{cid}"><clipPath id="c{cid}"><rect x="{x0}" y="{y - 18}" height="{lh}" '
            f'width="{width:.1f}"/></clipPath><text x="{x0}" y="{y}" fill="{c[role]}" '
            f'textLength="{width:.1f}" lengthAdjust="spacingAndGlyphs" clip-path="url(#c{cid})">'
            f"{body}</text></g>")
    end = t
    css.append(f"#cur{{animation:cur {{T}}s step-end infinite}}"
               f"@keyframes cur{{0%{{opacity:0}}{end:.3f}%,100%{{opacity:1}}}}")
    total = end + HOLD_S
    if asleep:  # the session is already on screen; nothing types while the cat sleeps
        css = []
    css = [re.sub(r"(\d+\.\d{3})%", lambda m: f"{float(m.group(1)) / total * 100:.3f}%", s)
           .replace("{T}", f"{total:.2f}") for s in css]

    cy = y0 + len(lines) * lh
    extents.append((cy, x0 + prompt_w + 12))
    cursor = (f'<g id="cur"><text x="{x0}" y="{cy}" fill="{c["accent"]}">{escape(PROMPT)}</text>'
              f'<rect class="caret" x="{x0 + prompt_w + 2:.1f}" y="{cy - 14}" width="9" height="18" '
              f'fill="{c["accent"]}"/></g>')

    # bongocat sits on the terminal's bottom edge, right-aligned; the terminal grows
    # until no text row runs under the cat
    scale = {"wide": 0.44, "medium": 0.36, "compact": 0.27}[layout]
    cx = W - 14 - CAT_X1 * scale
    cat_left = cx + CAT_X0 * scale - 8
    term_h = cy + 22
    while True:
        cat_top = term_h - (CAT_TABLE - CAT_TOP) * scale
        if all(right < cat_left or y + 5 < cat_top for y, right in extents):
            break
        term_h += 2
    h = round(term_h + (CAT_BOTTOM - CAT_TABLE) * scale + 2)
    cyy = term_h - CAT_TABLE * scale
    tf = f'transform="translate({cx:.1f} {cyy:.1f}) scale({scale})"'
    if asleep:
        zx, zy = cx + 330 * scale, cyy + 190 * scale
        cat = (f'<g {tf}>{frame("sleeping")}</g>'
               f'<text class="z" x="{zx:.0f}" y="{zy:.0f}" fill="{c["muted"]}" font-size="18">z</text>'
               f'<text class="z z2" x="{zx + 16:.0f}" y="{zy - 18:.0f}" fill="{c["muted"]}" font-size="14">z</text>')
        cat_css = (".z{animation:z 3.2s ease-in-out infinite}.z2{animation-delay:1.6s}"
                   "@keyframes z{0%{opacity:0;transform:translateY(6px)}50%{opacity:1}"
                   "100%{opacity:0;transform:translateY(-10px)}}")
    else:
        left, right = [], []
        for a0, b0 in typing:  # paws alternate every two keystrokes
            k, flip = a0, True
            while k < b0:
                (left if flip else right).append((k, min(k + TYPE_S * 2, b0)))
                flip, k = not flip, k + TYPE_S * 2
        idle, cur = [], 0.0
        for a0, b0 in sorted(left + right):
            if a0 > cur:
                idle.append((cur, a0))
            cur = max(cur, b0)
        idle.append((cur, total))
        cat = (f'<g {tf}><g class="ci">{frame("both-up")}</g><g class="cl">{frame("left-down")}</g>'
               f'<g class="cr">{frame("right-down")}</g></g>')
        cat_css = "".join(
            f".{n}{{animation:{n} {total:.2f}s step-end infinite}}{windows(n, w, total)}"
            for n, w in (("ci", idle), ("cl", left), ("cr", right)))

    # title-bar face: reacts to the session (or snores)
    if asleep:
        timeline, face_total = {"-_-": [(0, 1.6)], "-.-": [(1.6, 3.2)]}, 3.2
    else:
        timeline, face_total = faces_timeline(typing, outputs, end, total), total
    faces, face_css = [], []
    for k, (face, wins) in enumerate(sorted(timeline.items())):
        name = f"f{k}"
        faces.append(f'<text class="face {name}" x="{x0 + 16}" y="23" textLength="{3 * CHAR_W:.1f}" '
                     f'lengthAdjust="spacingAndGlyphs">{escape(face)}</text>')
        face_css.append(f".{name}{{animation:{name} {face_total:.2f}s step-end infinite}}"
                        f"{windows(name, wins, face_total)}")
    still = "-_-" if asleep else "&gt;_&lt;"

    state, dot = ("asleep", c["muted"]) if asleep else ("active", c["ok"])
    desc = (f"{a['repo']}: {a['message']}, pushed {ago(a['at'], now)}. "
            f"Bongocat {'sleeps' if asleep else 'drums along'}.")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img" aria-labelledby="t d">
<title id="t">{escape(a["name"])}</title>
<desc id="d">{escape(desc)}</desc>
<style>
text{{font:14px {FONT};white-space:pre}}
.face{{font-size:13px;font-weight:700;fill:{c["accent"]};opacity:0}}
.caret{{animation:b 1.1s step-end infinite}}@keyframes b{{50%{{opacity:0}}}}
.cl,.cr{{opacity:0}}
{"".join(css)}
{"".join(face_css)}
{cat_css}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important}}.face{{opacity:0}}.face.still{{opacity:1}}.ci{{opacity:1}}.cl,.cr{{opacity:0}}#cur,[id^=l]{{opacity:1}}[id^=l] rect{{width:100%}}}}
</style>
<rect x="1" y="1" width="{W - 2}" height="{term_h - 2}" rx="10" fill="{c["panel"]}" stroke="{c["border"]}"/>
<path d="M1 36h{W - 2}" stroke="{c["border"]}"/>
<circle cx="{x0}" cy="18.5" r="5" fill="{dot}"/>
{"".join(faces)}
<text class="face still" x="{x0 + 16}" y="23" textLength="{3 * CHAR_W:.1f}" lengthAdjust="spacingAndGlyphs">{still}</text>
<text x="{x0 + 16 + 4 * CHAR_W:.1f}" y="23" fill="{c["muted"]}" font-size="13">{escape(user)} · {state}</text>
{"".join(rows)}
{cursor}
{cat}
</svg>
'''


def stack(theme: str, layout: str, langs: list[tuple[str, float, str]], tools: list[str],
          daily: list[str], days: int) -> str:
    c, W = THEMES[theme], LAYOUTS[layout]
    x0 = 24 if W >= 600 else 18
    bar_w, bar_y = W - 2 * x0, 62
    per_row = {"wide": 4, "medium": 3, "compact": 2}[layout]
    col_w = bar_w / per_row
    segs, legend, css = [], [], []
    x = float(x0)
    for i, (name, share, colour) in enumerate(langs):
        w = share * bar_w
        segs.append(f'<rect class="s s{i}" x="{x:.1f}" y="{bar_y}" width="{max(w - 2, 1):.1f}" '
                    f'height="10" fill="{colour}"/>')
        css.append(f".s{i}{{animation-delay:{0.15 + i * 0.12:.2f}s}}"
                   f".g{i}{{animation-delay:{0.25 + i * 0.12:.2f}s}}")
        lx, ly = x0 + (i % per_row) * col_w, 104 + (i // per_row) * 26
        label = name.lower()
        legend.append(
            # GitHub's language colours range from near-white to near-black; a hairline
            # keeps every dot visible on both panels
            f'<g class="g g{i}"><circle cx="{lx + 5:.1f}" cy="{ly - 5}" r="4.5" fill="{colour}" '
            f'stroke="{c["fg"]}" stroke-opacity=".25"/>'
            f'<text x="{lx + 18:.1f}" y="{ly}" fill="{c["fg"]}">{escape(label)}</text>'
            f'<text x="{lx + 18 + (len(label) + 1) * CHAR_W:.1f}" y="{ly}" fill="{c["muted"]}">'
            f'{share * 100:.1f}%</text></g>')
        x += w
    y = 104 + ((len(langs) + per_row - 1) // per_row) * 26 + 8
    label_w = 13 * CHAR_W  # "builds with" + 2

    def row(label: str, items: list[str], y: int) -> tuple[str, int]:
        width = int((bar_w - label_w) / CHAR_W)
        out = [f'<text x="{x0}" y="{y}" fill="{c["muted"]}">{escape(label)}</text>']
        for ln in textwrap.wrap(" · ".join(items), width, break_on_hyphens=False):
            out.append(f'<text x="{x0 + label_w:.1f}" y="{y}" fill="{c["fg"]}">{escape(ln)}</text>')
            y += 24
        return "".join(out), y

    tools_svg, y = row("builds with", tools, y)
    daily_svg, y = row("daily", daily, y)
    h = y - 4
    span = "12 months" if days == 365 else f"{days} days"
    alt = ", ".join(f"{n} {s * 100:.1f}%" for n, s, _ in langs)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img" aria-labelledby="t d">
<title id="t">stack</title>
<desc id="d">Share of code in repos I pushed to in the last {span}: {escape(alt)}. Builds with {escape(", ".join(tools))}.</desc>
<style>
text{{font:13px {FONT};white-space:pre}}
.s{{transform-box:fill-box;transform-origin:left;animation:grow .9s cubic-bezier(.2,.7,.2,1) both}}
.g{{animation:fade .5s ease-out both}}
@keyframes grow{{from{{transform:scaleX(0)}}}}@keyframes fade{{from{{opacity:0}}}}
{"".join(css)}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important}}}}
</style>
<rect x="1" y="1" width="{W - 2}" height="{h - 2}" rx="10" fill="{c["panel"]}" stroke="{c["border"]}"/>
<path d="M1 36h{W - 2}" stroke="{c["border"]}"/>
<text x="{x0}" y="23" fill="{c["muted"]}"><tspan fill="{c["accent"]}">{escape(PROMPT)}</tspan>stack · {"repos active in the last " + span if W >= 600 else "active repos"}</text>
<text x="{W - x0}" y="23" fill="{c["muted"]}" text-anchor="end">{"share of code" if W >= 600 else "% of code"}</text>
<rect x="{x0}" y="{bar_y}" width="{bar_w}" height="10" rx="5" fill="{c["track"]}"/>
<clipPath id="bar"><rect x="{x0}" y="{bar_y}" width="{bar_w}" height="10" rx="5"/></clipPath>
<g clip-path="url(#bar)">{"".join(segs)}</g>
{"".join(legend)}
{tools_svg}
{daily_svg}
</svg>
'''
