# swarm-alignment

Research sprint: **can a swarm discover a hidden advantage?** Five same-model agents share a research task and a message board; only one can retrieve fresh information. Do the others work out who can do what, and route requests to it, through ordinary collaboration, or do they just copy each other's routing?

## Docs

| File | Contents |
|---|---|
| [docs/discovery.md](docs/discovery.md) | The sprint idea, plain-language definitions of "discovery" vs copying, related work with links, design risks and fixes, a suggested MVP and open questions |

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
