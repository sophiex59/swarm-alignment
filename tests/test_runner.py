import sys

import pytest

from swarm_alignment import runner


@pytest.mark.parametrize(
    ("flags", "mode", "thinking", "max_tokens"),
    [
        ([], "live", True, 3072),
        (["--mode", "phased"], "phased", False, 512),
        (["--mode", "phased", "--thinking"], "phased", True, 3072),
    ],
)
def test_mode_defaults_resolve_model_settings(
    monkeypatch, tmp_path, flags, mode, thinking, max_tokens
):
    observed = {}

    class FakeClient:
        def __init__(self, **kwargs):
            observed["client_kwargs"] = kwargs

        async def check_authentication(self):
            return None

    async def fake_run_all(args, client, out, model_params):
        observed["args"] = args
        observed["model_params"] = model_params

    monkeypatch.setattr(runner, "OpenAIChatClient", FakeClient)
    monkeypatch.setattr(runner, "wait_for_health", lambda base_url: 0)
    monkeypatch.setattr(runner, "run_all", fake_run_all)
    monkeypatch.setenv("SWARM_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    monkeypatch.setattr(sys, "argv", ["runner", "--out", str(tmp_path), *flags])

    runner.main()

    assert observed["args"].mode == mode
    assert observed["client_kwargs"]["enable_thinking"] is thinking
    assert observed["client_kwargs"]["max_tokens"] == max_tokens
    assert observed["model_params"]["mode"] == mode
    assert observed["model_params"]["enable_thinking"] is thinking
    assert observed["model_params"]["max_tokens"] == max_tokens


def test_live_mode_rejects_simultaneous_turn_order(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--out", str(tmp_path), "--mode", "live", "--turn-order", "simultaneous"],
    )

    with pytest.raises(SystemExit) as error:
        runner.main()

    assert error.value.code == 2
