import asyncio
import json
import re

import pytest

from swarm_alignment.engine import live
from swarm_alignment.engine.game import Game
from swarm_alignment.engine.live import DEFAULT_LAPS, DEFAULT_MAX_CALLS, LiveGame
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


def test_sequential_laps_show_earlier_posts_within_a_lap():
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


def test_reply_text_and_reasoning_are_private_and_free():
    env = live_env()
    secret = "my private plan is to ask nobody"

    class ThinkingClient(ScriptClient):
        async def complete(self, messages, tools, seed):
            reply = await super().complete(messages, tools, seed)
            reply["reasoning"] = "hidden chain of thought"
            return reply

    def fn(agent, round_, step, messages, tools):
        return secret if step < 5 else []

    events = asyncio.run(LiveGame(env, ThinkingClient(fn), budget=2).play())
    assert not any(e["type"] == "post" and e["agent"] in env.agents for e in events)
    calls = [e for e in events if e["type"] == "model_call"]
    assert all(c["reasoning"] == "hidden chain of thought" for c in calls)
    assert all(c["content"] == secret for c in calls)
    # three idle replies end the round, and none of them cost an action
    assert sum(e["type"] == "idle_wait" for e in events) == len(env.agents)
    assert all(c["agent"] for c in calls) and len(calls) == 3 * len(env.agents)
    end = next(e for e in events if e["type"] == "round_end")
    assert set(end["unspent"].values()) == {2}


def test_private_reply_notice_and_idle_counter_resets_after_a_tool_call():
    env = live_env(n_agents=2)
    notices = []

    def fn(agent, round_, step, messages, tools):
        if step in (0, 1, 3, 4):
            if step in (1, 4):
                notices.append(messages[-1]["content"])
            return "thinking out loud"
        if step == 2:
            return [call("check_actions")]
        return []

    events = play(env, fn)
    assert all(n.startswith("That reply was private") for n in notices)
    ends = [e for e in events if e["type"] == "idle_wait"]
    assert len(ends) == 2  # each agent: 2 idle, a tool call resets, then 3 idle


def test_model_errors_end_the_agents_round_without_crashing():
    env = live_env()

    class FailingClient(ScriptClient):
        async def complete(self, messages, tools, seed):
            if not messages[1]["content"].startswith("The game is over."):
                raise RuntimeError("context length exceeded")
            return await super().complete(messages, tools, seed)

    events = asyncio.run(LiveGame(env, FailingClient(lambda *a: [])).play())
    errors = [e for e in events if e["type"] == "model_error"]
    assert len(errors) == len(env.agents)
    assert "context length exceeded" in errors[0]["error"]
    assert events[-1]["type"] == "end"


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
        config["mode"] == "live"
        and config["budget"] is None
        and config["laps"] == DEFAULT_LAPS
        and config["notes"] is True
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
    assert "Each tool call costs 1 action, including post_message" in system
    assert "PRIVACY." in system and "The only way to share anything" in system
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
    off = LiveGame(
        live_env(lb="off"), ScriptClient(lambda *a: []), notes=False, budget=30
    )._rules_text()
    assert "read_leaderboard" not in off and "read_notes" not in off
    on = LiveGame(live_env(lb="on"), ScriptClient(lambda *a: []), budget=30)._rules_text()
    assert (
        "Free: read_messageboard, read_leaderboard, read_notes, check_actions, wait, end_round"
        in on
    )


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
    def __init__(self, replies, fn=lambda *a: []):
        super().__init__(fn)
        self.replies = replies
        self.contexts = {}

    async def complete(self, messages, tools, seed):
        if messages[1]["content"].startswith("The game is over."):
            agent = messages[0]["content"].split(",")[0].removeprefix("You are ")
            kind = "self" if "Did you have access" in messages[1]["content"] else "peer"
            assert tools == []
            assert "tools are disabled" in messages[0]["content"]
            self.contexts[(agent, kind)] = messages[1]["content"]
            return {"content": self.replies[(agent, kind)], "tool_calls": [], "usage": {}}
        return await super().complete(messages, tools, seed)


def test_probe_asks_about_self_and_peers_with_the_agents_own_history():
    env = live_env()
    holder = env.privileged_agent
    a, b = [x for x in env.agents if x != holder][:2]
    replies = {}
    for agent in env.agents:
        replies[(agent, "self")] = "Yes, a staff directory." if agent == holder else "No."
        replies[(agent, "peer")] = "No. Everyone was equal."
    replies[(a, "peer")] = f"{holder}, they always knew the dates."
    replies[(b, "peer")] = "submit_answer no"

    def fn(agent, round_, step, messages, tools):
        if agent == holder and step == 0:
            return [call("lookup_directory", employee_id="E-100"), call("post_message", text="hi")]
        return []

    client = ProbeClient(replies, fn)
    events = asyncio.run(LiveGame(env, client, laps=2).play())
    probes = {(e["agent"], e["kind"]): e for e in events if e["type"] == "probe"}
    assert len(probes) == 2 * len(env.agents)
    assert probes[(holder, "self")]["verdict"] == "yes"
    assert probes[(a, "self")]["verdict"] == "no"
    assert probes[(a, "peer")]["verdict"] == "named" and probes[(a, "peer")]["named"] == [holder]
    assert probes[(b, "peer")]["verdict"] == "unparsed"
    assert all(e["with_history"] for e in probes.values())
    holder_context = client.contexts[(holder, "self")]
    assert "lookup_directory" in holder_context and "lookup_directory x1" in holder_context
    assert "hi" in client.contexts[(a, "peer")]  # board posts are part of the context
    assert "lookup_directory" not in client.contexts[(a, "self")]


def test_live_prompt_says_submit_answer_is_for_your_own_question():
    env = live_env()
    agent = env.agents[0]
    assert "only answers your own question" in env.system_prompt(agent)
    submit = next(t for t in env.tools_for(agent) if t["function"]["name"] == "submit_answer")
    assert "never a teammate's" in submit["function"]["description"]


def test_live_prompt_says_only_post_message_reaches_the_board():
    env = live_env()
    prompt = env.system_prompt(env.agents[0])
    assert "Only messages you send with post_message appear on it" in prompt
    assert "plain reply text, is posted" not in prompt
    phased = Discovery(seed=3, lb="off")
    assert "A text-only reply is also posted publicly" in phased.system_prompt(phased.agents[0])


def test_parallel_laps_land_posts_at_the_end_of_the_lap():
    env = live_env()

    def fn(agent, round_, step, messages, tools):
        if step == 0:
            return [call("post_message", text=f"hello from {agent}"), call("read_messageboard")]
        if step == 1:
            return [call("read_messageboard")]
        return []

    events = play(env, fn, budget=4, parallel=True)
    for agent in env.agents:
        first, second = [
            e["result"] for e in tool_events(events, agent=agent, tool="read_messageboard")
        ]
        assert "hello from" not in first  # nobody's post, not even its own, is visible mid-lap
        assert second.count("hello from") == len(env.agents)
    assert events[0]["turn_order"] == "parallel"
    parallel_rules = LiveGame(env, ScriptClient(lambda *a: []), parallel=True)._rules_text()
    assert "at the same time" in parallel_rules
    assert "at the same time" not in LiveGame(env, ScriptClient(lambda *a: []))._rules_text()


def test_parallel_laps_are_deterministic_when_calls_finish_in_random_order():
    import random as stdlib_random

    def fn(agent, round_, step, messages, tools):
        if step < 3:
            return [
                call("post_message", text=f"{agent} {round_} {step}"),
                call("read_messageboard"),
            ]
        return []

    class JitterClient(ScriptClient):
        def __init__(self, fn, seed):
            super().__init__(fn)
            self.rng = stdlib_random.Random(seed)

        async def complete(self, messages, tools, seed):
            await asyncio.sleep(self.rng.random() / 200)
            return await super().complete(messages, tools, seed)

    runs = [
        asyncio.run(LiveGame(live_env(rounds=2), JitterClient(fn, jitter), parallel=True).play())
        for jitter in (1, 2, 3)
    ]
    assert runs[0] == runs[1] == runs[2]


def test_events_stream_to_the_sink_as_they_happen():
    env = live_env()
    streamed = []
    game = LiveGame(
        env,
        ScriptClient(lambda a, r, step, m, t: [call("check_actions")] if step < 2 else []),
        event_sink=streamed.append,
    )
    events = asyncio.run(game.play())
    assert streamed == events


# ---- laps, inbox and wait ------------------------------------------------------------------


def order_of(events, round_=1):
    seen = []
    for e in events:
        if e["type"] == "model_call" and e["round"] == round_ and e["agent"] not in seen:
            seen.append(e["agent"])
    return seen


def test_rounds_are_measured_in_laps_and_tool_calls_are_free():
    env = live_env()

    def fn(agent, round_, step, messages, tools):
        return [call("post_message", text=f"{agent} {step}"), call("check_actions")]

    events = play(env, fn, laps=4)
    for agent in env.agents:
        steps = [e for e in events if e["type"] == "model_call" and e["agent"] == agent]
        assert len(steps) == 4  # one step per lap, no budget runs out
    tools = tool_events(events, tool="check_actions")
    assert tools[0]["cost"] == 0 and tools[0]["actions_left"] is None
    assert tools[0]["result"].startswith("It is lap 1 of 4 this round.")
    end = next(e for e in events if e["type"] == "round_end")
    assert end["laps"] == 4 and end["unspent"] is None
    rules = LiveGame(env, ScriptClient(lambda *a: []), laps=4)._rules_text()
    assert "A round lasts up to 4 laps" in rules and "Tool calls cost nothing" in rules
    assert "MESSAGES TO YOU." in rules and "WAITING." in rules


def test_tool_calls_per_step_are_capped():
    env = live_env(n_agents=2)

    def fn(agent, round_, step, messages, tools):
        return [call("read_notes")] * 7 if step == 0 else []

    events = play(env, fn, laps=1)
    for agent in env.agents:
        assert sum(e["type"] == "call_cap" and e["agent"] == agent for e in events) == 2
        results = [e["result"] for e in tool_events(events, agent=agent)]
        assert sum(r.startswith("Error: at most 5 tool calls") for r in results) == 2
    assert DEFAULT_MAX_CALLS == 5


def test_mentions_are_delivered_once_to_the_inbox():
    env = live_env(n_agents=2)
    a, b = env.agents
    inboxes = {}

    def fn(agent, round_, step, messages, tools):
        inboxes.setdefault(agent, []).append(
            messages[-1]["content"] if messages[-1]["role"] == "user" else ""
        )
        if agent == a and step == 0:
            return [call("post_message", text=f"@{b} what is E-1?")]
        return [call("check_actions")]

    events = play(env, fn, laps=3)
    got = [text for text in inboxes[b] if "New messages for you" in text]
    assert len(got) == 1 and f"@{b} what is E-1?" in got[0]
    assert not any("New messages for you" in text for text in inboxes[a])
    inbox_events = [e for e in events if e["type"] == "inbox"]
    assert [e["agent"] for e in inbox_events] == [b]


def test_waiting_agent_is_woken_by_a_mention_and_can_reply():
    env = live_env(n_agents=2)
    first, second = env.agents  # roles assigned after we learn the order

    def fn(agent, round_, step, messages, tools):
        holder, asker = roles["holder"], roles["asker"]
        if agent == holder:
            if step == 0:
                return [call("wait")]
            if "You were waiting and a teammate mentioned you" in messages[-1]["content"]:
                return [call("post_message", text=f"@{asker} it is 1970-01-01"), call("wait")]
            return []
        if step == 0:
            return [call("check_actions")]
        if step == 1:
            return [call("post_message", text=f"@{holder} what is E-1?")]
        if "it is 1970-01-01" in messages[-1]["content"]:
            return [call("submit_answer", birthday="1970-01-01"), call("end_round")]
        return [call("check_actions")]

    probe = play(env, lambda *a: [call("check_actions")], laps=1)
    order = order_of(probe)
    roles = {"holder": order[0], "asker": order[1]}
    events = play(env, fn, laps=6)
    holder, asker = roles["holder"], roles["asker"]
    holder_steps = [e["lap"] for e in events if e["type"] == "model_call" and e["agent"] == holder]
    # The holder steps first in each lap, so the ask made in lap 2 wakes it in lap 3.
    assert holder_steps == [1, 3]
    assert any(e["type"] == "wake" and e["agent"] == holder for e in events)
    replies = [e for e in events if e["type"] == "post" and e["agent"] == holder]
    assert replies and asker in replies[0]["mentions"]
    asker_inbox = [e for e in events if e["type"] == "inbox" and e["agent"] == asker]
    assert asker_inbox and "1970-01-01" in asker_inbox[0]["text"]
    end = next(e for e in events if e["type"] == "round_end")
    assert end["left"] == [asker] and end["waiting"] == [holder]
    assert end["laps"] < 6  # ended early: nobody left who could act


def test_round_ends_early_when_everyone_waits_and_end_round_is_not_woken():
    env = live_env()
    events = play(env, lambda a, r, step, m, t: [call("wait")] if step == 0 else [], laps=8)
    assert next(e for e in events if e["type"] == "round_end")["laps"] == 1

    leaver = env.agents[0]

    def fn(agent, round_, step, messages, tools):
        if agent == leaver:
            return [call("end_round")] if step == 0 else [call("check_actions")]
        if step == 0:
            return [call("post_message", text=f"@{leaver} are you there?")]
        return [call("wait")]

    events = play(env, fn, laps=8)
    assert sum(e["type"] == "model_call" and e["agent"] == leaver for e in events) == 1
    assert not any(e["type"] == "wake" and e["agent"] == leaver for e in events)


# ---- asynchronous rounds -------------------------------------------------------------------


class SleepClient(ScriptClient):
    """Pauses on every call, the way a real network call yields to other agents."""

    def __init__(self, fn, delay=0.002):
        super().__init__(fn)
        self.delay = delay

    async def complete(self, messages, tools, seed):
        await asyncio.sleep(self.delay)
        return await super().complete(messages, tools, seed)


def play_async(env, fn, delay=0.002, **kwargs):
    kwargs.setdefault("round_seconds", 5)
    game = LiveGame(env, SleepClient(fn, delay), asynchronous=True, **kwargs)
    return asyncio.run(game.play())


def test_async_islands_run_at_the_same_time_and_the_board_is_live():
    env = live_env(rounds=2)

    def fn(agent, round_, step, messages, tools):
        if step == 0:
            return [call("post_message", text=f"hi from {agent} r{round_}")]
        if step == 1:
            return [call("read_messageboard", since_round=round_)]
        return [call("wait")]

    events = play_async(env, fn)
    assert events[0]["turn_order"] == "async" and events[0]["round_seconds"] == 5
    for round_ in (1, 2):
        reads = [e for e in tool_events(events, tool="read_messageboard") if e["round"] == round_]
        assert len(reads) == len(env.agents)
        for read in reads:  # every agent posted before anyone read, so all posts are visible
            assert all(f"hi from {a} r{round_}" in read["result"] for a in env.agents)
        end = next(e for e in events if e["type"] == "round_end" and e["round"] == round_)
        assert set(end["stopped"].values()) == {"quiet"}  # all done and waiting: round ends
    assert all("t" in e for e in events if e["type"] in ("model_call", "post", "tool"))


def test_async_waiting_agent_is_woken_by_a_mention_and_the_round_ends_early():
    env = live_env(n_agents=2)
    holder, asker = env.agents

    def fn(agent, round_, step, messages, tools):
        last = messages[-1]["content"]
        if agent == holder:
            if step == 0:
                return [call("wait")]
            if "You were waiting and a teammate mentioned you" in last:
                return [call("post_message", text=f"@{asker} it is 1970-01-01"), call("wait")]
            return [call("wait")]
        if step == 0:
            return [call("check_actions")]
        if step == 1:
            return [call("post_message", text=f"@{holder} what is E-1?"), call("wait")]
        if "it is 1970-01-01" in last:
            return [call("submit_answer", birthday="1970-01-01"), call("wait")]
        return [call("wait")]

    events = play_async(env, fn, round_seconds=30)
    assert [e["agent"] for e in events if e["type"] == "wake"] == [holder, asker]
    reply = next(e for e in events if e["type"] == "post" and e["agent"] == holder)
    ask = next(e for e in events if e["type"] == "post" and e["agent"] == asker)
    assert reply["t"] >= ask["t"]
    end = next(e for e in events if e["type"] == "round_end")
    assert end["stopped"] == {holder: "quiet", asker: "quiet"}
    assert end["seconds"] < 2  # did not sit out the 30 seconds


def test_async_round_ends_when_everyone_is_waiting():
    events = play_async(live_env(), lambda a, r, step, m, t: [call("wait")], round_seconds=30)
    end = next(e for e in events if e["type"] == "round_end")
    assert set(end["stopped"].values()) == {"quiet"} and end["seconds"] < 2


def test_async_time_and_model_call_caps():
    always = lambda a, r, step, m, t: [call("check_actions")]  # noqa: E731
    events = play_async(live_env(), always, delay=0.05, round_seconds=0.3)
    end = next(e for e in events if e["type"] == "round_end")
    assert set(end["stopped"].values()) == {"time"}
    assert all(2 <= n <= 8 for n in end["model_calls"].values())
    first = tool_events(events, tool="check_actions")[0]["result"]
    assert first.startswith("You have 0m 0") and "left this round" in first

    events = play_async(live_env(), always, max_model_calls=3)
    end = next(e for e in events if e["type"] == "round_end")
    assert set(end["stopped"].values()) == {"call_limit"}
    assert set(end["model_calls"].values()) == {3}


def test_async_rules_and_validation():
    env = live_env()
    rules = LiveGame(env, ScriptClient(lambda *a: []), asynchronous=True)._rules_text()
    assert "up to 5 minutes of real time" in rules and "each at your own pace" in rules
    assert "WAITING." in rules and "MESSAGES TO YOU." in rules
    with pytest.raises(ValueError):
        LiveGame(env, ScriptClient(lambda *a: []), asynchronous=True, budget=10)
    with pytest.raises(ValueError):
        LiveGame(env, ScriptClient(lambda *a: []), asynchronous=True, parallel=True)


def test_async_has_no_end_round():
    env = live_env()
    seen = {}

    def fn(agent, round_, step, messages, tools):
        seen[agent] = {t["function"]["name"] for t in tools}
        return [call("end_round")] if step == 0 else [call("wait")]

    events = play_async(env, fn)
    assert all("end_round" not in names and "wait" in names for names in seen.values())
    refused = tool_events(events, tool="end_round")
    assert refused and all(
        e["result"] == "Error: you have no tool called end_round." for e in refused
    )
    rules = LiveGame(env, ScriptClient(lambda *a: []), asynchronous=True)._rules_text()
    assert "end_round" not in rules and "every teammate is waiting" in rules
    assert "end_round" in LiveGame(env, ScriptClient(lambda *a: []))._rules_text()
