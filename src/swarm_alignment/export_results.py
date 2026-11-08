"""Export game logs to one compact JSON file for the results explorer page.

uv run python -m swarm_alignment.export_results --out results/explorer/data.json

Reads every run listed in RUNS (finished .jsonl logs, plus .jsonl.part logs of games still
running) and writes per-game summaries: per-round, per-agent outcomes, requests to the holder,
tool use, timings and probe answers. Raw transcripts are not included.
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from swarm_alignment import analysis

# One entry per run, oldest first. "design" says what was different about the run.
RUNS: list[dict[str, Any]] = [
    {
        "id": "live-smoke",
        "label": "Run 1",
        "date": "2026-11-08",
        "commit": "e44178d",
        "design": "Live board, 30-action budget, reply text auto-posted, thinking off",
    },
    {
        "id": "live-smoke-2",
        "label": "Run 2",
        "date": "2026-11-08",
        "commit": "013a8c7",
        "design": "Run 1 plus fixes: unread tracking, own-question rule, duplicates, probe",
    },
    {
        "id": "live-smoke-3",
        "label": "Run 3",
        "date": "2026-11-08",
        "commit": "c1fde42",
        "design": "Thinking on; reply text and reasoning private; only post_message is public",
    },
    {
        "id": "live-smoke-4",
        "label": "Run 4",
        "date": "2026-11-08",
        "commit": "15e298e",
        "design": "Rounds of up to 8 laps, no budget, mentions inbox, wait to stay reachable",
    },
    {
        "id": "live-smoke-5-async",
        "label": "Run 5",
        "date": "2026-11-08",
        "commit": "1483114",
        "design": "Asynchronous islands: each agent its own loop, 5 minutes per round",
    },
]

TOOLS = [*analysis.TOOL_ORDER, "wait"]


def game_record(events: list[dict[str, Any]], status: str) -> dict[str, Any]:
    cfg = analysis.config(events)
    holder = cfg["privileged_agent"]
    agents = cfg["agents"]
    rows = analysis.round_table(events)
    rounds_done = sorted({r["round"] for r in rows})
    grid = {
        agent: [
            next(
                (
                    {"r": r["round"], "res": r["result"], "own": r["own_record"]}
                    for r in rows
                    if r["agent"] == agent and r["round"] == n
                ),
                None,
            )
            for n in rounds_done
        ]
        for agent in agents
    }
    requests = analysis.requests_to_holder(events)
    counts = analysis.tool_counts(events)
    tool_use = {
        agent: {name: counts[agent].get(name, 0) for name in TOOLS if counts[agent].get(name)}
        for agent in agents
    }
    ends = {e["round"]: e for e in events if e["type"] == "round_end"}
    posts = Counter(e["round"] for e in events if e["type"] == "post" and e.get("via") == "tool")
    # Non-holder posts that address at least one teammate, and how many of those name the holder.
    addressed = [
        e
        for e in events
        if e["type"] == "post"
        and e.get("via") in ("tool", "content")
        and e["agent"] in agents
        and e["agent"] != holder
        and any(m != e["agent"] for m in e["mentions"])
    ]
    per_round = []
    for n in rounds_done:
        r_rows = [r for r in rows if r["round"] == n]
        directory = [r for r in r_rows if not r["holder"] and not r["own_record"]]
        reqs = [q for q in requests if q["round"] == n]
        end = ends.get(n, {})
        per_round.append(
            {
                "r": n,
                "dir_correct": sum(r["correct"] for r in directory),
                "dir_total": len(directory),
                "correct": sum(r["correct"] for r in r_rows),
                "requests": len(reqs),
                "answered": sum(q["answered"] for q in reqs),
                "posts": posts.get(n, 0),
                "addressed": sum(e["round"] == n for e in addressed),
                "to_holder": sum(e["round"] == n and holder in e["mentions"] for e in addressed),
                "laps": end.get("laps") if isinstance(end.get("laps"), int) else None,
                "seconds": end.get("seconds"),
            }
        )
    summary = analysis.summary(events) if status == "finished" else None
    model_params = cfg.get("model_params") or {}
    return {
        "lb": cfg["lb"],
        "seed": cfg["seed"],
        "status": status,
        "rounds_planned": cfg["rounds"],
        "rounds_done": len(rounds_done),
        "holder": holder,
        "agents": agents,
        "settings": {
            "turn_order": cfg.get("turn_order") or cfg.get("laps", "sequential"),
            "laps": cfg.get("laps") if isinstance(cfg.get("laps"), int) else None,
            "budget": cfg.get("budget"),
            "round_seconds": cfg.get("round_seconds"),
            "thinking": bool(model_params.get("thinking")),
        },
        "grid": grid,
        "per_round": per_round,
        "requests": [
            {k: q[k] for k in ("round", "from", "answered", "laps_to_answer", "seconds_to_answer")}
            for q in requests
        ],
        "tool_use": tool_use,
        "probes": [
            {"agent": e["agent"], "verdict": e.get("verdict"), "text": (e.get("text") or "")[:240]}
            for e in events
            if e["type"] == "probe"
        ],
        "summary": summary,
    }


def export(root: Path) -> dict[str, Any]:
    runs = []
    for run in RUNS:
        directory = root / run["id"]
        games = []
        for path in sorted(directory.glob("*.jsonl")):
            events = analysis.load(path)
            if events and events[-1]["type"] == "end":
                games.append(game_record(events, "finished"))
        finished = {(g["lb"], g["seed"]) for g in games}
        for path in sorted(directory.glob("*.jsonl.part")):
            events = analysis.load(path)
            if events and events[0]["type"] == "config":
                cfg = events[0]
                if (cfg["lb"], cfg["seed"]) not in finished:
                    games.append(game_record(events, "running"))
        if games:
            runs.append({**run, "games": sorted(games, key=lambda g: (g["lb"], g["seed"]))})
    return {"runs": runs}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--out", type=Path, default=Path("results/explorer/data.json"))
    args = parser.parse_args()
    data = export(args.results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, separators=(",", ":")))
    games = sum(len(r["games"]) for r in data["runs"])
    print(f"wrote {args.out}: {len(data['runs'])} runs, {games} games")


if __name__ == "__main__":
    main()
