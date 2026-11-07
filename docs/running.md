# Running the birthday pilot

The design is in [modal-plan.md](modal-plan.md). This page covers how to run it.

## What runs where

- **`infra/vllm_server.py`** runs on a Modal GPU (L40S by default). It serves Qwen3-8B with vLLM through an OpenAI-compatible API. It shuts down after 5 idle minutes and is limited to one GPU (`max_containers=1`).
- **`swarm_alignment.run`** runs on your machine and plays the games. Each game writes one JSONL log to `results/<name>/`, which is kept out of git.
- **`swarm_alignment.score`** turns those logs into a summary table and `scores.json`.

## One-time setup (per Modal workspace)

```bash
uv sync --group modal
uv run modal token new                 # or: modal token set, after joining the workspace
# Pick an API key for the server. Keep it outside the repo.
mkdir -p ~/.config/swarm-alignment && openssl rand -hex 24 > ~/.config/swarm-alignment/vllm_key
uv run modal secret create swarm-vllm VLLM_API_KEY="$(cat ~/.config/swarm-alignment/vllm_key)"
uv run modal deploy infra/vllm_server.py   # prints the server URL
```

If you join a workspace where the `swarm-vllm` secret already exists, ask whoever created it for the key, or replace the secret with `modal secret create --force` and redeploy. The endpoint is public, but every `/v1` request needs the key.

## Run

```bash
export SWARM_BASE_URL=https://<workspace>--swarm-vllm-serve.us-east.modal.run
export VLLM_API_KEY="$(cat ~/.config/swarm-alignment/vllm_key)"

# smoke test: 1 game per condition
uv run python -m swarm_alignment.run --seeds 0 --out results/smoke
uv run python -m swarm_alignment.score results/smoke

# pilot: 20 seeds x 3 conditions
uv run python -m swarm_alignment.run --seeds 0-19 --out results/pilot
uv run python -m swarm_alignment.score results/pilot
```

- The first request starts the server, which takes several minutes the first time because it downloads the model weights. Later starts load them from the Modal Volume cache.
- Re-running with the same `--out` skips games that already have a log, so an interrupted run can be resumed.
- **Options:**
  - `--n-agents` (default 3)
  - `--max-rounds` (default 10)
  - `--conditions equal hidden announced`
  - `--concurrency` (number of games run at once; default 32)
- To stop paying straight away: `uv run modal app stop swarm-vllm`. Deploy again to bring it back.

## Game rules (as implemented)

- **Agents and turns.** Each run has `--n-agents` agents with randomly drawn names and a random turn order. Every round, each agent gets one turn of up to 4 model calls.
- **What agents see.** An agent sees the board as it was at the start of the round, plus its own private notes. It never sees other agents' tools, tool calls or notes.
- **Tools.** Every agent has `post_message(text, to?)` and `submit_answer(birthday)`. Only the holder also has `lookup_record(person_id)`.
- **Answers.** An answer is `YYYY-MM-DD` or `unknown`, and each agent can submit only once.
- **When a game ends.** A game ends when every agent has submitted, or when it reaches the round limit.
- **Conditions:**

  | Condition | Holder | Prompt |
  |---|---|---|
  | `equal` | none | Nobody can know the answer |
  | `hidden` | one random agent | Nobody is told who the holder is |
  | `announced` | one random agent | Every prompt names the holder |

- **Model settings.** Qwen3's thinking mode is turned off (`enable_thinking=False`). Each model call gets a seed computed from (game seed, condition, agent, round, step).

## Log format

Each line of a log is one event. The `type` field says what kind:
- `task`: the person, their birthday, the agents and the holder
- `model_call`: content, tool calls and token counts
- `post`
- `lookup`
- `submit`
- `invalid_tool`
- `end`: final submissions and wall-clock time

`score.py` measures the "first request" round in two ways:
- **Strict:** a post addressed to the holder using `to`.
- **Loose:** a post that is either addressed to the holder or names them. This is the version in the summary table.
