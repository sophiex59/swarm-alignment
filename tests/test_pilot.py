import json
from unittest.mock import patch

import pytest

from swarm_alignment.pilot import (
    Config,
    Environment,
    MockBackend,
    OllamaBackend,
    Reply,
    main,
    parse_action,
    run_episode,
)


def act(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


def test_access_and_initial_information():
    env = Environment(Config(seed=12))
    record = next(iter(env.records))
    for agent in env.ids:
        initial = env.initial_messages(agent)
        assert env.records[record] not in json.dumps(initial)
        assert "privileged" not in json.dumps(initial)
        assert json.loads(initial[1]["content"])["questions"] == env.assignments[agent]
        result = env.execute(agent, act("lookup_record", record_id=record))
        if agent == env.privileged:
            assert result["code"] == env.records[record]
        else:
            assert result == {"error": "Retrieval unavailable"}


def test_submission_ownership_single_attempt_and_no_answer_leak():
    env = Environment(Config())
    q = env.assignments["A"][0]
    assert "error" in env.execute(
        "B", act("submit_answer", question_id=q["question_id"], answer="?")
    )
    wrong = env.execute("A", act("submit_answer", question_id=q["question_id"], answer="wrong"))
    assert wrong == {"correct": False, "your_score": 0}
    assert env.records[q["record_id"]] not in json.dumps(wrong)
    assert "error" in env.execute(
        "A", act("submit_answer", question_id=q["question_id"], answer=env.records[q["record_id"]])
    )
    q2 = env.assignments["A"][1]
    env.execute(
        "A",
        act("submit_answer", question_id=q2["question_id"], answer=env.records[q2["record_id"]]),
    )
    assert env.score["A"] == 1


def test_matched_conditions_and_hidden_scores():
    one = Environment(Config(seed=3))
    two = Environment(Config(seed=3, objective="team", scores_visible=False))
    assert one.records == two.records
    assert one.privileged == two.privileged
    assert one.schedule == two.schedule
    two.score["A"] = 9
    two.submissions["A"]["A-1"] = True
    assert two.execute("B", act("read_leaderboard")) == {"agents": two.ids, "scores": "unavailable"}


def test_board_attribution_limits_and_visibility():
    env = Environment(Config())
    assert "error" in env.execute("A", act("post_to_board", text="x" * 501))
    env.execute("A", act("post_to_board", text="hello"))
    assert "hello" not in json.dumps(env.initial_messages("B"))
    result = env.execute("B", act("read_board", after_message_id=0))
    assert result["messages"] == [{"id": 1, "agent": "A", "text": "hello"}]
    assert env.execute("B", act("read_board", after_message_id=1)) == {"messages": []}
    # Returned observations cannot mutate stored public messages.
    result["messages"][0]["text"] = "tampered"
    assert env.board[0]["text"] == "hello"


@pytest.mark.parametrize(
    "action",
    [
        [],
        {"tool": "read_leaderboard", "arguments": {"agent_id": "A"}},
        {"tool": "read_board", "arguments": {"after_message_id": True}},
        {"tool": "read_board", "arguments": {"after_message_id": -1}},
        {"tool": "submit_answer", "arguments": {"question_id": "A-1", "answer": 123}},
        {"tool": "exec", "arguments": {}},
    ],
)
def test_rejects_invalid_actions(action):
    with pytest.raises(ValueError):
        parse_action(json.dumps(action))


def test_mock_episode_and_full_private_logs(tmp_path):
    output = tmp_path / "episode"
    config = Config(agents=3, turns=7)
    summary = run_episode(config, MockBackend(), output, {"name": "mock"})
    assert summary["status"] == "completed"
    assert summary["scripted_mock"] is True
    assert summary["total_correct"] == 2
    assert summary["restricted_correct"] == 0
    assert summary["invalid_actions"] == 0
    events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
    assert len(events) == config.agents * config.turns
    assert len([e for e in events if e["agent"] == "A"]) == config.turns
    for event in events:
        messages = event["request_messages"]
        assert json.loads(messages[1]["content"])["agent_id"] == event["agent"]
    with pytest.raises(FileExistsError):
        run_episode(config, MockBackend(), output, {})


def test_invalid_model_output_consumes_one_turn(tmp_path):
    class BrokenFormat:
        def complete(self, messages):
            return Reply("not JSON", {})

    result = run_episode(Config(agents=2, turns=2), BrokenFormat(), tmp_path / "run", {})
    assert result["invalid_actions"] == 4
    assert result["total_correct"] == 0


def test_backend_failure_is_not_a_behavioural_result(tmp_path):
    class Offline:
        def complete(self, messages):
            raise RuntimeError("unavailable")

    output = tmp_path / "run"
    with pytest.raises(RuntimeError):
        run_episode(Config(), Offline(), output, {})
    assert json.loads((output / "summary.json").read_text())["status"] == "failed"


def test_cli_matrix(tmp_path):
    main(["--matrix", "--turns", "2", "--agents", "2", "--output", str(tmp_path)])
    summaries = [json.loads(p.read_text()) for p in tmp_path.glob("*/*/summary.json")]
    assert len(summaries) == 4
    assert {(s["objective"], s["scores_visible"]) for s in summaries} == {
        ("individual", True),
        ("individual", False),
        ("team", True),
        ("team", False),
    }


def test_ollama_payload_and_usage():
    backend = OllamaBackend("test-model", "http://localhost:11434", 0.7, 8192, 10)
    with patch("swarm_alignment.pilot.urlopen") as request:
        request.return_value.__enter__.return_value.read.return_value = json.dumps(
            {
                "message": {"content": '{"tool":"read_leaderboard","arguments":{}}'},
                "prompt_eval_count": 100,
                "eval_count": 20,
            }
        )
        reply = backend.complete([{"role": "user", "content": "test"}])
        payload = json.loads(request.call_args.args[0].data)
    assert payload["stream"] is False
    assert payload["options"]["num_ctx"] == 8192
    assert payload["messages"] == [{"role": "user", "content": "test"}]
    assert reply.usage["eval_count"] == 20


def test_adaption_forwards_only_this_agents_history():
    from swarm_alignment.pilot import AdaptionBackend

    backend = AdaptionBackend.__new__(AdaptionBackend)
    backend.model = "example"
    from unittest.mock import Mock

    backend.client = Mock()
    backend.client.responses.create.return_value = Mock(output_text="{}", usage=None)
    messages = [{"role": "system", "content": "policy"}, {"role": "user", "content": "own task"}]
    assert backend.complete(messages).text == "{}"
    backend.client.responses.create.assert_called_once_with(
        model="example", instructions="policy", input=messages[1:]
    )
