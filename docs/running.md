# Running Discovery

This guide covers the Discovery runner. The older birthday pilot runner remains available as `swarm_alignment.run`.

## Start the model server

The server runs vLLM on Modal. Deploy it with the Modal secret `swarm-vllm` set to the API key you will use locally:

```bash
uv sync --group modal
uv run modal token new
# Create this key once and keep it outside the repository.
mkdir -p ~/.config/swarm-alignment
openssl rand -hex 24 > ~/.config/swarm-alignment/vllm_key
uv run modal secret create swarm-vllm VLLM_API_KEY="$(cat ~/.config/swarm-alignment/vllm_key)"
uv run modal deploy infra/vllm_server.py
```

Deployment prints the endpoint URL. If the `swarm-vllm` secret already exists, use its existing key or replace the secret and redeploy. The local key must match the key in that secret.

## Run a game

Put `SWARM_BASE_URL` and `VLLM_API_KEY` in the repo's `.env` file. Run through uv's env-file option; `source .env` is not required:

```bash
uv run --env-file .env python -m swarm_alignment.runner \
  --env discovery \
  --lb on \
  --seeds 0 \
  --n-agents 4 \
  --rounds 5 \
  --board-window 5 \
  --out results/discovery-smoke \
  --live
```

The runner waits for the server, checks the key against `/v1/models`, then starts games. `--lb` accepts `off`, `on`, or `team`; pass multiple values to compare conditions. Discovery supports 2–8 agents and 1–10 rounds. Each completed game writes a JSONL event log in the output directory. Live mode prints the same events as they happen.

Use a separate output directory when changing run settings. Existing game logs are skipped by filename, even if the settings differ; resume checks are not yet tied to a run manifest.

## Score logs

```bash
uv run python -m swarm_alignment.metrics results/discovery-smoke
```

This writes `scores.json` and prints a summary. Results from the older birthday pilot use a separate scorer:

```bash
uv run python -m swarm_alignment.score results/smoke
```

## Stop the server

```bash
uv run --group modal modal app stop swarm-vllm
```
