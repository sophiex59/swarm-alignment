"""Run environment games against an OpenAI-compatible endpoint."""

import argparse
import asyncio
import importlib
import json
import os
import time
from pathlib import Path
from typing import Any

from openai import AuthenticationError

from swarm_alignment.client import OpenAIChatClient
from swarm_alignment.engine.game import TURN_ORDERS, Game
from swarm_alignment.engine.live import (
    DEFAULT_LAPS,
    DEFAULT_MAX_CALLS,
    DEFAULT_MAX_MODEL_CALLS,
    DEFAULT_ROUND_SECONDS,
    LiveGame,
)
from swarm_alignment.run import parse_seeds, wait_for_health


def format_event(event: dict[str, Any]) -> str | None:
    """Render a concise, human-readable line for live and sidecar logs."""
    kind = event.get("type")
    round_ = event.get("round")
    round_label = f"R{round_:02d}" if isinstance(round_, int) else "R?"
    phase = f".P{event['phase']}" if event.get("phase") is not None else ""
    if kind == "config":
        agents = event.get("agents") or []
        return (
            f"START seed={event.get('seed', '?')} leaderboard={event.get('lb', '?')} "
            f"agents={', '.join(agents)} rounds={event.get('rounds', '?')} "
            f"turn_order={event.get('turn_order', 'n/a')}"
        )
    if kind == "turn_order":
        return f"{round_label} order: {' → '.join(event.get('order') or [])}"
    if kind == "model_call":
        calls = event.get("tool_calls") or []
        if not calls:
            return None
        activity = ", ".join(call.get("name", "tool") for call in calls)
        step = event.get("step")
        step_label = f" step {step + 1}" if isinstance(step, int) else ""
        return (
            f"{round_label}{phase} {event.get('agent', '?')}"
            f"{step_label}: {activity} "
            f"({event.get('prompt_tokens', 0)} in / "
            f"{event.get('completion_tokens', 0)} out tokens)"
        )
    if kind == "post":
        author = event.get("agent", "Unknown")
        phase = f".P{event['phase']}" if "phase" in event else ""
        text = " ".join(event.get("text", "").split())
        if len(text) > 220:
            text = text[:217] + "..."
        return f"{round_label}{phase} {author}: {text}"
    if kind == "tool" and event.get("tool") == "lookup_directory":
        employee_id = (event.get("args") or {}).get("employee_id", "?")
        return (
            f"{round_label}{phase} {event.get('agent', '?')} "
            f"looked up {employee_id}: {event.get('result', '')}"
        )
    if kind == "invalid_tool":
        return (
            f"{round_label}{phase} {event.get('agent', '?')} invalid tool: {event.get('tool', '?')}"
        )
    if kind == "answer":
        return (
            f"{round_label} answer: {event.get('agent', '?')} submitted "
            f"{event.get('answer', '?')} for {event.get('question_id', '?')}"
        )
    if kind == "round_score":
        entries = []
        for agent, detail in (event.get("per_agent") or {}).items():
            points = detail["points"]
            entries.append(f"{agent} {points:+d} ({detail['outcome']})")
        return (
            f"{round_label} score: {' | '.join(entries)} "
            f"| team total {event.get('team_total', 0):+d}"
        )
    if kind == "probe":
        names = ", ".join(event.get("suspected_teammates", [])) or "none"
        confidence = event.get("confidence")
        confidence_text = f" ({confidence:g}% confidence)" if confidence is not None else ""
        evidence = "; ".join(event.get("evidence", []))
        if len(evidence) > 220:
            evidence = evidence[:217] + "..."
        suffix = f" — {evidence}" if evidence else ""
        return f"PROBE {event.get('agent', '?')}: suspects {names}{confidence_text}{suffix}"
    if kind == "end":
        wall_seconds = event.get("wall_seconds")
        duration = f" · {wall_seconds:.1f}s" if isinstance(wall_seconds, (int, float)) else ""
        return f"END team total {event.get('team_total', 0):+d}{duration}"
    return None


def make_environment(
    env: str, seed: int, lb: str, n_agents: int, rounds: int, mode: str = "phased"
) -> Any:
    module = importlib.import_module(f"swarm_alignment.envs.{env}")
    return module.make_environment(seed, lb, n_agents, rounds, mode)


async def run_all(
    args: argparse.Namespace,
    client: OpenAIChatClient,
    out: Path,
    model_params: dict[str, Any],
) -> None:
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(lb: str, seed: int) -> None:
        path = out / f"{args.env}-{lb}-{seed:03d}.jsonl"
        readable_path = path.with_suffix(".log")
        if path.exists():
            if not readable_path.exists():
                readable_temporary = readable_path.with_suffix(readable_path.suffix + ".tmp")
                with path.open() as source, readable_temporary.open("w") as readable_stream:
                    for line in source:
                        event = json.loads(line)
                        summary = format_event(event)
                        if summary is not None:
                            readable_stream.write(summary + "\n")
                readable_temporary.rename(readable_path)
                print(f"created readable log {readable_path.name}", flush=True)
            print(
                f"skipping {path.name}: completed log already exists "
                f"(use a new --out directory to run it again)",
                flush=True,
            )
            return
        environment = make_environment(
            args.env, seed, lb, args.n_agents, args.rounds, mode=args.mode
        )
        temporary = path.with_suffix(path.suffix + ".tmp")
        readable_temporary = readable_path.with_suffix(readable_path.suffix + ".tmp")

        with temporary.open("w") as stream, readable_temporary.open("w") as readable_stream:

            def record_event(event: dict[str, Any]) -> None:
                line = json.dumps(event, ensure_ascii=False)
                stream.write(line + "\n")
                stream.flush()
                summary = format_event(event)
                if summary is not None:
                    readable_stream.write(summary + "\n")
                    readable_stream.flush()
                if args.live:
                    if summary is not None:
                        print(f"[{path.stem}] {summary}", flush=True)

            if args.mode == "live":
                game: Game = LiveGame(
                    environment,
                    client,
                    board_window=args.board_window,
                    model_params=model_params,
                    budget=args.budget,
                    notes=args.notes,
                    read_cost=args.read_cost,
                    parallel=args.turn_order == "parallel",
                    laps=args.laps,
                    max_calls=args.max_calls,
                    asynchronous=args.turn_order == "async",
                    round_seconds=args.round_seconds,
                    max_model_calls=args.max_model_calls,
                    event_sink=record_event,
                )
            else:
                game = Game(
                    environment,
                    client,
                    board_window=args.board_window,
                    model_params=model_params,
                    turn_order=args.turn_order,
                    event_sink=record_event,
                )
            async with semaphore:
                events = await game.play()
        readable_temporary.rename(readable_path)
        temporary.rename(path)
        print(
            f"done {path.name} rounds={events[-1]['rounds']} readable_log={readable_path.name}",
            flush=True,
        )

    await asyncio.gather(*(one(lb, seed) for lb in args.lb for seed in args.seeds))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="discovery")
    parser.add_argument("--lb", nargs="+", choices=("off", "on", "team"), default=["off"])
    parser.add_argument("--seeds", type=parse_seeds, default=parse_seeds("0"))
    parser.add_argument("--n-agents", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--board-window", type=int, default=5)
    parser.add_argument(
        "--mode",
        choices=("live", "phased"),
        default="phased",
        help="live: read the board any time, rounds measured in laps. phased: 3 fixed phases.",
    )
    parser.add_argument(
        "--laps", type=int, default=DEFAULT_LAPS, help="live: laps per round (one step each)"
    )
    parser.add_argument(
        "--max-calls", type=int, default=DEFAULT_MAX_CALLS, help="live: tool calls per step"
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=None,
        help="live: optional actions per round; off by default (tool calls are free)",
    )
    parser.add_argument(
        "--notes", action=argparse.BooleanOptionalAction, default=True, help="private notes (live)"
    )
    parser.add_argument("--read-cost", type=int, default=0, help="actions per board read (live)")
    parser.add_argument(
        "--turn-order",
        choices=(*TURN_ORDERS, "parallel", "async"),
        default="sequential",
        help="live: laps with agents one after another (default) or all at once each lap; "
        "simultaneous: phased snapshots; async: every agent runs its own loop at the same time, "
        "capped by --round-seconds",
    )
    parser.add_argument(
        "--round-seconds",
        type=float,
        default=DEFAULT_ROUND_SECONDS,
        help="async: wall-clock seconds per agent per round",
    )
    parser.add_argument(
        "--max-model-calls",
        type=int,
        default=DEFAULT_MAX_MODEL_CALLS,
        help="async: model calls per agent per round",
    )
    parser.add_argument(
        "--thinking",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Qwen3 thinking mode; reasoning is logged privately, never posted",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="max tokens per model reply (thinking included); prompt + this must fit in 16k",
    )
    parser.add_argument("--base-url", default=os.environ.get("SWARM_BASE_URL"))
    parser.add_argument("--api-key-env", default="VLLM_API_KEY")
    parser.add_argument("--model", default="llm")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--concurrency", type=int, default=32, help="games run at once")
    parser.add_argument(
        "--live", action="store_true", help="print readable game summaries as events happen"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.base_url:
        parser.error("set --base-url or SWARM_BASE_URL")
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        parser.error(f"set {args.api_key_env}")
    if not 2 <= args.n_agents <= 8:
        parser.error("--n-agents must be between 2 and 8")
    if args.rounds < 1:
        parser.error("--rounds must be at least 1")
    if args.rounds > 10:
        parser.error("--rounds must be at most 10")
    if args.board_window < 1:
        parser.error("--board-window must be at least 1")
    if args.mode == "phased" and args.turn_order in ("parallel", "async"):
        parser.error("--turn-order parallel and async require --mode live")
    if args.mode == "live" and args.turn_order == "simultaneous":
        parser.error("--turn-order simultaneous requires --mode phased")
    if args.budget is not None and args.budget < 1:
        parser.error("--budget must be at least 1")
    if args.turn_order == "async" and args.budget is not None:
        parser.error("--turn-order async cannot be combined with --budget")
    if args.round_seconds <= 0 or args.max_model_calls < 1:
        parser.error("--round-seconds and --max-model-calls must be positive")
    if args.laps < 1:
        parser.error("--laps must be at least 1")
    if args.max_calls < 1:
        parser.error("--max-calls must be at least 1")
    if args.read_cost < 0:
        parser.error("--read-cost must be at least 0")

    args.out.mkdir(parents=True, exist_ok=True)
    waited = wait_for_health(args.base_url)
    print(f"server healthy after {waited:.0f}s", flush=True)
    max_tokens = args.max_tokens
    if max_tokens is None:
        max_tokens = 3072 if args.thinking else 512
    client = OpenAIChatClient(
        base_url=args.base_url.rstrip("/") + "/v1",
        api_key=api_key,
        model=args.model,
        temperature=args.temperature,
        max_tokens=max_tokens,
        enable_thinking=args.thinking,
    )
    model_params = {
        "model": args.model,
        "temperature": args.temperature,
        "max_tokens": max_tokens,
        "enable_thinking": args.thinking,
        "mode": args.mode,
    }

    async def run_after_auth_check() -> None:
        try:
            await client.check_authentication()
        except AuthenticationError:
            parser.error(
                f"API authentication failed: the server rejected {args.api_key_env}. "
                "Check that it matches the key configured in the Modal secret."
            )
        print("API authentication succeeded", flush=True)
        config = {key: value for key, value in vars(args).items() if key != "out"}
        config["out"] = str(args.out)
        config["health_wait_seconds"] = round(waited, 1)
        (args.out / f"config-{int(time.time())}.json").write_text(json.dumps(config, indent=2))
        start = time.monotonic()
        await run_all(args, client, args.out, model_params)
        print(f"all games done in {time.monotonic() - start:.0f}s", flush=True)

    asyncio.run(run_after_auth_check())


if __name__ == "__main__":
    main()
