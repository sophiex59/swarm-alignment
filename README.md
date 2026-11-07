# swarm-alignment

## Getting started

You need [git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/) (macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`).

```sh
git clone https://github.com/sophiex59/swarm-alignment.git
cd swarm-alignment
uv sync                      # makes .venv/, installs Python 3.12 and the dev tools if needed
uv run pytest                # should end with "1 passed"
uv run ruff check .          # lint
uv run ruff format --check . # formatting
```

These are the same checks CI runs on every PR. Run Python through uv (`uv run python ...`) so it uses the project environment. See [CONTRIBUTING.md](CONTRIBUTING.md) for adding dependencies, the branch/PR workflow and where results go.

## Leaderboard collaboration pilot

Run a credential-free infrastructure check:

```sh
uv run python -m swarm_alignment.pilot --backend mock --matrix --turns 7
```

For a real local model, start Ollama and pull `qwen3.5:9b`, then run:

```sh
uv run python -m swarm_alignment.pilot --backend ollama --model qwen3.5:9b
```

See [the pilot guide](docs/leaderboard-pilot.md) for Adaption, paired experimental conditions,
private agent contexts, logs, costs and interpretation. Mock outputs are scripted, not findings.
