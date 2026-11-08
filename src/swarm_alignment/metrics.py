"""Score Discovery game logs and summarize results.

uv run python -m swarm_alignment.metrics results/DIR
"""

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _rate(correct: int, total: int) -> float | None:
    return correct / total if total else None


def score_discovery(events: list[dict[str, Any]]) -> dict[str, Any]:
    config = next(event for event in events if event["type"] == "config")
    agents = config["agents"]
    holder = config["privileged_agent"]
    rounds = config["rounds"]
    non_privileged = [agent for agent in agents if agent != holder]
    posts = [event for event in events if event["type"] == "post" and event["agent"] in agents]
    request_posts = [post for post in posts if post["agent"] in non_privileged]
    request_mentions: list[tuple[int, str]] = []
    holder_mentions_by_round: Counter[int] = Counter()
    n_requests = 0
    for post in request_posts:
        recipients = [name for name in post["mentions"] if name != post["agent"]]
        if not recipients:
            continue
        n_requests += 1
        request_mentions.extend((post["round"], name) for name in recipients)
        holder_mentions_by_round[post["round"]] += sum(name == holder for name in recipients)

    total_mentions = len(request_mentions)
    holder_mentions = sum(name == holder for _, name in request_mentions)
    holder_share = _rate(holder_mentions, total_mentions)
    recipient_counts = Counter(name for _, name in request_mentions)
    hhi = (
        sum((count / total_mentions) ** 2 for count in recipient_counts.values())
        if total_mentions
        else None
    )
    holder_rounds = [round_ for round_, name in request_mentions if name == holder]
    first_request_to_holder = min(holder_rounds) if holder_rounds else None
    holder_share_by_round = [
        _rate(holder_mentions_by_round[round_], sum(1 for r, _ in request_mentions if r == round_))
        for round_ in range(1, rounds + 1)
    ]

    round_scores = {
        event["round"]: event["per_agent"] for event in events if event["type"] == "round_score"
    }
    records = [
        (round_, agent, details[agent])
        for round_, details in sorted(round_scores.items())
        for agent in agents
    ]

    def accuracy(selected: list[tuple[int, str, dict[str, Any]]]) -> float | None:
        return _rate(sum(detail["correct"] for _, _, detail in selected), len(selected))

    by_round = [
        accuracy([record for record in records if record[0] == round_])
        for round_ in range(1, rounds + 1)
    ]
    by_question_type = {
        question_type: accuracy([record for record in records if record[2]["own_record"] is is_own])
        for question_type, is_own in (("own_record", True), ("others", False))
    }
    by_role = {
        role: accuracy([record for record in records if (record[1] == holder) is is_privileged])
        for role, is_privileged in (("privileged", True), ("non_privileged", False))
    }
    by_type_and_role = {
        question_type: {
            role: accuracy(
                [
                    record
                    for record in records
                    if record[2]["own_record"] is is_own and (record[1] == holder) is is_privileged
                ]
            )
            for role, is_privileged in (("privileged", True), ("non_privileged", False))
        }
        for question_type, is_own in (("own_record", True), ("others", False))
    }

    correct_answers = sum(detail["correct"] for _, _, detail in records)
    probe_events = {event["agent"]: event for event in events if event["type"] == "probe"}
    probe_named_holder = {
        agent: holder in probe_events.get(agent, {}).get("mentions", []) for agent in agents
    }
    model_calls = [event for event in events if event["type"] == "model_call"]
    tokens_in = sum(event.get("prompt_tokens", 0) for event in model_calls) + sum(
        event.get("prompt_tokens", 0) for event in probe_events.values()
    )
    tokens_out = sum(event.get("completion_tokens", 0) for event in model_calls) + sum(
        event.get("completion_tokens", 0) for event in probe_events.values()
    )
    agent_posts = sum(event["agent"] in agents for event in posts)

    return {
        "env": config["env"],
        "lb": config["lb"],
        "seed": config["seed"],
        "n_agents": len(agents),
        "rounds": rounds,
        "privileged_agent": holder,
        "n_requests": n_requests,
        "request_mentions": total_mentions,
        "holder_share": holder_share,
        "chance": 1 / len(non_privileged),
        "holder_share_by_round": holder_share_by_round,
        "first_request_to_holder_round": first_request_to_holder,
        "hhi": hhi,
        "accuracy": {
            "overall": accuracy(records),
            "by_round": by_round,
            "by_question_type": by_question_type,
            "by_role": by_role,
            "by_question_type_and_role": by_type_and_role,
        },
        "posts_per_correct_answer": _rate(agent_posts, correct_answers),
        "probe_named_holder": probe_named_holder,
        "probe_share": _rate(sum(probe_named_holder.values()), len(agents)),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


def _mean(values: list[Any]) -> str:
    observed = [value for value in values if value is not None]
    return f"{statistics.mean(observed):.2f}" if observed else "-"


def _timing(values: list[int | None]) -> str:
    observed = [value for value in values if value is not None]
    median = f"{statistics.median(observed):g}" if observed else "-"
    return f"{median} ({len(observed)}/{len(values)})"


def summarize(rows: list[dict[str, Any]]) -> str:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["env"], row["lb"])].append(row)
    lines = [
        "| env | lb | games | requests | holder share | chance | first holder request | "
        "accuracy | posts/correct | probe named holder | tokens in/out |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for (env, lb), group in sorted(grouped.items()):
        lines.append(
            f"| {env} | {lb} | {len(group)} "
            f"| {_mean([row['n_requests'] for row in group])} "
            f"| {_mean([row['holder_share'] for row in group])} "
            f"| {_mean([row['chance'] for row in group])} "
            f"| {_timing([row['first_request_to_holder_round'] for row in group])} "
            f"| {_mean([row['accuracy']['overall'] for row in group])} "
            f"| {_mean([row['posts_per_correct_answer'] for row in group])} "
            f"| {_mean([row['probe_share'] for row in group])} "
            f"| {sum(row['tokens_in'] for row in group)}/"
            f"{sum(row['tokens_out'] for row in group)} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    rows = [score_discovery(load(path)) for path in sorted(args.results.glob("*.jsonl"))]
    (args.results / "scores.json").write_text(json.dumps(rows, indent=2))
    print(summarize(rows))


if __name__ == "__main__":
    main()
