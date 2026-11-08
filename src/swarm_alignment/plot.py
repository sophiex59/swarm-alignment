"""Plot learning curves from Discovery game logs: accuracy per round, one line per agent.

uv run --group plot python -m swarm_alignment.plot results/DIR [--lb off] [--window 3] [--out f.png]

One game: one line per agent (the holder in orange). Several games: the holder's mean and the
non-holders' mean, with each game's non-holders faint behind. The dotted line is the accuracy an
agent gets without help (the share of its questions about its own record); the dashed line is 1.
"""

import argparse
import json
from pathlib import Path
from typing import Any

HOLDER_COLOR = "#e8912d"
OTHER_COLORS = ["#d64545", "#3a6fc4", "#4a9d4f", "#8a5fc4", "#888888", "#2aa5a5", "#b0762f"]


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def rolling(values: list[float], window: int) -> list[float]:
    out = []
    for index in range(len(values)):
        chunk = values[max(0, index - window + 1) : index + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def curves(events: list[dict[str, Any]], window: int) -> dict[str, Any]:
    """Per-agent rolling accuracy by round, plus who the holder is and the no-help baseline."""
    config = next(event for event in events if event["type"] == "config")
    scores = sorted(
        (event for event in events if event["type"] == "round_score"), key=lambda e: e["round"]
    )
    agents = config["agents"]
    per_agent = {
        agent: rolling([float(s["per_agent"][agent]["correct"]) for s in scores], window)
        for agent in agents
    }
    own_rate = {
        agent: sum(s["per_agent"][agent]["own_record"] for s in scores) / len(scores)
        for agent in agents
    }
    return {
        "holder": config["privileged_agent"],
        "agents": agents,
        "curves": per_agent,
        "own_rate": own_rate,
        "rounds": [s["round"] for s in scores],
    }


def mean(series: list[list[float]]) -> list[float]:
    return [sum(values) / len(values) for values in zip(*series, strict=True)]


def plot(games: list[dict[str, Any]], title: str, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 5.5))
    rounds = games[0]["rounds"]
    if len(games) == 1:
        game = games[0]
        others = [a for a in game["agents"] if a != game["holder"]]
        ax.plot(
            rounds,
            game["curves"][game["holder"]],
            color=HOLDER_COLOR,
            lw=2.5,
            label=f"{game['holder']} (holds the directory)",
        )
        for color, agent in zip(OTHER_COLORS, others, strict=False):
            ax.plot(rounds, game["curves"][agent], color=color, lw=2, label=agent)
        baseline = sum(game["own_rate"][a] for a in others) / len(others)
    else:
        holder_curves = [g["curves"][g["holder"]] for g in games]
        other_curves = [
            mean([g["curves"][a] for a in g["agents"] if a != g["holder"]]) for g in games
        ]
        for curve in other_curves:
            ax.plot(rounds, curve, color="#3a6fc4", lw=0.8, alpha=0.25)
        ax.plot(
            rounds,
            mean(holder_curves),
            color=HOLDER_COLOR,
            lw=2.5,
            label=f"holder (mean of {len(games)} games)",
        )
        ax.plot(rounds, mean(other_curves), color="#3a6fc4", lw=2.5, label="non-holders (mean)")
        baseline = sum(
            g["own_rate"][a] for g in games for a in g["agents"] if a != g["holder"]
        ) / sum(len(g["agents"]) - 1 for g in games)
    ax.axhline(1.0, color="black", lw=1, ls="--", label="perfect")
    ax.axhline(baseline, color="gray", lw=1, ls=":", label="no help (own-record questions only)")
    ax.set_xlabel("Rounds")
    ax.set_ylabel("Correct (rolling accuracy)")
    ax.set_ylim(-0.02, 1.05)
    ax.set_title(title)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=150)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--lb", default=None, help="only logs with this leaderboard condition")
    parser.add_argument("--window", type=int, default=3, help="rolling window in rounds")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    games = []
    for path in sorted(args.directory.glob("*.jsonl")):
        events = load(path)
        config = next((e for e in events if e["type"] == "config"), None)
        if config is None or not any(e["type"] == "round_score" for e in events):
            continue
        if args.lb and config["lb"] != args.lb:
            continue
        games.append(curves(events, args.window))
    if not games:
        raise SystemExit("no finished games found")
    out = args.out or args.directory / f"curves{'-' + args.lb if args.lb else ''}.png"
    plot(games, f"Discovery: accuracy by round ({len(games)} game(s))", out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
