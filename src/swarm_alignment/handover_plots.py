"""Draw the handover page's charts from the run summaries and write them into docs/handover.html.

uv run python -m swarm_alignment.export_results    # refresh results/explorer/data.json first
uv run python -m swarm_alignment.handover_plots

Each chart is inline SVG between <!--plot:NAME--> and <!--/plot:NAME--> markers, so the page
stays one standalone file. Colours are CSS variables defined by the page, so the charts follow
its light and dark themes. Hovering a mark shows its exact numbers (SVG <title>).
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

COND_COLOR = {"off": "var(--bar)", "on": "var(--bar-2)"}
COND_NAME = {"off": "leaderboard off", "on": "leaderboard on"}
AGENT_COLORS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"]
POINTS = {"correct": 1, "wrong": -1, "unknown": 0}
ROLL = 3


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


def games_of(data: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    run = next(r for r in data["runs"] if r["label"] == label)
    return {g["lb"]: g for g in run["games"] if g["status"] == "finished"}


def totals(game: dict[str, Any], *keys: str) -> list[int]:
    return [sum(p[k] for p in game["per_round"]) for k in keys]


def line_chart_frame(svg: Svg, m: dict[str, int], rounds: int, y_ticks, y_lo, y_hi, fmt_tick):
    x = lambda r: m["l"] + (svg.w - m["l"] - m["r"]) * (r - 1) / (rounds - 1)  # noqa: E731
    y = lambda v: m["t"] + (svg.h - m["t"] - m["b"]) * (y_hi - v) / (y_hi - y_lo)  # noqa: E731
    for t in y_ticks:
        svg.line(m["l"], y(t), svg.w - m["r"], y(t))
        svg.text(m["l"] - 8, y(t) + 4, fmt_tick(t), anchor="end", size=10)
    for r in range(1, rounds + 1):
        svg.text(x(r), svg.h - m["b"] + 16, str(r), anchor="middle", size=10)
    svg.text((m["l"] + svg.w - m["r"]) / 2, svg.h - 4, "round", anchor="middle", size=10)
    return x, y


def path(points: list[tuple[float, float]]) -> str:
    return "M" + " L".join(f"{f(a)},{f(b)}" for a, b in points)


# 2. Run 6: cumulative share of directory questions solved, one line per game.
def plot_cumulative(data: dict[str, Any]) -> str:
    svg = Svg(800, 270, "Run 6: cumulative share of directory questions solved, per game")
    m = {"l": 46, "r": 150, "t": 14, "b": 36}
    x, y = line_chart_frame(svg, m, 10, (0, 0.25, 0.5, 0.75, 1), 0, 1, pct)
    labels = []
    for lb, game in sorted(games_of(data, "Run 6").items()):
        solved = asked = 0
        pts = []
        for p in game["per_round"]:
            solved += p["dir_correct"]
            asked += p["dir_total"]
            pts.append((p["r"], solved, asked))
        color = COND_COLOR[lb]
        dash = ' stroke-dasharray="6 4"' if lb == "on" else ""
        svg.add(
            f'<path d="{path([(x(r), y(s / a)) for r, s, a in pts])}" stroke="{color}" '
            f'stroke-width="2" fill="none"{dash}/>'
        )
        for r, s, a in pts:
            svg.add(
                tip(
                    f"Leaderboard {lb}, round {r}: {s} of {a} solved so far ({pct(s / a)})",
                    f'<circle cx="{f(x(r))}" cy="{f(y(s / a))}" r="4" fill="{color}" '
                    f'stroke="var(--surface)" stroke-width="1.5"/>'
                    f'<circle cx="{f(x(r))}" cy="{f(y(s / a))}" r="9" fill="transparent"/>',
                )
            )
        r, s, a = pts[-1]
        labels.append((y(s / a), f"Leaderboard {lb}: {s}/{a} ({pct(s / a)})", color, lb))
    ys = dodge([v for v, *_ in labels], 15, m["t"] + 4, svg.h - m["b"])
    for (_, text, color, lb), ly in zip(labels, ys, strict=True):
        dash = ' stroke-dasharray="4 3"' if lb == "on" else ""
        svg.add(
            f'<line x1="{f(x(10) + 8)}" y1="{f(ly)}" x2="{f(x(10) + 22)}" y2="{f(ly)}" '
            f'stroke="{color}" stroke-width="2"{dash}/>'
        )
        svg.text(x(10) + 27, ly + 4, text, size=11, fill="var(--ink)")
    return svg.render()


ROUND_BARS = [
    ("dir_total", "directory questions", "var(--faint)"),
    ("requests", "requests to Dune", "var(--s1)"),
    ("dir_correct", "solved", "var(--s3)"),
]


# 3. Run 6, each round: directory questions, requests to the holder, and solves (games pooled).
def plot_per_round(data: dict[str, Any]) -> str:
    games = games_of(data, "Run 6")
    svg = Svg(800, 230, "Run 6, each round: directory questions, requests to the holder, solves")
    m = {"l": 46, "r": 20, "t": 14, "b": 36}
    w = svg.w - m["l"] - m["r"]
    rounds = [p["r"] for p in games["off"]["per_round"]]
    pooled = {
        r: {
            key: {
                lb: next(p for p in g["per_round"] if p["r"] == r)[key] for lb, g in games.items()
            }
            for key, _, _ in ROUND_BARS
        }
        for r in rounds
    }
    top_value = max(sum(v.values()) for row in pooled.values() for v in row.values())
    top_value += top_value % 2
    yv = lambda v: m["t"] + (svg.h - m["t"] - m["b"]) * (1 - v / top_value)  # noqa: E731
    for t in range(0, top_value + 1, 2):
        svg.line(m["l"], yv(t), svg.w - m["r"], yv(t))
        svg.text(m["l"] - 8, yv(t) + 4, str(t), anchor="end", size=10)
    band = w / len(rounds)
    bw, gap = 14, 2
    for i, r in enumerate(rounds):
        cx = m["l"] + band * (i + 0.5)
        for j, (key, name, color) in enumerate(ROUND_BARS):
            per_game = pooled[r][key]
            v = sum(per_game.values())
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
            detail = ", ".join(f"{COND_NAME[lb]} {n}" for lb, n in sorted(per_game.items()))
            svg.add(
                tip(
                    f"Round {r}: {v} {name} ({detail})",
                    bar + f'<rect x="{f(bx - 1)}" y="{m["t"]}" width="{bw + 2}" '
                    f'height="{f(base - m["t"])}" fill="transparent"/>',
                )
            )
        svg.text(cx, svg.h - m["b"] + 16, str(r), anchor="middle", size=10)
    svg.text((m["l"] + svg.w - m["r"]) / 2, svg.h - 4, "round", anchor="middle", size=10)
    return svg.render()


def rolling(cells: list[dict[str, Any] | None]) -> list[tuple[int, float, float, dict]]:
    out = []
    for k, cell in enumerate(cells):
        if cell is None:
            continue
        win = [POINTS.get(c["res"], 0) for c in cells[max(0, k - ROLL + 1) : k + 1] if c]
        mean = sum(win) / len(win)
        se = statistics.stdev(win) / len(win) ** 0.5 if len(win) > 1 else 0.0
        out.append((cell["r"], mean, se, cell))
    return out


# 4. Run 6, each agent: average score over the last 3 rounds, one panel per game.
def plot_agents(data: dict[str, Any], lb: str) -> str:
    game = games_of(data, "Run 6")[lb]
    holder = game["holder"]
    any_wrong = any(c and c["res"] == "wrong" for row in game["grid"].values() for c in row)
    lo = -1 if any_wrong else 0
    svg = Svg(
        800,
        250,
        f"Run 6, {COND_NAME[lb]}: each agent's average score over the last "
        f"{ROLL} rounds, with ±1 standard error",
    )
    m = {"l": 46, "r": 170, "t": 30, "b": 36}
    ticks = (-1, -0.5, 0, 0.5, 1) if lo < 0 else (0, 0.5, 1)
    x, y = line_chart_frame(
        svg, m, len(game["per_round"]), ticks, lo - 0.08, 1.08, lambda t: f"{t:+g}" if t else "0"
    )
    svg.text(m["l"], 16, f"Leaderboard {lb}", size=12.5, fill="var(--ink)", weight=600)
    others = [a for a in game["agents"] if a != holder]
    colors = {a: AGENT_COLORS[i] for i, a in enumerate(others)} | {holder: "var(--ink)"}
    series = {a: rolling(game["grid"][a]) for a in game["agents"]}
    for a in others:  # bands first, under every line
        pts = series[a]
        upper = [(x(r), y(min(1, v + se))) for r, v, se, _ in pts]
        lower = [(x(r), y(max(lo, v - se))) for r, v, se, _ in reversed(pts)]
        svg.add(f'<path d="{path(upper + lower)} Z" fill="{colors[a]}" fill-opacity="0.1"/>')
    labels = []
    for a in [holder, *others]:
        pts, color = series[a], colors[a]
        svg.add(
            f'<path d="{path([(x(r), y(v)) for r, v, _, _ in pts])}" stroke="{color}" '
            f'stroke-width="2" fill="none"/>'
        )
        for r, v, se, cell in pts:
            cx, cy = x(r), y(v)
            what = "own record" if cell["own"] else "directory question"
            if cell["res"] == "correct" and not cell["own"]:
                mark = (
                    f'<circle cx="{f(cx)}" cy="{f(cy)}" r="4.5" fill="{color}" '
                    f'stroke="var(--surface)" stroke-width="2"/>'
                )
            elif cell["res"] == "correct":
                mark = (
                    f'<circle cx="{f(cx)}" cy="{f(cy)}" r="4" fill="var(--surface)" '
                    f'stroke="{color}" stroke-width="2"/>'
                )
            elif cell["res"] == "wrong":
                mark = (
                    f'<path d="M{f(cx - 4)},{f(cy - 4)} L{f(cx + 4)},{f(cy + 4)} '
                    f'M{f(cx + 4)},{f(cy - 4)} L{f(cx - 4)},{f(cy + 4)}" stroke="var(--crit)" '
                    f'stroke-width="2"/>'
                )
            else:
                mark = ""
            svg.add(
                tip(
                    f"{a}, round {r}: {cell['res']} on a {what}. Average of the last "
                    f"{min(ROLL, r)} rounds {v:+.2f} ± {se:.2f}",
                    mark + f'<circle cx="{f(cx)}" cy="{f(cy)}" r="9" fill="transparent"/>',
                )
            )
        correct = sum(1 for c in game["grid"][a] if c and c["res"] == "correct")
        name = f"{a} (holder)" if a == holder else a
        labels.append((y(pts[-1][1]), f"{name} · {correct}/{len(pts)} correct", color))
    ys = dodge([v for v, *_ in labels], 15, m["t"], svg.h - m["b"])
    end = x(len(game["per_round"]))
    for (_, text, color), ly in zip(labels, ys, strict=True):
        svg.add(f'<circle cx="{f(end + 14)}" cy="{f(ly)}" r="4" fill="{color}"/>')
        svg.text(end + 23, ly + 4, text, size=11, fill="var(--ink)")
    return svg.render()


# 5. Run 6: seconds from a request to the holder's reply.
def plot_reply_times(data: dict[str, Any]) -> str:
    games = games_of(data, "Run 6")
    svg = Svg(800, 150, "Run 6: seconds from each request to the holder's reply")
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


PLOTS: dict[str, Callable[[dict[str, Any]], str]] = {
    "cumulative": plot_cumulative,
    "per-round": plot_per_round,
    "agents-off": lambda d: plot_agents(d, "off"),
    "agents-on": lambda d: plot_agents(d, "on"),
    "reply-times": plot_reply_times,
}


def fill(page: str, data: dict[str, Any]) -> str:
    for name, draw in PLOTS.items():
        pattern = re.compile(rf"(<!--plot:{name}-->).*?(<!--/plot:{name}-->)", re.S)
        if not pattern.search(page):
            raise ValueError(f"page has no <!--plot:{name}--> slot")
        chart = draw(data)
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
