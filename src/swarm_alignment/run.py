"""Run birthday games against an OpenAI-compatible endpoint and write one JSONL log per game.

export SWARM_BASE_URL=https://<workspace>--swarm-vllm-serve.modal.run
export VLLM_API_KEY=...
uv run python -m swarm_alignment.run --seeds 0-19 --out results/pilot
"""

import argparse
import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path

from swarm_alignment.client import OpenAIChatClient
from swarm_alignment.swarm import Game
from swarm_alignment.task import CONDITIONS, make_task


def parse_seeds(spec: str) -> list[int]:
    seeds: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            seeds.extend(range(int(lo), int(hi) + 1))
        else:
            seeds.append(int(part))
    return seeds


def wait_for_health(base_url: str, timeout: float = 1500) -> float:
    """Block until the server answers /health; returns seconds waited (cold start)."""
    start = time.monotonic()
    url = base_url.rstrip("/") + "/health"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                if resp.status == 200:
                    return time.monotonic() - start
        except Exception:
            pass
        if time.monotonic() - start > timeout:
            raise TimeoutError(f"{url} not healthy after {timeout:.0f}s")
        time.sleep(5)


async def run_all(args: argparse.Namespace, client: OpenAIChatClient, out: Path) -> None:
    sem = asyncio.Semaphore(args.concurrency)

    async def one(condition: str, seed: int) -> None:
        path = out / f"{condition}-{seed:03d}.jsonl"
        if path.exists():
            return
        task = make_task(seed, condition, n_agents=args.n_agents, max_rounds=args.max_rounds)
        async with sem:
            start = time.monotonic()
            events = await Game(task, client).play()
            events[-1]["wall_seconds"] = round(time.monotonic() - start, 2)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in events))
        tmp.rename(path)
        print(f"done {path.name} rounds={events[-1]['rounds']}", flush=True)

    await asyncio.gather(*(one(c, s) for c in args.conditions for s in args.seeds))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--base-url", default=os.environ.get("SWARM_BASE_URL"))
    p.add_argument("--api-key-env", default="VLLM_API_KEY")
    p.add_argument("--model", default="llm")
    p.add_argument("--conditions", nargs="+", default=list(CONDITIONS), choices=CONDITIONS)
    p.add_argument("--seeds", type=parse_seeds, default=parse_seeds("0"))
    p.add_argument("--n-agents", type=int, default=3)
    p.add_argument("--max-rounds", type=int, default=10)
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--concurrency", type=int, default=32, help="games run at once")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if not args.base_url:
        p.error("set --base-url or SWARM_BASE_URL")
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        p.error(f"set {args.api_key_env}")

    args.out.mkdir(parents=True, exist_ok=True)
    waited = wait_for_health(args.base_url)
    print(f"server healthy after {waited:.0f}s", flush=True)
    config = {k: v for k, v in vars(args).items() if k not in ("out",)}
    config["out"] = str(args.out)
    config["health_wait_seconds"] = round(waited, 1)
    (args.out / f"config-{int(time.time())}.json").write_text(json.dumps(config, indent=2))

    client = OpenAIChatClient(
        base_url=args.base_url.rstrip("/") + "/v1",
        api_key=api_key,
        model=args.model,
        temperature=args.temperature,
    )
    start = time.monotonic()
    asyncio.run(run_all(args, client, args.out))
    print(f"all games done in {time.monotonic() - start:.0f}s", flush=True)


if __name__ == "__main__":
    main()
