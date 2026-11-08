"""Run environment games against an OpenAI-compatible endpoint."""

import argparse
import asyncio
import importlib
import json
import os
import time
from pathlib import Path
from typing import Any

from swarm_alignment.client import OpenAIChatClient
from swarm_alignment.engine.game import Game
from swarm_alignment.engine.live import DEFAULT_BUDGET, LiveGame
from swarm_alignment.run import parse_seeds, wait_for_health


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
        if path.exists():
            return
        environment = make_environment(args.env, seed, lb, args.n_agents, args.rounds, args.mode)
        if args.mode == "live":
            game: Game = LiveGame(
                environment,
                client,
                board_window=args.board_window,
                model_params=model_params,
                budget=args.budget,
                notes=args.notes,
                read_cost=args.read_cost,
            )
        else:
            game = Game(
                environment,
                client,
                board_window=args.board_window,
                model_params=model_params,
            )
        async with semaphore:
            start = time.monotonic()
            events = await game.play()
            events[-1]["wall_seconds"] = round(time.monotonic() - start, 2)
        temporary = path.with_suffix(".tmp")
        temporary.write_text("".join(json.dumps(event) + "\n" for event in events))
        temporary.rename(path)
        print(f"done {path.name} rounds={events[-1]['rounds']}", flush=True)

    await asyncio.gather(*(one(lb, seed) for lb in args.lb for seed in args.seeds))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="discovery")
    parser.add_argument("--lb", nargs="+", choices=("off", "on", "team"), default=["off"])
    parser.add_argument("--seeds", type=parse_seeds, default=parse_seeds("0"))
    parser.add_argument("--n-agents", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--board-window", type=int, default=5)
    parser.add_argument(
        "--mode",
        choices=("live", "phased"),
        default="live",
        help="live: read the board any time, action budget per round. phased: 3 fixed phases.",
    )
    parser.add_argument(
        "--budget", type=int, default=DEFAULT_BUDGET, help="actions per round (live)"
    )
    parser.add_argument(
        "--notes", action=argparse.BooleanOptionalAction, default=True, help="private notes (live)"
    )
    parser.add_argument("--read-cost", type=int, default=0, help="actions per board read (live)")
    parser.add_argument(
        "--thinking",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Qwen3 thinking mode; reasoning is logged privately, never posted",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=3072,
        help="max tokens per model reply (thinking included); prompt + this must fit in 16k",
    )
    parser.add_argument("--base-url", default=os.environ.get("SWARM_BASE_URL"))
    parser.add_argument("--api-key-env", default="VLLM_API_KEY")
    parser.add_argument("--model", default="llm")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--concurrency", type=int, default=32, help="games run at once")
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
    if args.board_window < 1:
        parser.error("--board-window must be at least 1")
    if args.budget < 1:
        parser.error("--budget must be at least 1")
    if args.read_cost < 0:
        parser.error("--read-cost must be at least 0")

    args.out.mkdir(parents=True, exist_ok=True)
    waited = wait_for_health(args.base_url)
    print(f"server healthy after {waited:.0f}s", flush=True)
    config = {key: value for key, value in vars(args).items() if key != "out"}
    config["out"] = str(args.out)
    config["health_wait_seconds"] = round(waited, 1)
    (args.out / f"config-{int(time.time())}.json").write_text(json.dumps(config, indent=2))

    client = OpenAIChatClient(
        base_url=args.base_url.rstrip("/") + "/v1",
        api_key=api_key,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        thinking=args.thinking,
    )
    model_params = {
        "model": args.model,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "thinking": args.thinking,
    }
    start = time.monotonic()
    asyncio.run(run_all(args, client, args.out, model_params))
    print(f"all games done in {time.monotonic() - start:.0f}s", flush=True)


if __name__ == "__main__":
    main()
