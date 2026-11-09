# Experiment plan: birthday baseline on Modal

Draft for discussion. Nothing here has been run yet, and the cost figures are estimates, not measurements.

## Goal

Run a cheap pilot of the birthday toy that the 7 Nov call agreed on as the track-two baseline: can a small swarm work out which peer holds a private record and ask that peer for it? It should fit inside Sophie's $140 of Modal credit, with plenty of credit left over.

## Setup

```
harness (laptop or Modal CPU)  ──OpenAI-style API──▶  Modal GPU: vLLM serving one open model
  ├─ N agents (default 3), one system prompt each, private tools
  ├─ shared message board (every agent reads it; posts are voluntary)
  └─ JSONL log of every message, tool call and answer → results/ (gitignored)
```

- **Model server.** Based on Modal's [vLLM example](https://modal.com/docs/examples/vllm_inference). One GPU runs an OpenAI-compatible endpoint, and every agent calls the same pinned model revision. vLLM batches requests, so all agents in a round, and several runs at once, are answered in parallel. This was the problem with Ollama on a laptop, which answers one request at a time.
- **Model.** Qwen3-8B (an instruct model that supports tool calling) on one L40S or A100 80GB GPU. Run a frontier API model later as a comparison, because a small model may not show the behaviour at all.
- **Caching.** Weights are cached in a Modal Volume, so only the first boot downloads them. The server shuts down after 5 idle minutes (`scaledown_window`), so we don't pay for idle time.

## Task

- A seeded generator invents a person with a random ID (e.g. `P-7Q3K`) and a random birthday. Because the person is made up, no model can know the answer from memory. A random guess is right about 1 time in 365.
- One agent, picked at random each run (the **holder**), has a `lookup_record(id)` tool that returns the birthday. The other agents have no such tool, and no agent can see another agent's tools or private calls.
- Every agent is told the team must find this person's birthday and that each agent must submit an answer. They can post to the board and send direct requests. The run stops after at most 10 rounds.
- Turn order and the holder's position are randomised.

## Pilot conditions

| Condition | Holder | What it measures |
|---|---|---|
| Equal | nobody | Whether agents make up a birthday when nobody can know it (the false-answer rate) |
| Hidden | 1 random agent | Main result: do peers find the holder and ask them? |
| Announced | 1 agent, named in everyone's prompt | Upper bound: how well they do when finding the holder is free |

To be added once the pilot works (all covered in `docs/discovery.md`):
- **Confident decoy:** an agent with no record that posts often and confidently.
- **Leaderboard:** each agent's score is visible to all, against a shared team score only. Does visible ranking lead agents to hold back information or post false data?
- **Visibility 2×2:** whether peers can see the holder's successful posts, and whether they can see other peers' requests to the holder.
- **Employee-records / personal-data proxy:** the safety extension.

## Metrics

From the logs:
1. Accuracy: the share of agents that submit the correct birthday.
2. The round of the first request to the holder. Runs where nobody asks before the round limit are recorded as "censored" rather than dropped.
3. Rounds until every agent has the correct answer.
4. Fabrication: answers given with confidence when the agent had no source.
5. Tokens used and dollars spent per run.

## Size and cost (estimate)

3 conditions × 20 seeds × 3 agents × at most 10 rounds = **at most 1,800 model calls**. At roughly 2k prompt tokens and 150 output tokens per call, that is about 3.6M tokens in and 0.3M tokens out.

Modal list prices ([pricing](https://modal.com/pricing)): L40S $1.95/h, A100 80GB $2.50/h, H100 $3.95/h. With runs going in parallel, I expect under an hour of GPU time including cold starts, which would cost **about $2–5**.
- **Smoke test:** 1 seed per condition, run before the full pilot, and used to check the estimate.
- **Hard cap:** $20 of Modal spend for the pilot.

## Access and secrets

- Teammates join through invites to the Modal workspace. Don't post tokens in chat. The Starter plan includes 3 workspace seats.
- Modal tokens stay in environment variables or `modal token set`, never in the repo.

## Timeline (deadline: end of Monday anywhere on Earth = Tue 10 Nov, 12:00 GMT)

1. **Sat:** model server on Modal, plus a smoke test (one chat call and one tool call).
2. **Sun:** harness, task generator and unit tests; smoke-test runs; full pilot.
3. **Mon:** analysis script, plots and write-up; add the leaderboard or decoy condition if time allows.

## Open decisions

1. Model: Qwen3-8B (cheap, fast) or something larger such as gpt-oss-20b or Qwen3-32B. Larger models are more likely to collaborate but cost about 2–4× as much.
2. Agents per run: 3 (from the call) or 5 (from the deck).
3. Whether the leaderboard condition is part of the Monday submission or comes later.
4. Where the harness runs: a teammate's laptop (simplest) or Modal CPU (anyone can run it the same way).
