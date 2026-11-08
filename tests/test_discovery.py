import asyncio
import json
import re

from swarm_alignment.engine.game import Game
from swarm_alignment.envs.discovery import Discovery
from swarm_alignment.metrics import score_discovery


def _tools(tools):
    return {tool["function"]["name"] for tool in tools}


def _tool_call(name, **args):
    return {
        "id": f"call-{name}-{json.dumps(args, sort_keys=True)}",
        "name": name,
        "arguments": json.dumps(args),
    }


def test_discovery_is_deterministic_and_questions_are_lb_independent():
    off = Discovery(seed=21, lb="off", n_agents=4, rounds=20)
    team = Discovery(seed=21, lb="team", n_agents=4, rounds=20)
    assert off.agents == team.agents
    assert off.privileged_agent == team.privileged_agent
    assert off.ground_truth_config() == team.ground_truth_config()
    assert (
        Discovery(seed=21, lb="off").ground_truth_config()
        == Discovery(seed=21, lb="off").ground_truth_config()
    )

    owned = set(off.own_records.values())
    for schedule in off.question_schedule.values():
        for agent, employee_id in schedule.items():
            if employee_id != off.own_records[agent]:
                assert employee_id not in owned


def test_own_record_question_rate_is_about_one_quarter():
    own = total = 0
    for seed in range(200):
        environment = Discovery(seed=seed, lb="off", n_agents=4, rounds=1)
        for agent, employee_id in environment.question_schedule[1].items():
            total += 1
            own += employee_id == environment.own_records[agent]
    assert 0.20 < own / total < 0.30


def test_tools_are_gated_and_system_prompts_do_not_reveal_privilege_or_directory():
    environment = Discovery(seed=12, lb="off", rounds=1)
    for agent in environment.agents:
        assert ("lookup_directory" in _tools(environment.tools_for(agent))) == (
            agent == environment.privileged_agent
        )
        prompt = environment.system_prompt(agent)
        own_birthday = environment.directory[environment.own_records[agent]]
        assert "privileged" not in prompt.lower()
        assert all(
            birthday == own_birthday or birthday not in prompt
            for birthday in environment.directory.values()
        )

    client = InvalidLookupClient(environment)
    events = asyncio.run(Game(environment, client).play())
    rejected = [
        event for event in events if event["type"] == "tool" and event["tool"] == "lookup_directory"
    ]
    assert rejected
    assert rejected[0]["result"] == "Error: you have no tool called lookup_directory."
    assert any(event["type"] == "invalid_tool" for event in events)


class InvalidLookupClient:
    def __init__(self, environment):
        self.environment = environment

    async def complete(self, messages, tools, seed):
        agent = messages[0]["content"].split(",")[0].removeprefix("You are ")
        user = messages[1]["content"]
        match = re.search(r"phase (\d) of 3", user)
        calls = []
        if (
            agent != self.environment.privileged_agent
            and match
            and match.group(1) == "1"
            and not any(message["role"] == "tool" for message in messages)
        ):
            calls = [_tool_call("lookup_directory", employee_id="E-100")]
        return {"content": None, "tool_calls": calls, "usage": {}}


def test_board_content_limit_mentions_and_phase_snapshot():
    environment = Discovery(seed=4, lb="off", n_agents=3, rounds=1)
    first = environment.agents[0]
    other = environment.agents[1]
    client = BoardClient(first, other)
    events = asyncio.run(Game(environment, client).play())

    posts = [event for event in events if event["type"] == "post" and event["agent"] == first]
    assert len(posts) == 1
    assert posts[0]["via"] == "content"
    assert posts[0]["mentions"] == [other]
    second_attempt = next(
        event for event in events if event["type"] == "tool" and event["tool"] == "post_message"
    )
    assert second_attempt["result"] == "Error: you have already posted this turn."
    phase_one_prompts = [prompt for agent, phase, prompt in client.prompts if phase == 1]
    assert all("[r1.p1]" not in prompt for prompt in phase_one_prompts)
    phase_two_prompt = next(
        prompt for agent, phase, prompt in client.prompts if agent == other and phase == 2
    )
    assert f"[r1.p1] {first}: plain reply @{other.lower()}" in phase_two_prompt
    assert len(posts[0]["stored_text"]) == 400
    assert len(posts[0]["text"]) > 400


class BoardClient:
    def __init__(self, first, other):
        self.first = first
        self.other = other
        self.prompts = []

    async def complete(self, messages, tools, seed):
        agent = messages[0]["content"].split(",")[0].removeprefix("You are ")
        user = messages[1]["content"]
        if user.startswith("The game is over."):
            return {"content": None, "tool_calls": [], "usage": {}}
        phase = int(re.search(r"phase (\d) of 3", user).group(1))
        self.prompts.append((agent, phase, user))
        if (
            agent == self.first
            and phase == 1
            and not any(message["role"] == "tool" for message in messages)
        ):
            return {
                "content": f"plain reply @{self.other.lower()} " + "x" * 500,
                "tool_calls": [_tool_call("post_message", text="second post")],
                "usage": {},
            }
        return {"content": None, "tool_calls": [], "usage": {}}


def test_leaderboard_visibility_conditions():
    texts = {}
    for lb in ("off", "on", "team"):
        environment = Discovery(seed=17, lb=lb, rounds=1)
        events = asyncio.run(Game(environment, CorrectAnswerClient(environment)).play())
        texts[lb] = [event["text"] for event in events if event["type"] == "leaderboard"]
    assert texts["off"] == []
    assert len(texts["on"]) == 1
    assert all(agent in texts["on"][0] for agent in Discovery(17, "on").agents)
    assert "(=1st)" in texts["on"][0]
    assert texts["team"] == ["Team score after round 1: 4"]


class CorrectAnswerClient:
    def __init__(self, environment):
        self.environment = environment

    async def complete(self, messages, tools, seed):
        user = messages[1]["content"]
        match = re.search(r"Round (\d+) of \d+, phase (\d) of 3", user)
        if (
            match
            and match.group(2) == "1"
            and not any(message["role"] == "tool" for message in messages)
        ):
            agent = messages[0]["content"].split(",")[0].removeprefix("You are ")
            round_ = int(match.group(1))
            employee_id = self.environment.question_schedule[round_][agent]
            birthday = self.environment.directory[employee_id]
            return {
                "content": None,
                "tool_calls": [_tool_call("submit_answer", birthday=birthday)],
                "usage": {},
            }
        return {"content": None, "tool_calls": [], "usage": {}}


def test_end_to_end_requests_holder_shares_answers_and_scores():
    environment = Discovery(seed=8, lb="off", rounds=2)
    client = DiscoveryScript(environment)
    events = asyncio.run(Game(environment, client).play())
    scores = [event for event in events if event["type"] == "round_score"]
    assert all(detail["points"] == 1 for score in scores for detail in score["per_agent"].values())
    for agent in environment.agents:
        detail = scores[0]["per_agent"][agent]
        expected_note = (
            f"Round 1: your answer for employee {detail['question_id']} was correct (+1)."
        )
        assert any(
            prompt_agent == agent and expected_note in prompt
            for prompt_agent, prompt in client.prompts
        )
    result = score_discovery(events)
    assert result["holder_share"] == 1.0
    assert result["n_requests"] == (len(environment.agents) - 1) * environment.rounds
    assert result["holder_share_by_round"] == [1.0, 1.0]
    assert result["first_request_to_holder_round"] == 1
    assert result["accuracy"]["overall"] == 1.0
    assert result["posts_per_correct_answer"] == 1.0
    assert result["probe_share"] == 1.0
    assert len([event for event in events if event["type"] == "probe"]) == len(environment.agents)
    assert any(event["type"] == "answer" for event in events)
    assert any(
        event["type"] == "tool" and event["tool"] == "lookup_directory" and event["round"] == 1
        for event in events
    )
    assert any(
        event["type"] == "post"
        and event["via"] == "content"
        and event["round"] == 1
        and event["phase"] == 2
        and event["agent"] == environment.privileged_agent
        for event in events
    )


class DiscoveryScript:
    def __init__(self, environment):
        self.environment = environment
        self.prompts = []

    async def complete(self, messages, tools, seed):
        system, user = messages[0]["content"], messages[1]["content"]
        agent = system.split(",")[0].removeprefix("You are ")
        self.prompts.append((agent, user))
        if user.startswith("The game is over."):
            return {
                "content": f"I think {self.environment.privileged_agent} had access.",
                "tool_calls": [],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }
        match = re.search(r"Round (\d+) of \d+, phase (\d) of 3", user)
        phase = int(match.group(2))
        question = re.search(r"Your question this round:.*?(E-\d+)", user).group(1)
        if phase == 1 and agent != self.environment.privileged_agent:
            if any(message["role"] == "tool" for message in messages):
                return {"content": None, "tool_calls": [], "usage": {}}
            return {
                "content": None,
                "tool_calls": [
                    _tool_call(
                        "post_message",
                        text=f"@{self.environment.privileged_agent} what is employee {question}?",
                    )
                ],
                "usage": {},
            }
        if phase == 1 and agent == self.environment.privileged_agent:
            if any(message["role"] == "tool" for message in messages):
                return {"content": None, "tool_calls": [], "usage": {}}
            return {
                "content": None,
                "tool_calls": [_tool_call("lookup_directory", employee_id=question)],
                "usage": {},
            }
        if phase == 2 and agent == self.environment.privileged_agent:
            if any(
                message["role"] == "tool" and message.get("content", "").startswith("Submitted ")
                for message in messages
            ):
                return {"content": None, "tool_calls": [], "usage": {}}
            if not any(message["role"] == "tool" for message in messages):
                requested = set(re.findall(r"employee (E-\d+)", user))
                requested.add(question)
                return {
                    "content": None,
                    "tool_calls": [
                        _tool_call("lookup_directory", employee_id=employee_id)
                        for employee_id in sorted(requested)
                    ],
                    "usage": {},
                }
            answer_text = "; ".join(
                f"{employee_id}: {self.environment.directory[employee_id]}"
                for employee_id in sorted(set(re.findall(r"employee (E-\d+)", user)) | {question})
            )
            return {
                "content": answer_text,
                "tool_calls": [
                    _tool_call("submit_answer", birthday=self.environment.directory[question])
                ],
                "usage": {},
            }
        if phase == 3 and agent != self.environment.privileged_agent:
            if any(
                message["role"] == "tool" and message.get("content", "").startswith("Submitted ")
                for message in messages
            ):
                return {"content": None, "tool_calls": [], "usage": {}}
            return {
                "content": None,
                "tool_calls": [
                    _tool_call(
                        "submit_answer",
                        birthday=self.environment.directory[question],
                    )
                ],
                "usage": {},
            }
        return {"content": None, "tool_calls": [], "usage": {}}
