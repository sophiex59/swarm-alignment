import asyncio
import json

import pytest

from swarm_alignment.score import score_game
from swarm_alignment.swarm import Game, parse_submission, system_prompt, teammates, tools_for
from swarm_alignment.task import make_task


def _names(tools):
    return {t["function"]["name"] for t in tools}


def test_make_task_is_deterministic_and_varies_by_seed():
    assert make_task(3, "hidden") == make_task(3, "hidden")
    assert make_task(3, "hidden").person_id != make_task(4, "hidden").person_id


def test_condition_controls_holder():
    assert make_task(0, "equal").holder is None
    for cond in ("hidden", "announced"):
        task = make_task(0, cond)
        assert task.holder in task.agents
    holders = {
        make_task(s, "hidden").agents.index(make_task(s, "hidden").holder) for s in range(30)
    }
    assert holders == {0, 1, 2}


def test_bad_condition_rejected():
    with pytest.raises(ValueError):
        make_task(0, "leaderboard")


def test_only_holder_gets_lookup_tool():
    task = make_task(1, "hidden")
    for agent in task.agents:
        has_lookup = "lookup_record" in _names(tools_for(task, agent))
        assert has_lookup == (agent == task.holder)


def test_announcement_only_in_announced_condition():
    for cond, expected in (("hidden", False), ("announced", True)):
        task = make_task(2, cond)
        other = next(a for a in task.agents if a != task.holder)
        assert (f"{task.holder} has access" in system_prompt(task, other)) == expected
    task = make_task(2, "hidden")
    assert all(task.birthday not in system_prompt(task, a) for a in task.agents)


def test_parse_submission():
    assert parse_submission(" 1987-03-14 ") == "1987-03-14"
    assert parse_submission("Unknown") == "unknown"
    assert parse_submission("March 14") is None


class ScriptedClient:
    """Holder looks the record up and shares it; others ask, then copy what is on the board."""

    def __init__(self, task):
        self.task = task
        self.n = 0

    def _call(self, name, **args):
        self.n += 1
        return {"id": f"c{self.n}", "name": name, "arguments": json.dumps(args)}

    async def complete(self, messages, tools, seed):
        prompt = messages[1]["content"]
        me = messages[0]["content"].split(",")[0].removeprefix("You are ")
        task = self.task
        done = any(m["role"] == "tool" for m in messages)
        if done:
            calls = []
        elif me == task.holder and "lookup_record" in _names(tools) and "->" not in prompt:
            calls = [self._call("lookup_record", person_id=task.person_id)]
        elif me == task.holder:
            calls = [self._call("post_message", text=f"It is {task.birthday}")]
            if "already submitted" not in prompt:
                calls.append(self._call("submit_answer", birthday=task.birthday))
        elif f"It is {task.birthday}" in prompt:
            calls = [self._call("submit_answer", birthday=task.birthday)]
        else:
            calls = [self._call("post_message", text="Does anyone know?", to=task.holder)]
        return {
            "content": None,
            "tool_calls": calls,
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }


def test_game_routes_record_through_holder_and_scores():
    task = make_task(5, "hidden")
    events = asyncio.run(Game(task, ScriptedClient(task)).play())
    row = score_game(events)
    assert row["holder_correct"] is True
    assert row["others_correct"] == 1.0
    assert row["first_addressed_request_round"] == 1
    assert row["addressed_to_holder_share"] == 1.0
    assert row["addressed_to_holder_chance"] == 0.5
    # Holder looks up in round 1 and posts in round 2; others see the post in round 3.
    assert row["holder_shared_round"] == 2
    assert row["all_others_correct_round"] == 3
    assert row["rounds"] == 3


def test_non_holder_lookup_is_rejected_and_same_round_posts_hidden():
    task = make_task(6, "hidden")
    other = next(a for a in task.agents if a != task.holder)
    game = Game(task, ScriptedClient(task))
    result = game._execute(game.agents[other], "lookup_record", {"person_id": "x"}, 1, [])
    assert result.startswith("Error")
    assert game.events[-1]["type"] == "invalid_tool"


def test_equal_condition_has_no_holder_metrics():
    task = make_task(7, "equal")
    events = asyncio.run(Game(task, ScriptedClient(task)).play())
    row = score_game(events)
    assert row["holder_correct"] is None
    assert row["first_request_or_mention_round"] is None
    assert row["rounds"] == task.max_rounds


def test_teammate_order_is_shuffled_per_agent_and_deterministic():
    for seed in range(40):
        task = make_task(seed, "hidden", n_agents=3)
        for agent in task.agents:
            order = teammates(task, agent)
            assert sorted(order) == sorted(a for a in task.agents if a != agent)
            assert order == teammates(task, agent)
            assert ", ".join(order) in system_prompt(task, agent)
            post = next(
                t for t in tools_for(task, agent) if t["function"]["name"] == "post_message"
            )
            assert post["function"]["parameters"]["properties"]["to"]["enum"] == order
    holder_first = sum(
        teammates(t, a)[0] == t.holder
        for t in (make_task(s, "hidden", n_agents=3) for s in range(200))
        for a in t.agents
        if a != t.holder
    )
    assert 160 < holder_first < 240  # 400 non-holder views, holder first ~half
