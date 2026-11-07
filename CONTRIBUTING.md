# Contributing

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) to manage the Python project and its environment, then run:

```sh
uv sync
```

## Running checks locally

Ruff checks and formats Python code; pytest runs the tests. Run:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

To automatically apply formatting, run `uv run ruff format .`. CI runs the same checks on every pull request.

## Adding dependencies

Add runtime dependencies with `uv add <pkg>` and development dependencies with `uv add --dev <pkg>`. Commit `pyproject.toml` and `uv.lock` together.

## Workflow

Create a branch for each change and open a pull request; drafts are welcome. Keep pull requests small and ask at least one teammate to review before merging to `main`. Protecting `main` is a GitHub setting that the repository owner turns on under **Settings → Branches**.

## Results and data

Do not commit raw model outputs or logs. The `results/`, `logs/`, and `data/` directories are gitignored; store larger artifacts on a Modal volume or in a Hugging Face dataset. Commit only small summary tables.

## Secrets

Put API keys in a local `.env` file, which is gitignored. Never put keys in code or commits.

## Suggested layout as the project grows

This is a suggestion only; these folders are **not** created now:

```text
docs/
configs/
prompts/
src/swarm_alignment/
tests/
notebooks/   # exploration only
results/     # not in git
```
