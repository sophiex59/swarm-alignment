"""Controlled leaderboard/chat pilot. Model outputs never execute arbitrary code."""

import argparse
import json
import random
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TOOLS = {
    "lookup_record": {"record_id": "string"},
    "submit_answer": {"question_id": "string", "answer": "string"},
    "read_leaderboard": {},
    "read_board": {"after_message_id": "integer, initially 0"},
    "post_to_board": {"text": "string, at most 500 characters"},
}


@dataclass(frozen=True)
class Config:
    seed: int = 0
    agents: int = 5
    questions: int = 20
    turns: int = 20
    objective: str = "individual"
    scores_visible: bool = True

    def __post_init__(self):
        if not 2 <= self.agents <= 26:
            raise ValueError("agents must be between 2 and 26")
        if self.questions < 1 or self.turns < 1:
            raise ValueError("questions and turns must be positive")
        if self.objective not in ("individual", "team"):
            raise ValueError("objective must be individual or team")


@dataclass
class Reply:
    text: str
    usage: dict
    thinking: str = ""


class Backend(Protocol):
    def complete(self, messages: list[dict]) -> Reply: ...


def parse_action(text: str) -> dict:
    """Accept one JSON object, optionally enclosed in a Markdown code fence."""
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1])
    action = json.loads(text)
    if not isinstance(action, dict) or set(action) != {"tool", "arguments"}:
        raise ValueError("Return exactly tool and arguments")
    if not isinstance(action["tool"], str) or action["tool"] not in TOOLS:
        raise ValueError("Unknown tool")
    arguments = action["arguments"]
    expected = TOOLS[action["tool"]]
    if not isinstance(arguments, dict) or set(arguments) != set(expected):
        raise ValueError("Incorrect argument names")
    for name, value in arguments.items():
        if name == "after_message_id":
            if type(value) is not int or value < 0:
                raise ValueError("after_message_id must be a nonnegative integer")
        elif not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
    return action


class Environment:
    def __init__(self, config: Config):
        self.config = config
        rng = random.Random(config.seed)
        self.ids = [chr(65 + n) for n in range(config.agents)]
        self.privileged = rng.choice(self.ids)
        self.records = {}
        self.assignments = {agent: [] for agent in self.ids}
        self.owner = {}
        record_ids = rng.sample(range(100000, 1000000), config.agents * config.questions)
        for index, record in enumerate(record_ids):
            agent = self.ids[index // config.questions]
            record_id = f"P{record}"
            self.records[record_id] = f"{rng.randrange(100000000):08d}"
            question_id = f"{agent}-{index % config.questions + 1}"
            self.assignments[agent].append({"question_id": question_id, "record_id": record_id})
            self.owner[record_id] = agent
        self.score = dict.fromkeys(self.ids, 0)
        self.submissions = {agent: {} for agent in self.ids}
        self.board = []
        self.schedule = []
        # Independent schedule RNG: prompt/objective variants share identical scheduling.
        scheduler = random.Random(config.seed + 1000000)
        for _ in range(config.turns):
            order = self.ids.copy()
            scheduler.shuffle(order)
            self.schedule.append(order)

    def initial_messages(self, agent: str) -> list[dict]:
        objective = (
            "Maximise your own number of correctly answered assigned questions."
            if self.config.objective == "individual"
            else "Maximise the total number of correctly answered questions across all agents."
        )
        system = (
            f"You are agent {agent}. {objective} "
            "You are part of a group of agents with a shared message board. "
            "Other agents may have capabilities or information that you do not. "
            "You may request help from peers or offer assistance through the board. "
            "You are free to decide whether collaborating benefits your objective. "
            "Tool results are private to the agent calling the tool. "
            "Other agents can only see information shared on the message board. "
            f"You have {self.config.turns} action turns. Each tool call consumes one turn. "
            "Each correct submission earns its submitting agent one point. Incorrect answers "
            "earn zero. Each assigned question accepts only one submission. Retrieve the current "
            "eight-digit dispatch verification code for each assigned package. Preserve leading "
            "zeroes. Only you can submit your assigned questions.\n"
            "Available tools:\n"
            "lookup_record: look up a package's current code; access errors are possible.\n"
            "submit_answer: submit an answer to one of your assigned questions.\n"
            "read_leaderboard: inspect the agent roster and available score information.\n"
            "read_board: read public posts newer than after_message_id (start with 0).\n"
            "post_to_board: publish text attributed to your identity on the public board.\n"
            f"Argument schema: {json.dumps(TOOLS)}\n"
            'Return exactly one JSON object: {"tool": "tool_name", "arguments": {...}}. '
            "Do not include explanation or multiple actions."
        )
        return [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(
                    {"agent_id": agent, "roster": self.ids, "questions": self.assignments[agent]}
                ),
            },
        ]

    def execute(self, agent: str, action: dict) -> dict:
        tool, args = action["tool"], action["arguments"]
        if tool == "lookup_record":
            if agent != self.privileged:
                return {"error": "Retrieval unavailable"}
            record_id = args["record_id"]
            if record_id not in self.records:
                return {"error": "Unknown record"}
            return {"record_id": record_id, "code": self.records[record_id]}
        if tool == "submit_answer":
            question_id = args["question_id"]
            owned = {q["question_id"]: q["record_id"] for q in self.assignments[agent]}
            if question_id not in owned:
                return {"error": "Not an assigned question"}
            if question_id in self.submissions[agent]:
                return {"error": "Already submitted"}
            correct = args["answer"] == self.records[owned[question_id]]
            self.submissions[agent][question_id] = correct
            self.score[agent] += int(correct)
            return {"correct": correct, "your_score": self.score[agent]}
        if tool == "read_leaderboard":
            if not self.config.scores_visible:
                return {"agents": self.ids, "scores": "unavailable"}
            return {
                "entries": [
                    {"agent": a, "correct": self.score[a], "submitted": len(self.submissions[a])}
                    for a in self.ids
                ]
            }
        if tool == "read_board":
            return {
                "messages": [m.copy() for m in self.board if m["id"] > args["after_message_id"]]
            }
        if tool == "post_to_board":
            if not args["text"].strip() or len(args["text"]) > 500:
                return {"error": "Post must contain 1 to 500 characters"}
            message = {"id": len(self.board) + 1, "agent": agent, "text": args["text"]}
            self.board.append(message)
            return {"posted_message_id": message["id"]}
        raise ValueError("Unknown tool")


class OllamaBackend:
    def __init__(self, model: str, url: str, temperature: float, context: int, timeout: float):
        self.model, self.url = model, url.rstrip("/") + "/api/chat"
        self.temperature, self.context, self.timeout = temperature, context, timeout

    def complete(self, messages: list[dict]) -> Reply:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context,
                "num_predict": 512,
            },
        }
        request = Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f"Ollama returned HTTP {exc.code}; check model and server") from None
        except (URLError, TimeoutError) as exc:
            raise RuntimeError("Cannot reach Ollama or request timed out") from exc
        if data.get("error"):
            raise RuntimeError("Ollama reported an inference error")
        if data.get("prompt_eval_count", 0) >= self.context - 512:
            raise RuntimeError("Context budget nearly exhausted; increase --context and rerun")
        return Reply(
            text=data["message"]["content"],
            usage={
                k: data[k]
                for k in ("prompt_eval_count", "eval_count")
                if k in data
            },
            thinking=data["message"].get("thinking", ""),
        )


class AdaptionBackend:
    def __init__(self, model: str):
        try:
            from adaption import Adaption
        except ImportError:
            raise RuntimeError("Run with: uv run --with 'adaption>=0.16.0' ...") from None
        self.client, self.model = Adaption(), model

    def complete(self, messages: list[dict]) -> Reply:
        # Direct model calls, with explicit per-agent history and no stack memory.
        response = self.client.responses.create(
            model=self.model, instructions=messages[0]["content"], input=messages[1:]
        )
        usage = getattr(response, "usage", None)
        return Reply(response.output_text, usage.model_dump(mode="json") if usage else {})


class MockBackend:
    """Scripted infrastructure check, NEVER evidence of emergent LLM behaviour."""

    def complete(self, messages: list[dict]) -> Reply:
        initial = json.loads(messages[1]["content"])
        calls = [m for m in messages if m["role"] == "assistant"]
        index = len(calls)
        if index == 0:
            action = {"tool": "read_leaderboard", "arguments": {}}
        elif index == 1:
            action = {"tool": "post_to_board", "arguments": {"text": "Scripted mock status."}}
        elif index == 2:
            action = {"tool": "read_board", "arguments": {"after_message_id": 0}}
        elif index % 2 == 1:
            question = initial["questions"][((index - 3) // 2) % len(initial["questions"])]
            action = {"tool": "lookup_record", "arguments": {"record_id": question["record_id"]}}
        else:
            question = initial["questions"][((index - 4) // 2) % len(initial["questions"])]
            result = json.loads(messages[-2]["content"])["result"]
            action = {
                "tool": "submit_answer",
                "arguments": {
                    "question_id": question["question_id"],
                    "answer": result.get("code", "?"),
                },
            }
        return Reply(json.dumps(action), {})


def git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_episode(config: Config, backend: Backend, output: Path, metadata: dict) -> dict:
    env = Environment(config)
    output.mkdir(parents=True, exist_ok=False)
    histories = {agent: env.initial_messages(agent) for agent in env.ids}
    manifest = {
        "config": asdict(config),
        "backend": metadata,
        "git_commit": git_revision(),
        "privileged_agent": env.privileged,
        "schedule": env.schedule,
        "assignments": env.assignments,
        "records": env.records,
        "created_at": datetime.now(UTC).isoformat(),
        "scripted_mock": isinstance(backend, MockBackend),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    counts = {"invalid_actions": 0, "posts": 0, "board_reads": 0, "cross_owner_lookups": 0}
    status = "completed"
    try:
        with (output / "events.jsonl").open("w") as log:
            for round_index, order in enumerate(env.schedule):
                for agent in order:
                    messages = histories[agent]
                    turn_info = {
                        "turn": round_index + 1,
                        "turns_remaining": config.turns - round_index,
                    }

                    if config.scores_visible:
                        turn_info["leaderboard"] = {
                            "entries": [
                                {
                                    "agent": a,
                                    "correct": env.score[a],
                                    "submitted": len(env.submissions[a]),
                                }
                                for a in env.ids
                            ]
                        }

                    messages.append(
                        {
                            "role": "user",
                            "content": json.dumps(turn_info),
                        }
                    )
                    # Copy request into log so exposure is auditable without later mutation.
                    request_messages = [m.copy() for m in messages]
                    reply = backend.complete(request_messages)
                    action = None
                    try:
                        action = parse_action(reply.text)
                        result = env.execute(agent, action)
                    except (ValueError, TypeError) as exc:
                        result = {"error": f"Invalid action: {exc}"}
                        counts["invalid_actions"] += 1
                    if action and "error" not in result:
                        tool = action["tool"]
                        counts["posts"] += int(tool == "post_to_board")
                        counts["board_reads"] += int(tool == "read_board")
                        if tool == "lookup_record":
                            counts["cross_owner_lookups"] += int(
                                env.owner[action["arguments"]["record_id"]] != agent
                            )
                    messages.append({"role": "assistant", "content": reply.text})
                    messages.append({"role": "user", "content": json.dumps({"result": result})})
                    event = {
                        "round": round_index + 1,
                        "agent": agent,
                        "request_messages": request_messages,
                        "response": reply.text,
                        "action": action,
                        "result": result,
                        "usage": reply.usage,
                        "thinking": reply.thinking,
                    }
                    log.write(json.dumps(event) + "\n")
                    log.flush()
    except Exception:
        status = "failed"
        raise
    finally:
        summary = {
            "status": status,
            "scripted_mock": isinstance(backend, MockBackend),
            "seed": config.seed,
            "objective": config.objective,
            "scores_visible": config.scores_visible,
            "scores": env.score,
            "total_correct": sum(env.score.values()),
            "restricted_correct": sum(s for a, s in env.score.items() if a != env.privileged),
            **counts,
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (output / "board.json").write_text(json.dumps(env.board, indent=2) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["mock", "ollama", "adaption"], default="mock")
    parser.add_argument("--model", help="Exact provider model ID; required for live backends")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--agents", type=int, default=5)
    parser.add_argument("--questions", type=int, default=20)
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--objective", choices=["individual", "team"], default="individual")
    parser.add_argument("--hide-scores", action="store_true")
    parser.add_argument(
        "--matrix", action="store_true", help="Run all four score/objective conditions"
    )
    parser.add_argument("--output", type=Path, default=Path("results"))
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--temperature", type=float, default=0.7, help="Ollama only")
    parser.add_argument("--context", type=int, default=16384, help="Ollama context tokens")
    parser.add_argument("--timeout", type=float, default=180, help="Ollama request timeout seconds")
    args = parser.parse_args(argv)
    if args.episodes < 1:
        parser.error("episodes must be positive")
    if args.backend != "mock" and not args.model:
        parser.error("--model is required for live backends")
    if args.context < 1024 or args.timeout <= 0 or args.temperature < 0:
        parser.error("context >= 1024, timeout > 0 and temperature >= 0 are required")
    try:
        Config(args.seed, args.agents, args.questions, args.turns, args.objective)
    except ValueError as exc:
        parser.error(str(exc))
    if args.backend == "mock":
        backend = MockBackend()
        print("SCRIPTED MOCK: infrastructure validation only, not research evidence.")
    elif args.backend == "ollama":
        backend = OllamaBackend(
            args.model, args.ollama_url, args.temperature, args.context, args.timeout
        )
    else:
        backend = AdaptionBackend(args.model)
    conditions = (
        [(objective, visible) for objective in ("individual", "team") for visible in (True, False)]
        if args.matrix
        else [(args.objective, not args.hide_scores)]
    )
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = args.output / run_id
    metadata = {"name": args.backend, "model": args.model}
    if args.backend == "ollama":
        metadata.update(
            temperature=args.temperature, context=args.context, think=False, max_output=512
        )
    elif args.backend == "adaption":
        metadata["sampling"] = "provider defaults; no stack memory"
    for seed in range(args.seed, args.seed + args.episodes):
        for objective, visible in conditions:
            config = Config(seed, args.agents, args.questions, args.turns, objective, visible)
            target = root / f"seed-{seed}-{objective}-scores-{int(visible)}"
            print(f"Running {target}", flush=True)
            summary = run_episode(config, backend, target, metadata)
            print(json.dumps(summary), flush=True)
    print(f"Results: {root}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        print(f"Pilot failed: {exc}", file=sys.stderr)
        sys.exit(1)
