# swarm-alignment

## Getting started

You need [git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/) (macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`).

```sh
git clone https://github.com/sophiex59/swarm-alignment.git
cd swarm-alignment
uv sync                      # makes .venv/, installs Python 3.12 and the dev tools if needed
uv run pytest                # should end with "10 passed"
uv run ruff check .          # lint
uv run ruff format --check . # formatting
```

These are the same checks CI runs on every PR. Run Python through uv (`uv run python ...`) so it uses the project environment. See [CONTRIBUTING.md](CONTRIBUTING.md) for adding dependencies, the branch/PR workflow and where results go.

## Running the birthday pilot

See [docs/running.md](docs/running.md): deploy the model server on Modal, play the games, score the logs. [docs/results.md](docs/results.md) has the results so far, and [docs/costs.md](docs/costs.md) tracks what each run has cost and what the next ones should cost. [docs/environments-plan.md](docs/environments-plan.md) is the build plan for the three environments in Will's spec: discovery, inference and safety.
