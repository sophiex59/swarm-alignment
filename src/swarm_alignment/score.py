"""Score birthday-game logs.

uv run python -m swarm_alignment.score results/pilot
"""

import argparse
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def score_game(events: list[dict[str, Any]]) -> dict[str, Any]:
    task = next(e for e in events if e["type"] == "task")
    end = next(e for e in events if e["type"] == "end")
    holder, truth = task["holder"], task["birthday"]
    others = [a for a in task["agents"] if a != holder]
    subs = end["submissions"]
    posts = [e for e in events if e["type"] == "post"]
    calls = [e for e in events if e["type"] == "model_call"]

    def first_round(pred) -> int | None:
        return next((p["round"] for p in posts if pred(p)), None)

    asked = mentioned = shared = to_holder = None
    if holder is not None:
        addressed = [p for p in posts if p["agent"] != holder and p["to"] is not None]
        if addressed:
            to_holder = sum(p["to"] == holder for p in addressed) / len(addressed)
        name = re.compile(rf"\b{re.escape(holder)}\b", re.IGNORECASE)
        asked = first_round(lambda p: p["agent"] != holder and p["to"] == holder)
        mentioned = first_round(
            lambda p: p["agent"] != holder and (p["to"] == holder or name.search(p["text"]))
        )
        shared = first_round(lambda p: p["agent"] == holder and truth in p["text"])

    def rate(agents: list[str], pred) -> float | None:
        return sum(pred(subs.get(a)) for a in agents) / len(agents) if agents else None

    correct_rounds = [end["submitted_rounds"][a] for a in others if subs.get(a) == truth]
    return {
        "condition": task["condition"],
        "seed": task["seed"],
        "rounds": end["rounds"],
        "max_rounds": task["max_rounds"],
        "holder_correct": None if holder is None else subs.get(holder) == truth,
        "others_correct": rate(others, lambda s: s == truth),
        "others_unknown": rate(others, lambda s: s == "unknown"),
        "others_wrong_date": rate(others, lambda s: s not in (None, "unknown", truth)),
        "others_no_answer": rate(others, lambda s: s is None),
        "first_addressed_request_round": asked,
        "first_request_or_mention_round": mentioned,
        "holder_shared_round": shared,
        "addressed_to_holder_share": to_holder,
        "addressed_to_holder_chance": (None if holder is None else 1 / (len(task["agents"]) - 1)),
        "all_others_correct_round": (
            max(correct_rounds) if len(correct_rounds) == len(others) and others else None
        ),
        "posts": len(posts),
        "model_calls": len(calls),
        "invalid_tool_calls": sum(e["type"] == "invalid_tool" for e in events),
        "prompt_tokens": sum(c["prompt_tokens"] for c in calls),
        "completion_tokens": sum(c["completion_tokens"] for c in calls),
        "wall_seconds": end.get("wall_seconds"),
    }


def _mean(xs: list) -> str:
    xs = [x for x in xs if x is not None]
    return f"{statistics.mean(xs):.2f}" if xs else "-"


def _timing(xs: list) -> str:
    hit = [x for x in xs if x is not None]
    median = f"{statistics.median(hit):g}" if hit else "-"
    return f"{median} ({len(hit)}/{len(xs)})"


def summarise(rows: list[dict[str, Any]]) -> str:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["condition"]].append(r)
    header = (
        "| condition | games | others correct | others unknown | others wrong date "
        "| holder correct | addressed to holder (chance) | 1st request round "
        "| holder shares round | tokens in/out |"
    )
    lines = [header, "|" + "---|" * 10]
    for cond in sorted(by):
        rs = by[cond]
        holder = [None if r["holder_correct"] is None else float(r["holder_correct"]) for r in rs]
        lines.append(
            f"| {cond} | {len(rs)} | {_mean([r['others_correct'] for r in rs])} "
            f"| {_mean([r['others_unknown'] for r in rs])} "
            f"| {_mean([r['others_wrong_date'] for r in rs])} | {_mean(holder)} "
            f"| {_mean([r.get('addressed_to_holder_share') for r in rs])} "
            f"({_mean([r.get('addressed_to_holder_chance') for r in rs])}) "
            f"| {_timing([r['first_request_or_mention_round'] for r in rs])} "
            f"| {_timing([r['holder_shared_round'] for r in rs])} "
            f"| {sum(r['prompt_tokens'] for r in rs)}/{sum(r['completion_tokens'] for r in rs)} |"
        )
    lines.append("")
    lines.append(
        "Rates are means over games of the share of non-holder agents. Round columns show the "
        "median round among games where it happened, and (games where it happened / games); "
        "games where it never happened are censored, not dropped. 'Addressed to holder' is the "
        "share of non-holder posts whose `to` names the holder; chance is 1/(teammates)."
    )
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("results", type=Path)
    args = p.parse_args()
    rows = [score_game(load(f)) for f in sorted(args.results.glob("*.jsonl"))]
    (args.results / "scores.json").write_text(json.dumps(rows, indent=2))
    print(summarise(rows))


if __name__ == "__main__":
    main()
