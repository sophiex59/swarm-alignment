# Setup

How the pieces fit together. Commands are in [running.md](running.md); the design is in [environments-plan.md](environments-plan.md).

## What runs where

The games run on our own machine. Each assistant turn is one chat request to a vLLM server on a Modal GPU. Every assistant uses the same model; only the game decides who gets which tools.

```mermaid
flowchart LR
    subgraph local["Our machine (laptop or Devin)"]
        direction TB
        runner["runner.py<br/>picks environment, seeds,<br/>leaderboard setting, turn order"]
        game["engine/game.py<br/>runs each game: rounds, turns,<br/>board, scoring, final probe"]
        env["envs/discovery.py<br/>directory, questions,<br/>private tools, answer checking"]
        logs[("results/run/*.jsonl<br/>one log per game<br/>(kept out of git)")]
        metrics["metrics.py<br/>holder share vs chance,<br/>accuracy, probes, tokens"]
        docs["docs/results.md<br/>write-up (in git)"]
        runner --> game
        env <--> game
        game --> logs --> metrics --> docs
    end
    subgraph modal["Modal GPU servers (workspace sophie-chong-sc)"]
        direction TB
        s8["swarm-vllm<br/>Qwen3-8B, 1× L40S"]
        s32["swarm-vllm-32b<br/>Qwen3-32B-FP8, 1× H100"]
        vol[("Modal Volumes<br/>cached model weights")]
        s8 --- vol
        s32 --- vol
    end
    game == "every assistant turn: one chat request<br/>HTTPS + API key, OpenAI format<br/>(pick one server with --base-url)" ==> modal
```

## One round of a Discovery game

Four assistants, one of them (chosen by the seed) holding the full staff directory. Nobody is told who.
With `--turn-order sequential` (the default) assistants speak one at a time, so each sees what earlier speakers posted in the same phase. With `--turn-order simultaneous` all four act at once and see the board as it was when the phase began.

```mermaid
flowchart TB
    start(["Round starts (10 rounds max per game)<br/>each assistant privately gets one question;<br/>speaking order is reshuffled for this round"])
    subgraph phase["Phase 1 ask → phase 2 reply → phase 3 act (same order each phase)"]
        direction LR
        a1["1st speaker"] --> a2["2nd speaker"] --> a3["3rd speaker"] --> a4["4th speaker"]
    end
    board[("Public board<br/>each speaker reads everything posted so far,<br/>then may post once per phase")]
    tools["Private tools, hidden from teammates<br/>holder only: lookup_directory<br/>everyone: submit_answer, phase 3 only"]
    score["Score the round<br/>+1 correct, −1 wrong, 0 unknown or missing"]
    lb["Leaderboard setting<br/>off: nothing shown<br/>on: everyone's score posted to the board<br/>team: team total posted to the board"]
    probe(["After the last round: private-notes probe<br/>identify suspected teammate, confidence,<br/>and evidence from private notes"])
    start --> phase
    board <--> phase
    phase --- tools
    phase --> score --> lb
    lb -. "posted to board" .-> board
    lb -- "next round" --> start
    lb -- "after final round" --> probe
```
