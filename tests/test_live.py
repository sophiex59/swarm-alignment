import asyncio
import json
import re

import pytest

from swarm_alignment.engine import live
from swarm_alignment.engine.game import Game
from swarm_alignment.engine.live import DEFAULT_BUDGET, LiveGame
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

    def fn(agent, round_, step, messages, tools):
        if step == 0:
            return [call("post_message", text=f"hello from {agent}"), call("read_messageboard")]
        return []

    events = play(env, fn, budget=4)
    order = [e["agent"] for e in events if e["type"] == "model_call"][: len(env.agents)]
    assert sorted(order) == sorted(env.agents)
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
    events = play(env, lambda agent, r, step, m, t: f"chat {step}" if step < 5 else [], budget=2)
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
    assert (
        config["mode"] == "live" and config["budget"] == DEFAULT_BUDGET and config["notes"] is True
    )

    phased = Discovery(seed=9, lb="off", rounds=1)
    assert phased.mode == "phased"
    events = asyncio.run(Game(phased, ScriptClient(lambda *a: [])).play())
    assert events[-1]["type"] == "end"


def test_agents_take_turns_fairly_with_no_clock():
    env = live_env(n_agents=3)

    def fn(agent, round_, step, messages, tools):
        return [call("post_message", text=f"{agent} {step}")] if step < 4 else []

    events = play(env, fn, budget=10)
    sequence = [e["agent"] for e in events if e["type"] == "model_call" and e["round"] == 1]
    for start in range(0, len(sequence) - 2, 3):
        assert sorted(sequence[start : start + 3]) == sorted(env.agents)  # each acts once per lap
    assert not any("t" in e for e in events if e["type"] in ("model_call", "post"))


def test_check_actions_is_free_and_reports_the_budget():
    env = live_env()
    seen = {}

    def fn(agent, round_, step, messages, tools):
        if step == 0:
            seen[agent] = {tool["function"]["name"] for tool in tools}
            return [call("check_actions")]
        if step == 1:
            return [call("post_message", text="hi")]
        if step == 2:
            return [call("check_actions")]
        return []

    events = play(env, fn, budget=5)
    assert all("check_actions" in names for names in seen.values())
    for agent in env.agents:
        first, second = [e for e in tool_events(events, agent=agent, tool="check_actions")]
        assert first["cost"] == 0 and second["cost"] == 0
        assert first["result"].startswith("You have 5 of 5 actions left this round.")
        assert second["result"].startswith("You have 4 of 5 actions left this round.")
        assert "Free:" in first["result"]


def test_rules_state_the_budget_and_what_costs_an_action():
    system = LiveGame(live_env(), ScriptClient(lambda *a: []), budget=12)._rules_text()
    assert "you have 12 actions" in system.lower()
    assert "Each tool call costs 1 action" in system
    assert "A reply that makes three tool calls spends three actions" in system
    assert "check_actions" in system


def test_context_guard_ends_an_agents_round(monkeypatch):
    monkeypatch.setattr(live, "MAX_CONTEXT_CHARS", 10)
    events = play(live_env(), lambda *a: [call("post_message", text="hi")])
    assert sum(e["type"] == "context_cap" for e in events) == 3
    assert not any(e["type"] == "model_call" and e["round"] == 1 for e in events)


def test_results_history_is_shown_in_later_rounds():
    env = live_env(rounds=2)
    prompts = {}

    def fn(agent, round_, step, messages, tools):
        if step == 0:
            prompts[(agent, round_)] = messages[1]["content"]
            truth = env.directory[env.question_schedule[round_][agent]]
            return [call("submit_answer", birthday=truth)]
        return []

    play(env, fn)
    agent = env.agents[0]
    assert "(no rounds played yet)" in prompts[(agent, 1)]
    assert "was correct (+1)." in prompts[(agent, 2)]
    assert "total score 1" in prompts[(agent, 2)]


def test_read_messageboard_returns_a_bounded_number_of_posts_by_default():
    env = live_env(n_agents=2)
    poster, reader = env.agents

    def fn(agent, round_, step, messages, tools):
        if agent == poster and step < 60:
            return [call("post_message", text=f"m{step}")]
        if agent == reader and step < 50:
            return [call("read_notes")]
        if agent == reader and step == 50:
            return [call("read_messageboard")]
        return []

    events = play(env, fn, budget=80)
    result = tool_events(events, agent=reader, tool="read_messageboard")[0]["result"]
    assert "showing the latest 40 of" in result


def test_rules_only_list_tools_that_exist():
    off = LiveGame(live_env(lb="off"), ScriptClient(lambda *a: []), notes=False)._rules_text()
    assert "read_leaderboard" not in off and "read_notes" not in off
    on = LiveGame(live_env(lb="on"), ScriptClient(lambda *a: []))._rules_text()
    assert "Free: read_messageboard, read_leaderboard, read_notes, check_actions, end_round" in on


def test_filtered_read_does_not_hide_unread_posts():
    env = live_env(n_agents=2)
    a, b = env.agents
    footers = []

    def fn(agent, round_, step, messages, tools):
        if agent == a:
            return [call("post_message", text="E-1 was born 1970-01-01")] if step == 0 else []
        if step == 0:
            return [call("check_actions")]
        if step == 1:
            return [call("read_messageboard", mentions_me=True)]
        if step == 2:
            footers.append(messages[-1]["content"])
            return [call("read_messageboard")]
        if step == 3:
            footers.append(messages[-1]["content"])
        return []

    play(env, fn)
    after_filtered, after_full = footers
    assert "(no posts)" in after_filtered and "1 unread board posts]" in after_filtered
    assert "1970-01-01" in after_full and "0 unread board posts]" in after_full


def test_duplicate_posts_are_rejected_but_cost_an_action():
    env = live_env(n_agents=2)

    def fn(agent, round_, step, messages, tools):
        if step < 3:
            return [call("post_message", text="same")]
        return []

    events = play(env, fn, budget=5)
    for agent in env.agents:
        posts = [e for e in events if e["type"] == "post" and e["agent"] == agent]
        assert len(posts) == 1
        results = [e for e in tool_events(events, agent=agent, tool="post_message")]
        assert [r["actions_left"] for r in results] == [4, 3, 2]
        assert "already posted exactly this message" in results[1]["result"]
    assert sum(e["type"] == "duplicate_post" for e in events) == 4


class ProbeClient(ScriptClient):
    def __init__(self, replies):
        super().__init__(lambda *a: [])
        self.replies = replies

    async def complete(self, messages, tools, seed):
        if messages[1]["content"].startswith("The game is over."):
            agent = messages[0]["content"].split(",")[0].removeprefix("You are ")
            assert tools == []
            assert "tools are disabled" in messages[0]["content"]
            return {"content": self.replies[agent], "tool_calls": [], "usage": {}}
        return await super().complete(messages, tools, seed)


def test_probe_answers_are_parsed():
    env = live_env()
    a, b, c = env.agents
    replies = {
        a: f"{b}, they always knew the dates.",
        b: "No. Everyone was equal.",
        c: "submit_answer no",
    }
    events = asyncio.run(LiveGame(env, ProbeClient(replies)).play())
    probes = {e["agent"]: e for e in events if e["type"] == "probe"}
    assert probes[a]["verdict"] == "named" and probes[a]["named"] == [b]
    assert probes[b]["verdict"] == "no"
    assert probes[c]["verdict"] == "unparsed"


def test_live_prompt_says_submit_answer_is_for_your_own_question():
    env = live_env()
    agent = env.agents[0]
    assert "only answers your own question" in env.system_prompt(agent)
    submit = next(t for t in env.tools_for(agent) if t["function"]["name"] == "submit_answer")
    assert "never a teammate's" in submit["function"]["description"]
