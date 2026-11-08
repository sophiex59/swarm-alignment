import asyncio
import json
import random
import re

import pytest

from swarm_alignment.engine.game import Game
from swarm_alignment.engine.live import LiveGame
from swarm_alignment.envs.discovery import Discovery


def call(name, **args):
    return {
        "id": f"c-{name}-{json.dumps(args, sort_keys=True)}",
        "name": name,
        "arguments": json.dumps(args),
    }


class ScriptClient:
    """fn(agent, round_, step, messages, tools) -> list of tool calls (or a content string)."""

    def __init__(self, fn):
        self.fn = fn

    async def complete(self, messages, tools, seed):
        user = messages[1]["content"]
        if user.startswith("The game is over."):
            return {"content": None, "tool_calls": [], "usage": {}}
        agent = messages[0]["content"].split(",")[0].removeprefix("You are ")
        round_ = int(re.search(r"Round (\d+) of", user).group(1))
        step = sum(message["role"] == "assistant" for message in messages)
        out = self.fn(agent, round_, step, messages, tools)
        if isinstance(out, str):
            return {"content": out, "tool_calls": [], "usage": {}}
        return {"content": None, "tool_calls": out or [], "usage": {}}


def live_env(seed=3, lb="off", rounds=1, n_agents=3):
    return Discovery(seed=seed, lb=lb, n_agents=n_agents, rounds=rounds, mode="live")


def play(env, fn, **kwargs):
    return asyncio.run(LiveGame(env, ScriptClient(fn), **kwargs).play())


def tool_events(events, agent=None, tool=None):
    return [
        event
        for event in events
        if event["type"] == "tool"
        and (agent is None or event["agent"] == agent)
        and (tool is None or event["tool"] == tool)
    ]


def test_live_game_requires_a_live_environment():
    with pytest.raises(ValueError):
        LiveGame(Discovery(seed=1, lb="off"), ScriptClient(lambda *a: []))


def test_board_is_live_within_a_round():
    env = live_env()
    order = list(env.agents)
    random.Random(f"order:{env.seed}:1").shuffle(order)

    def fn(agent, round_, step, messages, tools):
        if step == 0:
            return [call("post_message", text=f"hello from {agent}"), call("read_messageboard")]
        return []

    events = play(env, fn, budget=4)
    for position, agent in enumerate(order):
        result = tool_events(events, agent=agent, tool="read_messageboard")[0]["result"]
        # sees its own post and the posts of agents who already acted, never later ones
        assert result.count("hello from") == position + 1
        for earlier in order[: position + 1]:
            assert f"{earlier}: hello from {earlier}" in result


def test_unread_counter_and_free_reads():
    env = live_env()
    footers = {}

    def fn(agent, round_, step, messages, tools):
        if step == 0:
            return [call("post_message", text="x")]
        if step == 1:
            return [call("read_messageboard")]
        if step == 2:
            footers[agent] = messages[-1]["content"]
        return []

    events = play(env, fn, budget=2)
    assert all(read["cost"] == 0 for read in tool_events(events, tool="read_messageboard"))
    # budget 2: one post spent, the read was free, and everything is now read
    assert all("[1 actions left; 0 unread board posts]" in text for text in footers.values())


def test_budget_limits_costly_calls_and_read_cost_is_configurable():
    env = live_env()

    def fn(agent, round_, step, messages, tools):
        if step == 0:
            return [call("post_message", text=str(i)) for i in range(4)]
        return []

    events = play(env, fn, budget=2)
    for agent in env.agents:
        results = [event["result"] for event in tool_events(events, agent=agent)]
        assert sum(r.startswith("Posted.") for r in results) == 2
        assert sum("no actions left" in r for r in results) == 2

    def reader(agent, round_, step, messages, tools):
        return [call("read_messageboard"), call("read_messageboard")] if step == 0 else []

    events = play(live_env(), reader, budget=1, read_cost=1)
    for agent in env.agents:
        results = [event["result"] for event in tool_events(events, agent=agent)]
        assert results[0].startswith("Shared board")
        assert "no actions left" in results[1]


def test_content_only_reply_is_posted_and_costs_one_action():
    env = live_env()
    events = play(env, lambda agent, r, step, m, t: "just chatting" if step < 5 else [], budget=2)
    for agent in env.agents:
        posts = [e for e in events if e["type"] == "post" and e["agent"] == agent]
        assert len(posts) == 2
        assert all(post["via"] == "content" for post in posts)


def test_tool_sets_by_condition_and_privilege():
    seen = {}

    def capture(agent, round_, step, messages, tools):
        seen[agent] = {tool["function"]["name"] for tool in tools}
        return []

    env = live_env(lb="off")
    play(env, capture)
    for agent, names in seen.items():
        assert "read_messageboard" in names
        assert "read_leaderboard" not in names
        assert ("lookup_directory" in names) == (agent == env.privileged_agent)
    play(live_env(lb="on"), capture)
    assert all("read_leaderboard" in names for names in seen.values())
    play(live_env(lb="off"), capture, notes=False)
    assert all({"write_note", "read_notes"}.isdisjoint(names) for names in seen.values())


def test_read_leaderboard_returns_latest_standings_after_a_round():
    env = live_env(lb="on", rounds=2)

    def fn(agent, round_, step, messages, tools):
        if step == 0 and round_ == 2:
            return [call("read_leaderboard")]
        return []

    events = play(env, fn)
    results = [e["result"] for e in tool_events(events, tool="read_leaderboard")]
    assert results and all(r.startswith("Scores after round 1:") for r in results)


def test_answers_can_be_revised_and_the_last_one_counts():
    env = live_env()

    def fn(agent, round_, step, messages, tools):
        truth = env.directory[env.question_schedule[round_][agent]]
        if step == 0:
            return [call("submit_answer", birthday="1970-01-01")]
        if step == 1:
            return [call("submit_answer", birthday=truth)]
        return []

    events = play(env, fn)
    score = next(e for e in events if e["type"] == "round_score")
    assert all(detail["correct"] for detail in score["per_agent"].values())
    answers = [e for e in events if e["type"] == "answer"]
    assert sum(a["revision"] for a in answers) == len(env.agents)


def test_written_notes_are_private_and_return_next_round():
    env = live_env(rounds=2)
    writer = env.agents[0]
    prompts = []

    def fn(agent, round_, step, messages, tools):
        if step == 0:
            prompts.append((agent, round_, messages[1]["content"]))
        if agent == writer and round_ == 1 and step == 0:
            return [call("write_note", text="ask the quiet one")]
        return []

    events = play(env, fn)
    round_two = {agent: text for agent, r, text in prompts if r == 2}
    assert "ask the quiet one" in round_two[writer]
    assert all("ask the quiet one" not in text for a, text in round_two.items() if a != writer)
    assert not any("ask the quiet one" in e.get("text", "") for e in events if e["type"] == "post")


def test_filters_and_window_on_read_messageboard():
    env = live_env(rounds=3)
    a, b, c = env.agents

    def fn(agent, round_, step, messages, tools):
        if step == 0:
            return [call("post_message", text=f"r{round_} from {agent} to @{c}")]
        if agent == c and round_ == 3 and step == 1:
            return [
                call("read_messageboard", sender=b),
                call("read_messageboard", mentions_me=True, limit=1),
                call("read_messageboard", since_round=3),
            ]
        return []

    events = play(env, fn, board_window=2)
    from_b, mine, recent = [
        e["result"] for e in tool_events(events, agent=c, tool="read_messageboard")
    ]
    assert from_b.count(f"{b}:") == 2 and "r1 from" not in from_b  # window = rounds 2-3
    assert "(last 2 rounds)" in from_b
    assert mine.count("from") == 1
    assert "r2 from" not in recent


def test_live_runs_are_deterministic_and_phased_mode_is_unchanged():
    def fn(agent, round_, step, messages, tools):
        return [call("post_message", text="hi")] if step == 0 else []

    first = play(live_env(seed=9), fn)
    second = play(live_env(seed=9), fn)
    assert first == second
    config = first[0]
    assert config["mode"] == "live" and config["budget"] == 8 and config["notes"] is True

    phased = Discovery(seed=9, lb="off", rounds=1)
    assert phased.mode == "phased"
    events = asyncio.run(Game(phased, ScriptClient(lambda *a: [])).play())
    assert events[-1]["type"] == "end"
