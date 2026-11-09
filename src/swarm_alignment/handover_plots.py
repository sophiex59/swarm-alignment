"""Draw the handover page's charts from the run summaries and write them into docs/handover.html.

uv run python -m swarm_alignment.export_results    # refresh results/explorer/data.json first
uv run python -m swarm_alignment.handover_plots

Each chart is inline SVG between <!--plot:NAME--> and <!--/plot:NAME--> markers, so the page
stays one standalone file. Colours are CSS variables defined by the page, so the charts follow
its light and dark themes. Hovering a mark shows its exact numbers (SVG <title>).

Charts that depend on which questions count are drawn twice: once with only directory questions
(class v-dir) and once also counting questions about the agent's own record (class v-all). The
page's toggle shows one or the other.
"""

import argparse
import json
import re
import statistics
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "handover.html"
DATA = ROOT / "results" / "explorer" / "data.json"
RUN = "Run 6"

COND_COLOR = {"off": "var(--bar)", "on": "var(--bar-2)"}
COND_NAME = {"off": "leaderboard off", "on": "leaderboard on"}
POINTS = {"correct": 1, "wrong": -1, "unknown": 0}


def f(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def pct(x: float) -> str:
    return f"{round(100 * x)}%"


class Svg:
    def __init__(self, w: int, h: int, label: str) -> None:
        self.w, self.h, self.label, self.parts = w, h, label, []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def text(self, x, y, s, anchor="start", size=11, fill="var(--muted)", weight=None) -> None:
        bold = f' font-weight="{weight}"' if weight else ""
        self.add(
            f'<text x="{f(x)}" y="{f(y)}" text-anchor="{anchor}" font-size="{size}" '
            f'fill="{fill}"{bold}>{escape(s)}</text>'
        )

    def line(self, x1, y1, x2, y2, stroke="var(--grid)", width=1, dash=None) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(
            f'<line x1="{f(x1)}" y1="{f(y1)}" x2="{f(x2)}" y2="{f(y2)}" stroke="{stroke}" '
            f'stroke-width="{width}"{d}/>'
        )

    def render(self) -> str:
        return (
            f'<svg viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{escape(self.label)}" '
            f'font-family="var(--font-body)">' + "".join(self.parts) + "</svg>"
        )


def tip(title: str, body: str) -> str:
    return f"<g><title>{escape(title)}</title>{body}</g>"


def path(points: list[tuple[float, float]]) -> str:
    return "M" + " L".join(f"{f(a)},{f(b)}" for a, b in points)


def dodge(ys: list[float], gap: float, lo: float, hi: float) -> list[float]:
    """Spread label positions at least `gap` apart, keeping them inside [lo, hi]."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    placed = [ys[i] for i in order]
    for k in range(1, len(placed)):
        placed[k] = max(placed[k], placed[k - 1] + gap)
    if placed and placed[-1] > hi:
        placed[-1] = hi
        for k in range(len(placed) - 2, -1, -1):
            placed[k] = min(placed[k], placed[k + 1] - gap)
    placed = [max(lo, y) for y in placed]
    out = [0.0] * len(ys)
    for k, i in enumerate(order):
        out[i] = placed[k]
    return out


def games_of(data: dict[str, Any], label: str = RUN) -> dict[str, dict[str, Any]]:
    run = next(r for r in data["runs"] if r["label"] == label)
    return {g["lb"]: g for g in run["games"] if g["status"] == "finished"}


def rounds_of(game: dict[str, Any]) -> list[int]:
    return [p["r"] for p in game["per_round"]]


def answers(game: dict[str, Any], r: int, own: bool) -> list[tuple[str, dict[str, Any]]]:
    """Non-holder answers in round r: directory questions, plus own-record ones if `own`."""
    return [
        (agent, cell)
        for agent, cells in game["grid"].items()
        if agent != game["holder"]
        for cell in cells
        if cell and cell["r"] == r and (own or not cell["own"])
    ]


def kind(own: bool) -> str:
    return "questions" if own else "directory questions"


def frame(svg: Svg, m: dict[str, int], rounds: int, y_ticks, y_lo, y_hi, fmt_tick):
    x = lambda r: m["l"] + (svg.w - m["l"] - m["r"]) * (r - 1) / (rounds - 1)  # noqa: E731
    y = lambda v: m["t"] + (svg.h - m["t"] - m["b"]) * (y_hi - v) / (y_hi - y_lo)  # noqa: E731
    for t in y_ticks:
        svg.line(m["l"], y(t), svg.w - m["r"], y(t))
        svg.text(m["l"] - 8, y(t) + 4, fmt_tick(t), anchor="end", size=10)
    for r in range(1, rounds + 1):
        svg.text(x(r), svg.h - m["b"] + 16, str(r), anchor="middle", size=10)
    svg.text((m["l"] + svg.w - m["r"]) / 2, svg.h - 4, "round", anchor="middle", size=10)
    return x, y


def end_labels(svg: Svg, labels, x_end: float, m: dict[str, int]) -> None:
    """Direct labels right of the last point: (y, text, colour, dashed)."""
    ys = dodge([v for v, *_ in labels], 15, m["t"] + 4, svg.h - m["b"])
    for (_, text, color, dashed), ly in zip(labels, ys, strict=True):
        dash = ' stroke-dasharray="4 3"' if dashed else ""
        svg.add(
            f'<line x1="{f(x_end + 8)}" y1="{f(ly)}" x2="{f(x_end + 22)}" y2="{f(ly)}" '
            f'stroke="{color}" stroke-width="2"{dash}/>'
        )
        svg.text(x_end + 27, ly + 4, text, size=11, fill="var(--ink)")


# 1. Mean score per round across the non-holders, ±1 SE across those agents, one line per game.
def plot_mean(data: dict[str, Any], own: bool) -> str:
    games = games_of(data)
    any_wrong = any(
        c["res"] == "wrong"
        for g in games.values()
        for r in rounds_of(g)
        for _, c in answers(g, r, own)
    )
    lo = -1 if any_wrong else 0
    svg = Svg(800, 270, f"{RUN}: mean non-holder score per round on {kind(own)}, ±1 SE")
    m = {"l": 46, "r": 160, "t": 14, "b": 36}
    ticks = (-1, -0.5, 0, 0.5, 1) if lo else (0, 0.25, 0.5, 0.75, 1)
    n_rounds = max(len(rounds_of(g)) for g in games.values())
    x, y = frame(svg, m, n_rounds, ticks, lo - 0.04, 1.04, lambda t: f"{t:+g}" if t else "0")
    labels = []
    for lb, game in sorted(games.items()):
        color, dashed = COND_COLOR[lb], lb == "on"
        pts = []
        for r in rounds_of(game):
            vals = [POINTS.get(c["res"], 0) for _, c in answers(game, r, own)]
            if not vals:
                pts.append(None)
                continue
            mean = sum(vals) / len(vals)
            se = statistics.stdev(vals) / len(vals) ** 0.5 if len(vals) > 1 else 0.0
            pts.append((r, mean, se, len(vals)))
        # Bands and lines break where a round had no included question.
        runs, cur = [], []
        for p in pts:
            if p is None:
                if cur:
                    runs.append(cur)
                cur = []
            else:
                cur.append(p)
        if cur:
            runs.append(cur)
        for seg in runs:
            upper = [(x(r), y(min(1, v + se))) for r, v, se, _ in seg]
            lower = [(x(r), y(max(lo, v - se))) for r, v, se, _ in reversed(seg)]
            svg.add(f'<path d="{path(upper + lower)} Z" fill="{color}" fill-opacity="0.12"/>')
        for seg in runs:
            dash = ' stroke-dasharray="6 4"' if dashed else ""
            svg.add(
                f'<path d="{path([(x(r), y(v)) for r, v, _, _ in seg])}" stroke="{color}" '
                f'stroke-width="2" fill="none"{dash}/>'
            )
        for r, v, se, n in (p for p in pts if p):
            svg.add(
                tip(
                    f"{COND_NAME[lb].capitalize()}, round {r}: mean {v:+.2f} ± {se:.2f} "
                    f"over {n} agent{'s' if n > 1 else ''}",
                    f'<circle cx="{f(x(r))}" cy="{f(y(v))}" r="{3 + n}" fill="{color}" '
                    f'stroke="var(--surface)" stroke-width="1.5"/>'
                    f'<circle cx="{f(x(r))}" cy="{f(y(v))}" r="10" fill="transparent"/>',
                )
            )
        all_vals = [
            POINTS.get(c["res"], 0) for r in rounds_of(game) for _, c in answers(game, r, own)
        ]
        overall = sum(all_vals) / len(all_vals)
        last = next(p for p in reversed(pts) if p)
        labels.append((y(last[1]), f"{COND_NAME[lb].capitalize()}: {overall:+.2f}", color, dashed))
    end_labels(svg, labels, x(n_rounds), m)
    return svg.render()


# 2. Cumulative share solved, one line per game.
def plot_cumulative(data: dict[str, Any], own: bool) -> str:
    games = games_of(data)
    svg = Svg(800, 270, f"{RUN}: cumulative share of {kind(own)} solved, per game")
    m = {"l": 46, "r": 160, "t": 14, "b": 36}
    n_rounds = max(len(rounds_of(g)) for g in games.values())
    x, y = frame(svg, m, n_rounds, (0, 0.25, 0.5, 0.75, 1), 0, 1, pct)
    labels = []
    for lb, game in sorted(games.items()):
        solved = asked = 0
        pts = []
        for r in rounds_of(game):
            got = answers(game, r, own)
            solved += sum(c["res"] == "correct" for _, c in got)
            asked += len(got)
            if asked:
                pts.append((r, solved, asked))
        color, dashed = COND_COLOR[lb], lb == "on"
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        svg.add(
            f'<path d="{path([(x(r), y(s / a)) for r, s, a in pts])}" stroke="{color}" '
            f'stroke-width="2" fill="none"{dash}/>'
        )
        for r, s, a in pts:
            svg.add(
                tip(
                    f"{COND_NAME[lb].capitalize()}, round {r}: {s} of {a} solved so far "
                    f"({pct(s / a)})",
                    f'<circle cx="{f(x(r))}" cy="{f(y(s / a))}" r="4" fill="{color}" '
                    f'stroke="var(--surface)" stroke-width="1.5"/>'
                    f'<circle cx="{f(x(r))}" cy="{f(y(s / a))}" r="9" fill="transparent"/>',
                )
            )
        r, s, a = pts[-1]
        labels.append((y(s / a), f"{COND_NAME[lb].capitalize()}: {s}/{a}", color, dashed))
    end_labels(svg, labels, x(n_rounds), m)
    return svg.render()


# 3. Each round: questions, requests to the holder, solves (both games pooled).
def plot_per_round(data: dict[str, Any], own: bool) -> str:
    games = games_of(data)
    svg = Svg(800, 230, f"{RUN}, each round: {kind(own)}, requests to the holder, solves")
    m = {"l": 46, "r": 20, "t": 14, "b": 36}
    rounds = rounds_of(next(iter(games.values())))
    bars = [("questions", "var(--faint)"), ("requests", "var(--s1)"), ("solved", "var(--s3)")]
    counts = {}
    for r in rounds:
        counts[r] = {}
        for lb, g in games.items():
            got = answers(g, r, own)
            req = next(p for p in g["per_round"] if p["r"] == r)["requests"]
            counts[r][lb] = {
                "questions": len(got),
                "requests": req,
                "solved": sum(c["res"] == "correct" for _, c in got),
            }
    top_value = max(sum(c[k] for c in counts[r].values()) for r in rounds for k, _ in bars)
    top_value += top_value % 2
    yv = lambda v: m["t"] + (svg.h - m["t"] - m["b"]) * (1 - v / top_value)  # noqa: E731
    for t in range(0, top_value + 1, 2):
        svg.line(m["l"], yv(t), svg.w - m["r"], yv(t))
        svg.text(m["l"] - 8, yv(t) + 4, str(t), anchor="end", size=10)
    band = (svg.w - m["l"] - m["r"]) / len(rounds)
    bw, gap = 14, 2
    names = {"questions": kind(own), "requests": "requests to the holder", "solved": "solved"}
    for i, r in enumerate(rounds):
        cx = m["l"] + band * (i + 0.5)
        for j, (key, color) in enumerate(bars):
            v = sum(c[key] for c in counts[r].values())
            bx = cx + (j - 1) * (bw + gap) - bw / 2
            top, base = yv(v), yv(0)
            rad = min(3, (base - top) / 2)
            bar = (
                f'<path d="M{f(bx)},{f(base)} V{f(top + rad)} Q{f(bx)},{f(top)} {f(bx + rad)},'
                f"{f(top)} H{f(bx + bw - rad)} Q{f(bx + bw)},{f(top)} {f(bx + bw)},{f(top + rad)} "
                f'V{f(base)} Z" fill="{color}"/>'
                if v > 0
                else ""
            )
            detail = ", ".join(f"{COND_NAME[lb]} {c[key]}" for lb, c in sorted(counts[r].items()))
            svg.add(
                tip(
                    f"Round {r}: {v} {names[key]} ({detail})",
                    bar + f'<rect x="{f(bx - 1)}" y="{m["t"]}" width="{bw + 2}" '
                    f'height="{f(base - m["t"])}" fill="transparent"/>',
                )
            )
        svg.text(cx, svg.h - m["b"] + 16, str(r), anchor="middle", size=10)
    svg.text((m["l"] + svg.w - m["r"]) / 2, svg.h - 4, "round", anchor="middle", size=10)
    return svg.render()


# 4. Every agent's answer in every round, as a grid: no averaging at all.
def plot_grid(data: dict[str, Any], own: bool) -> str:
    games = games_of(data)
    first = next(iter(games.values()))
    agents = [first["holder"], *[a for a in first["agents"] if a != first["holder"]]]
    rounds = rounds_of(first)
    row, head = 26, 34
    block = head + row * len(agents) + 14
    svg = Svg(800, block * len(games) + 22, f"{RUN}: every agent's answer in every round")
    m = {"l": 130, "r": 80}
    col = (svg.w - m["l"] - m["r"]) / len(rounds)
    for gi, (lb, game) in enumerate(sorted(games.items())):
        y0 = gi * block
        svg.text(0, y0 + 16, COND_NAME[lb].capitalize(), size=12.5, fill="var(--ink)", weight=600)
        for i, r in enumerate(rounds):
            svg.text(m["l"] + col * (i + 0.5), y0 + 30, str(r), anchor="middle", size=10)
        svg.text(svg.w - m["r"] + 12, y0 + 30, "correct", size=10)
        for k, agent in enumerate(agents):
            cy = y0 + head + row * k + row / 2
            name = f"{agent} (holder)" if agent == game["holder"] else agent
            svg.text(m["l"] - 12, cy + 4, name, anchor="end", size=11, fill="var(--ink)")
            svg.line(m["l"], cy, svg.w - m["r"], cy, stroke="var(--grid)")
            shown = correct = 0
            for i, cell in enumerate(game["grid"][agent]):
                if cell is None:
                    continue
                cx = m["l"] + col * (i + 0.5)
                holder = agent == game["holder"]
                excluded = cell["own"] and not own
                what = "own record" if cell["own"] else "directory question"
                if excluded:
                    mark = (
                        f'<text x="{f(cx)}" y="{f(cy + 4)}" text-anchor="middle" font-size="9.5" '
                        f'fill="var(--faint)">own</text>'
                    )
                    title = f"{agent}, round {cell['r']}: own-record question (excluded)"
                else:
                    shown += 1
                    correct += cell["res"] == "correct"
                    if cell["res"] == "correct" and cell["own"]:
                        mark = (
                            f'<circle cx="{f(cx)}" cy="{f(cy)}" r="6" fill="var(--surface)" '
                            f'stroke="var(--ink)" stroke-width="2"/>'
                        )
                    elif cell["res"] == "correct":
                        fill = "var(--ink)" if holder else "var(--s3)"
                        mark = (
                            f'<circle cx="{f(cx)}" cy="{f(cy)}" r="7" fill="{fill}" '
                            f'stroke="var(--surface)" stroke-width="2"/>'
                        )
                    elif cell["res"] == "wrong":
                        mark = (
                            f'<path d="M{f(cx - 5)},{f(cy - 5)} L{f(cx + 5)},{f(cy + 5)} '
                            f'M{f(cx + 5)},{f(cy - 5)} L{f(cx - 5)},{f(cy + 5)}" '
                            f'stroke="var(--crit)" stroke-width="2.5"/>'
                        )
                    else:
                        mark = (
                            f'<rect x="{f(cx - 6)}" y="{f(cy - 6)}" width="12" height="12" rx="2" '
                            f'fill="var(--surface)" stroke="var(--faint)" stroke-width="1.5" '
                            f'stroke-dasharray="2 2"/>'
                        )
                    title = f"{agent}, round {cell['r']}: {cell['res']} on a {what}"
                svg.add(
                    tip(
                        title,
                        f'<rect x="{f(cx - col / 2)}" y="{f(cy - row / 2)}" width="{f(col)}" '
                        f'height="{row}" fill="transparent"/>' + mark,
                    )
                )
            svg.text(svg.w - m["r"] + 12, cy + 4, f"{correct}/{shown}", size=11, fill="var(--ink)")
    return svg.render()


# 5. Seconds from a request to the holder's reply.
def plot_reply_times(data: dict[str, Any]) -> str:
    games = games_of(data)
    svg = Svg(800, 150, f"{RUN}: seconds from each request to the holder's reply")
    m = {"l": 120, "r": 20, "t": 22, "b": 36}
    span = 300
    plot_r = svg.w - m["r"] - 90
    no_reply_x = svg.w - m["r"] - 40
    x = lambda s: m["l"] + (plot_r - m["l"]) * min(s, span) / span  # noqa: E731
    for t in range(0, span + 1, 60):
        svg.line(x(t), m["t"] - 8, x(t), svg.h - m["b"])
        svg.text(x(t), svg.h - m["b"] + 16, f"{t} s", anchor="middle", size=10)
    svg.text(no_reply_x, svg.h - m["b"] + 16, "no reply", anchor="middle", size=10)
    svg.text(
        (m["l"] + plot_r) / 2,
        svg.h - 4,
        "seconds after the request (rounds last 300 s)",
        anchor="middle",
        size=10,
    )
    row_h = (svg.h - m["t"] - m["b"]) / 2
    for i, (lb, game) in enumerate(sorted(games.items())):
        cy = m["t"] + row_h * (i + 0.5)
        svg.text(
            m["l"] - 12,
            cy + 4,
            COND_NAME[lb].capitalize(),
            anchor="end",
            size=11,
            fill="var(--ink)",
        )
        secs = [
            q["seconds_to_answer"]
            for q in game["requests"]
            if q["answered"] and q["seconds_to_answer"] is not None
        ]
        for k, q in enumerate(game["requests"]):
            jitter = (-4, 4)[k % 2]
            if q["answered"] and q["seconds_to_answer"] is not None:
                s = q["seconds_to_answer"]
                mark = (
                    f'<circle cx="{f(x(s))}" cy="{f(cy + jitter)}" r="5" '
                    f'fill="{COND_COLOR[lb]}" stroke="var(--surface)" stroke-width="2"/>'
                )
                title = f"Round {q['round']}, {q['from']} asked: reply after {s:g} s"
            else:
                mark = (
                    f'<circle cx="{f(no_reply_x)}" cy="{f(cy + jitter)}" r="4.5" '
                    f'fill="var(--surface)" stroke="{COND_COLOR[lb]}" stroke-width="2"/>'
                )
                title = f"Round {q['round']}, {q['from']} asked: no reply that round"
            svg.add(tip(title, mark))
        if secs:
            med = statistics.median(secs)
            svg.line(x(med), cy - 13, x(med), cy + 13, stroke="var(--ink)", width=2)
            svg.text(x(med) + 6, cy - 8, f"median {med:.0f} s", size=10, fill="var(--ink)")
    return svg.render()


# name -> (draw, whether it has a directory-only and an all-questions version)
PLOTS: dict[str, tuple[Callable[..., str], bool]] = {
    "mean": (plot_mean, True),
    "cumulative": (plot_cumulative, True),
    "per-round": (plot_per_round, True),
    "grid": (plot_grid, True),
    "reply-times": (plot_reply_times, False),
}


def draw(name: str, data: dict[str, Any]) -> str:
    fn, variants = PLOTS[name]
    if not variants:
        return fn(data)
    return f'<div class="v-dir">{fn(data, False)}</div><div class="v-all">{fn(data, True)}</div>'


def fill(page: str, data: dict[str, Any]) -> str:
    for name in PLOTS:
        pattern = re.compile(rf"(<!--plot:{name}-->).*?(<!--/plot:{name}-->)", re.S)
        if not pattern.search(page):
            raise ValueError(f"page has no <!--plot:{name}--> slot")
        chart = draw(name, data)
        page = pattern.sub(lambda mt, s=chart: mt.group(1) + s + mt.group(2), page)
    return page


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--page", type=Path, default=PAGE)
    args = parser.parse_args()
    data = json.loads(args.data.read_text())
    args.page.write_text(fill(args.page.read_text(), data))
    print(f"drew {len(PLOTS)} charts into {args.page}")


if __name__ == "__main__":
    main()
