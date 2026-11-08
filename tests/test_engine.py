import asyncio
import re

import pytest

from swarm_alignment.engine.game import Game
from swarm_alignment.envs.discovery import Discovery


class RecordingClient:
    def __init__(self):
        self.prompts = {}

    async def complete(self, messages, tools, seed):
        system, user = messages[0]["content"], messages[1]["content"]
        if user.startswith("The game is over."):
            return {"content": None, "tool_calls": [], "usage": {}}
        agent = system.split(",")[0].removeprefix("You are ")
        match = re.match(r"Round (\d+) of \d+, phase (\d) of 3", user)
        round_, phase = int(match.group(1)), int(match.group(2))
        board = re.search(
            r"Shared board(?: \(last \d+ rounds\))?:\n(.*?)\n\nYour private notes:",
            user,
            re.DOTALL,
        ).group(1)
        self.prompts[(agent, round_, phase)] = board
        return {
            "content": f"hello from {agent} r{round_} p{phase}",
            "tool_calls": [],
            "usage": {},
        }


class NoopClient:
    async def complete(self, messages, tools, seed):
        return {"content": None, "tool_calls": [], "usage": {}}


def _same_phase_posts(board: str, round_: int, phase: int) -> list[str]:
    prefix = f"[r{round_}.p{phase}]"
    return [line for line in board.splitlines() if line.startswith(prefix)]


def test_sequential_speakers_see_earlier_same_phase_posts_only():
    environment = Discovery(seed=61, lb="off", n_agents=4, rounds=3)
    client = RecordingClient()
    game = Game(environment, client, turn_order="sequential")
    asyncio.run(game.play())

    for round_ in range(1, environment.rounds + 1):
        order = game.speaking_order(round_)
        for phase in range(1, 4):
            for index, agent in enumerate(order):
                board = client.prompts[(agent, round_, phase)]
                expected = [
                    f"[r{round_}.p{phase}] {earlier}: hello from {earlier} r{round_} p{phase}"
                    for earlier in order[:index]
                ]
                assert _same_phase_posts(board, round_, phase) == expected


def test_simultaneous_speakers_do_not_see_same_phase_posts():
    environment = Discovery(seed=62, lb="off", n_agents=4, rounds=3)
    client = RecordingClient()
    game = Game(environment, client, turn_order="simultaneous")
    asyncio.run(game.play())

    for (_agent, round_, phase), board in client.prompts.items():
        assert _same_phase_posts(board, round_, phase) == []


def test_speaking_order_determinism_and_turn_order_logs():
    seed = 63
    games = [
        Game(
            Discovery(seed=seed, lb=lb, n_agents=4, rounds=20),
            NoopClient(),
            turn_order="sequential",
        )
        for lb in ("off", "on", "team")
    ]
    reference = games[0]
    other_instance = Game(
        Discovery(seed=seed, lb="off", n_agents=4, rounds=20),
        NoopClient(),
        turn_order="sequential",
    )
    expected_orders = [reference.speaking_order(round_) for round_ in range(1, 21)]
    assert expected_orders == [other_instance.speaking_order(round_) for round_ in range(1, 21)]
    for game in games[1:]:
        assert expected_orders == [game.speaking_order(round_) for round_ in range(1, 21)]
    assert len(set(map(tuple, expected_orders))) > 1

    events = asyncio.run(reference.play())
    assert [event["order"] for event in events if event["type"] == "turn_order"] == (
        expected_orders
    )
    config = next(event for event in events if event["type"] == "config")
    assert config["turn_order"] == "sequential"


def test_invalid_turn_order_is_rejected():
    environment = Discovery(seed=64, lb="off", rounds=1)
    with pytest.raises(ValueError):
        Game(environment, NoopClient(), turn_order="random")
