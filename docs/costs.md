# Costs

What we have spent on compute so far, how to check it, and what the next runs should cost. Figures marked **measured** come from Modal's billing API (`modal billing`). Figures marked **estimate** have not been run yet.

## Spend to date (Modal workspace `sophie-chong-sc`)

| Date (UTC) | What | GPU | Cost | Source |
|---|---|---|---|---|
| 2026-11-07 | Smoke test: 1 birthday game per condition (3 games), plus deploy | L40S | **$0.39** | measured |
| 2026-11-08 | Discovery smoke runs 1–2 with Qwen3-8B (6 games, plus a crashed attempt) | L40S | **$1.19** | measured |
| 2026-11-08 | Discovery smoke run 3 with Qwen3-32B (3 games), incl. cold start | H100 | **$0.90** | measured |
| 2026-11-08 | Discovery smoke run 4 with Qwen3-32B, sequential turns (3 games) | H100 | **$0.74** | measured |

Breakdown of the $0.39 (Modal's `swarm-vllm` app, 18:00–19:00 UTC):

| Resource | Cost |
|---|---|
| L40S GPU | $0.346 |
| Network egress | $0.029 |
| CPU | $0.012 |
| Memory | $0.005 |

The month-to-date summary shows $0.45 metered and **$0.00 billed**:
- **Credits:** $0.39 of compute was covered by workspace credits.
- **Allowances:** network egress ($0.029) and Volume storage ($0.026, the cached model weights) were covered by the free allowances.

Other services: Adaption $0, since no datasets or training jobs have been created. No paid model APIs have been used.

## What the smoke test used (measured)

| | |
|---|---|
| Model / GPU | Qwen3-8B on 1× L40S ($1.95/h) |
| Games | 3 (equal, hidden, announced; seed 0), 3 agents, max 10 rounds |
| Model calls | 89 |
| Tokens | 79,563 in / 4,569 out (~27k in per game) |
| First server start | 252 s, including downloading the weights to the Volume and compiling |
| Playing the games | 28 s, all 3 at once |
| GPU time billed | ~10.6 min ($0.346 ÷ $1.95/h), of which about 5 min is the idle wait before scale-down |

Most of the cost was start-up and idle time, not the games themselves.

## Projections (estimate)

| Run | Approx. GPU time | Approx. cost |
|---|---|---|
| Pilot: 60 games (3 conditions × 20 seeds), Qwen3-8B, L40S | 2–3 min start (cached) + 3–5 min games + 5 min idle | **$0.35–0.50** |
| Each further 60-game batch while the server is still warm | 3–5 min | $0.10–0.20 |
| Same pilot with 5 agents | more calls and longer prompts, about 2–3× the game time | $0.50–0.80 |
| Same pilot with gpt-oss-20b on L40S | similar to Qwen3-8B | $0.35–0.60 |
| Same pilot with Qwen3-32B on H100 ($3.95/h) | roughly 3× slower and 2× the hourly rate | $1.50–3.00 |
| A day of development (server up on and off for ~3–6 h) | 3–6 h | $6–12 |

Even the larger options are a small part of the $140 available, so the budget is not what limits us. Developer time and the server's start-up time are.

## How to check spend

```bash
uv run --group modal modal billing report --for today -r h --show-resources   # per app, per hour
uv run --group modal modal billing summary                                   # month to date, incl. credits applied
```

The remaining credit balance is not shown by the CLI. Check it on the Modal dashboard under Settings → Usage & Billing.

## Spend controls in place

- `max_containers=1` in `infra/vllm_server.py`: at most one GPU at a time, however many requests arrive.
- `scaledown_window=5 min`: the GPU stops 5 minutes after the last request.
- To stop paying at once: `uv run --group modal modal app stop swarm-vllm`.

## Prices used

From [modal.com/pricing](https://modal.com/pricing), checked 2026-11-07:

| GPU | Price per hour |
|---|---|
| L40S | $1.95 |
| A100 80 GB | $2.50 |
| H100 | $3.95 |
| H200 | $4.54 |
