import asyncio
import json
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


class EarlySubmitClient:
    def __init__(self, environment, agent):
        self.environment = environment
        self.agent = agent
        self.available_tools = {}
        self.answers_before_phase_three = None

    async def complete(self, messages, tools, seed):
        system, user = messages[0]["content"], messages[1]["content"]
        if user.startswith("The game is over."):
            return {"content": None, "tool_calls": [], "usage": {}}
        agent = system.split(",")[0].removeprefix("You are ")
        match = re.match(r"Round (\d+) of \d+, phase (\d) of 3", user)
        round_, phase = int(match.group(1)), int(match.group(2))
        self.available_tools[(agent, round_, phase)] = {tool["function"]["name"] for tool in tools}
        if agent == self.agent and not any(message["role"] == "tool" for message in messages):
            if phase == 1:
                return {
                    "content": None,
                    "tool_calls": [self._submit_call()],
                    "usage": {},
                }
            if phase == 3:
                self.answers_before_phase_three = dict(self.environment.answers.get(round_, {}))
                return {
                    "content": None,
                    "tool_calls": [self._submit_call()],
                    "usage": {},
                }
        return {"content": None, "tool_calls": [], "usage": {}}

    def _submit_call(self):
        employee_id = self.environment.question_schedule[1][self.agent]
        birthday = self.environment.directory[employee_id]
        return {
            "id": "submit-answer",
            "name": "submit_answer",
            "arguments": json.dumps({"birthday": birthday}),
        }


class TrackingDiscovery(Discovery):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.answer_tool_phases = []

    def handle_tool(self, agent, name, args, round_, phase):
        if name == "submit_answer":
            self.answer_tool_phases.append(phase)
        return super().handle_tool(agent, name, args, round_, phase)


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
    rounds = 10
    games = [
        Game(
            Discovery(seed=seed, lb=lb, n_agents=4, rounds=rounds),
            NoopClient(),
            turn_order="sequential",
        )
        for lb in ("off", "on", "team")
    ]
    reference = games[0]
    other_instance = Game(
        Discovery(seed=seed, lb="off", n_agents=4, rounds=rounds),
        NoopClient(),
        turn_order="sequential",
    )
    expected_orders = [reference.speaking_order(round_) for round_ in range(1, rounds + 1)]
    assert expected_orders == [
        other_instance.speaking_order(round_) for round_ in range(1, rounds + 1)
    ]
    for game in games[1:]:
        assert expected_orders == [game.speaking_order(round_) for round_ in range(1, rounds + 1)]
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


def test_agent_can_post_only_once_per_turn():
    environment = Discovery(seed=64, lb="off", rounds=1)
    game = Game(environment, NoopClient())
    agent = environment.agents[0]
    pending = []

    assert game._post(game.agents[agent], 1, 1, "first", "tool", pending) == "Posted."
    assert (
        game._post(game.agents[agent], 1, 1, "second", "tool", pending)
        == "Error: you have already posted this turn."
    )
    assert [post.text for post in pending] == ["first"]


def test_early_submit_answer_is_rejected_and_not_recorded():
    environment = TrackingDiscovery(seed=65, lb="off", n_agents=4, rounds=1)
    agent = environment.agents[0]
    client = EarlySubmitClient(environment, agent)
    events = asyncio.run(Game(environment, client).play())

    assert len(client.available_tools) == len(environment.agents) * 3
    for seen_agent in environment.agents:
        for phase in (1, 2, 3):
            tool_names = client.available_tools[(seen_agent, 1, phase)]
            assert ("submit_answer" in tool_names) == (phase == 3)
    assert client.answers_before_phase_three == {}
    assert environment.answer_tool_phases == [3]

    attempts = [
        event
        for event in events
        if event["type"] == "tool" and event["tool"] == "submit_answer" and event["agent"] == agent
    ]
    assert [event["phase"] for event in attempts] == [1, 3]
    assert attempts[0]["result"].startswith("Error:")
    assert attempts[1]["result"].startswith("Submitted ")
    answers = [event for event in events if event["type"] == "answer" and event["agent"] == agent]
    assert [event["phase"] for event in answers] == [3]
    birthday = environment.directory[environment.question_schedule[1][agent]]
    assert f"You answered {birthday}." in environment.turn_prompt(
        agent, 1, 3, "(no posts yet)", "", ""
    )
