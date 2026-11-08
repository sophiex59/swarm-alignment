"""Summaries of live-mode game logs, for the experiment notebook and quick checks.

uv run python -m swarm_alignment.analysis results/DIR
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

TOOL_ORDER = [
    "post_message",
    "read_messageboard",
    "read_leaderboard",
    "check_actions",
    "write_note",
    "read_notes",
    "submit_answer",
    "lookup_directory",
    "end_round",
]


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def load_dir(directory: Path) -> dict[str, list[dict[str, Any]]]:
    """Finished game logs in a directory, keyed by file name."""
    games = {}
    for path in sorted(Path(directory).glob("*.jsonl")):
        events = load(path)
        if events and events[-1]["type"] == "end":
            games[path.name] = events
    return games


def config(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(event for event in events if event["type"] == "config")


def round_table(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per round and agent: correct, answer kind, and whether it was own-record."""
    cfg = config(events)
    rows = []
    for event in events:
        if event["type"] != "round_score":
            continue
        for agent, detail in event["per_agent"].items():
            answer = detail["answer"]
            rows.append(
                {
                    "round": event["round"],
                    "agent": agent,
                    "holder": agent == cfg["privileged_agent"],
                    "own_record": detail["own_record"],
                    "correct": detail["correct"],
                    "result": "correct"
                    if detail["correct"]
                    else ("unknown" if answer in (None, "unknown") else "wrong"),
                }
            )
    return rows


def tool_counts(events: list[dict[str, Any]]) -> dict[str, Counter]:
    """Per agent: tool calls by name, plus text-only replies and rejected duplicate posts."""
    cfg = config(events)
    counts = {agent: Counter() for agent in cfg["agents"]}
    for event in events:
        if event["type"] == "tool" and event["agent"] in counts:
            counts[event["agent"]][event["tool"]] += 1
        elif event["type"] == "model_call" and event["agent"] in counts:
            if (event.get("content") or "").strip() and not event.get("tool_calls"):
                counts[event["agent"]]["(text-only reply)"] += 1
        elif event["type"] == "duplicate_post":
            counts[event["agent"]]["(duplicate rejected)"] += 1
    return counts


def requests_to_holder(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Posts by non-holders that @mention the holder, with whether the holder was still acting."""
    cfg = config(events)
    holder = cfg["privileged_agent"]
    last_step = {}
    for event in events:
        if event["type"] == "model_call" and event["agent"] == holder:
            last_step[event["round"]] = max(last_step.get(event["round"], 0), event["tick"])
    out = []
    for event in events:
        if event["type"] == "post" and event["agent"] != holder and holder in event["mentions"]:
            out.append(
                {
                    "round": event["round"],
                    "tick": event["tick"],
                    "from": event["agent"],
                    "text": event["stored_text"],
                    "holder_still_active": event["tick"] < last_step.get(event["round"], 0),
                }
            )
    return out


def summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = config(events)
    holder = cfg["privileged_agent"]
    rows = round_table(events)
    others = [r for r in rows if not r["holder"]]
    directory_q = [r for r in others if not r["own_record"]]
    own_q = [r for r in others if r["own_record"]]
    holder_rows = [r for r in rows if r["holder"]]
    ends = [e for e in events if e["type"] == "round_end"]
    unspent = [v for e in ends for v in e["unspent"].values()]
    calls = [e for e in events if e["type"] == "model_call"]
    requests = requests_to_holder(events)
    probes = {e["agent"]: e.get("verdict", "?") for e in events if e["type"] == "probe"}
    return {
        "lb": cfg["lb"],
        "seed": cfg["seed"],
        "rounds": cfg["rounds"],
        "budget": cfg.get("budget"),
        "holder": holder,
        "holder_correct": f"{sum(r['correct'] for r in holder_rows)}/{len(holder_rows)}",
        "non_holder_directory_correct": (
            f"{sum(r['correct'] for r in directory_q)}/{len(directory_q)}"
        ),
        "non_holder_own_record_correct": f"{sum(r['correct'] for r in own_q)}/{len(own_q)}",
        "requests_to_holder": len(requests),
        "requests_while_holder_active": sum(r["holder_still_active"] for r in requests),
        "first_request_round": min((r["round"] for r in requests), default=None),
        "mean_unspent_actions": round(sum(unspent) / len(unspent), 1) if unspent else None,
        "duplicate_posts_rejected": sum(e["type"] == "duplicate_post" for e in events),
        "model_calls": len(calls),
        "prompt_tokens": sum(c.get("prompt_tokens", 0) for c in calls),
        "completion_tokens": sum(c.get("completion_tokens", 0) for c in calls),
        "wall_seconds": events[-1].get("wall_seconds"),
        "probe_verdicts": probes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    for name, events in load_dir(args.directory).items():
        print(name)
        for key, value in summary(events).items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
