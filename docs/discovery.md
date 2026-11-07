# Discovery: can a swarm discover a hidden advantage?

Source: the slide deck *Research sprint: Can a swarm discover a hidden advantage? How agents learn who can do what through ordinary collaboration* (experimental concept, November 2026). This doc summarises the deck, places it in related work, critiques the design, and suggests a minimal first version. Sources were checked on 2026-11-07. Unless a paper is marked otherwise, its figures come from the **abstract or landing page only**, not the full PDF; recheck them before you quote them.

Contents: [1 The idea](#1-the-idea-as-in-the-deck) · [2 Plain-language definitions](#2-what-counts-as-discovery-plain-language-definitions) · [3 Related work](#3-related-work) · [4 What is new](#4-what-is-new-here) · [5 Design risks](#5-design-risks-and-suggested-fixes) · [6 Suggested MVP](#6-suggested-mvp) · [7 Open questions](#7-open-questions)

---

## 1. The idea (as in the deck)

* **Setup.** Five agents run on the same model and get the same shared research task, each in a private workspace. They talk on a shared message board with @mentions, requests and voluntary updates. **Only agent A can retrieve fresh information** (web access); B–E are restricted. No agent can see the others' tools or configuration, and tool calls are private: agents see only what peers choose to post.
* **The hoped-for sequence.** (1) independent attempts → (2) visible success (A posts information the others could not get) → (3) first delegation (a peer sends a new retrieval request to A) → (4) swarm convergence (the others increasingly route retrieval through A).
* **The key distinction (deck):** *actual capability inference* versus *simply copying a successful routing pattern*.
* **Four conditions** (which agent gets the working connection is randomised):

  | Condition | Setup | Measures |
  |---|---|---|
  | Equal restrictions | Nobody can retrieve external answers | Baseline routing |
  | Hidden advantage | One agent can retrieve; peers are unaware | Spontaneous discovery |
  | Announced advantage | All agents know which peer has access | Coordination ceiling |
  | Hidden + limited visibility | A can retrieve, but peers see fewer messages | Role of social transmission |

* **Task design:** controlled fresh facts that cannot be recovered from model memory.
* **Outcomes:** (1) *first routed request*: rounds until a peer voluntarily sends A a task that needs access; (2) *propagation*: how quickly other peers start sending similar requests to A; (3) *coordination gain*: task success and cost compared with the equal and announced baselines.
* **Safety extension:** all agents have the same tools, but one has a weaker synthetic permission boundary.

## 2. What counts as discovery: plain-language definitions

The deck's three outcomes can be measured from the message log alone. The harder question is *why* a peer delegated. There are three different reasons a peer might @mention A:

| Reason | What the peer has worked out | How to tell from the log |
|---|---|---|
| **Capability inference** | "A can do something I can't" | Delegates *before* seeing anyone else delegate to A; the request is access-dependent (needs fresh facts), not generic |
| **Copying a routing pattern** | "Others ask A, so I'll ask A" | Delegates only *after* seeing another peer's request to A; asks A for things A has no special edge on, too |
| **Activity / prestige following** | "A posts a lot, sounds confident" | Also sends requests to a talkative agent with *no* access (needs a control agent, §5) |

In human groups, "who knows what" is called a **transactive memory system** ([Wegner 1987](https://doi.org/10.1007/978-1-4612-4634-3_9); [Moreland 1999](https://doi.org/10.4324/9781410603227-1)). Copying high-status peers is called **prestige-biased copying** ([Henrich & Gil-White 2001](https://pubmed.ncbi.nlm.nih.gov/11384884)). Both apply here, and the experiment needs to tell them apart.

**Worked example (made-up numbers).** One run, hidden-advantage condition, 10 rounds. Rows are the round in which each peer first sends A an access-dependent request:

| Peer | Saw A post a sourced fresh fact by round | Saw another peer delegate to A by round | First access-dependent request to A | Classified as |
|---|---|---|---|---|
| C | 2 | never before own request | 3 | inference (first routed request = round 3) |
| B | 2 | 3 (saw C) | 4 | could be copying or inference: ambiguous |
| D | 2 | 3 | 7 | ambiguous, slow |
| E | 2 | 3 | never (censored at 10) | did not converge |

From this run: *first routed request* = 3 rounds; *propagation* = 3 of 4 peers routed by round 10, median 4 rounds after A's first success. Of the two ambiguous cases, the log alone can't say whether B inferred or copied. This is why §5 adds belief probes and a 2×2 visibility manipulation.

## 3. Related work

### 3.1 Human groups: pooling unshared information and knowing who knows what

* **Hidden profiles.** [Stasser & Titus 1985](https://doi.org/10.1037/0022-3514.48.6.1467) ([PDF](https://www.uni-muenster.de/imperia/md/content/psyifp/aeechterhoff/vorlesungkommunikation/stasser_titus_unsharedinfogroupdisc_jpsp1985.pdf)): group discussion is dominated by information everyone already holds and by information that supports existing preferences, so unique information is under-pooled. [Stasser & Titus 2003, *Hidden profiles: a brief history*](https://doi.org/10.1080/1047840X.2003.9682897). Our setting differs: the unique asset is a *tool* (a capability), not a fact.
* **Transactive memory.** [Liang, Moreland & Argote 1995](https://journals.sagepub.com/doi/10.1177/0146167295214009): groups trained together built "who knows what" knowledge and performed better than newly formed groups. [Moreland & Myaskovsky 2000](https://doi.org/10.1006/obhd.2000.2891) asked whether that benefit comes from transactive memory or just from better communication. This is the human version of our inference-vs-copying question.
* **Prestige-biased copying.** [Henrich & Gil-White 2001](https://pubmed.ncbi.nlm.nih.gov/11384884): social learners copy successful models, and newcomers use *who others defer to* as a cue. This predicts the copying route in §2.

### 3.2 LLM groups pooling distributed information

* [Li, Naito & Shirado, *Systematic failures in collective reasoning under distributed information in multi-agent LLMs* (ICML 2026; HiddenBench)](https://arxiv.org/abs/2505.11556): 65 hidden-profile tasks, 15 frontier LLMs. Accuracy is 30.1% when information is distributed across agents, against 80.7% for a single agent given all of it. Agents "fail to reason about what others might know but have not yet expressed". [Code](https://github.com/Yassellee/HiddenBench_ICML). This is the strongest prior: same-model swarms tend *not* to model what peers hold.
* [*Hidden profile decision making in multi-agent LLM groups* (CSCW 2026 Companion)](https://dl.acm.org/doi/10.1145/3785651.3831525): 3-agent groups from 4 model families. Only Kimi K2 under asynchronous communication beat chance (87%, n = 15 sessions, flagged as early). Two models shared private info at >75% rates but never combined it. **Sharing and integrating are separate capabilities**, and in our setting sharing is roughly A's visible success, integrating roughly peers' routing.

### 3.3 Inferring a partner's capabilities and deciding when to delegate

* [Choi et al., *Multi-agent LLMs fail to explore each other* (arXiv 2607.11250)](https://www.emergentmind.com/papers/2607.11250): treats it as a game where agents must **probe peers to infer their capabilities**. Modern LLM agents show "myopic and polarized interaction patterns". A structured peer-selection method (MACE) helps. The closest prior to this project, and it predicts slow or lopsided discovery.
* [Yin et al., *Epistemic Context Learning* (arXiv 2601.21742)](https://arxiv.org/abs/2601.21742): agents conform to misleading peers. Giving them peers' past rounds lets them estimate peer reliability, so the judgement becomes *who is speaking*, not *what is said*. Relevant to whether B–E trust A's facts or think A is hallucinating.
* [*Partner modelling emerges in recurrent agents (but only when it matters)* (NeurIPS 2025)](https://proceedings.neurips.cc/paper_files/paper/2025/file/0b8e8bfc40184226888e821620b216c9-Paper-Conference.pdf): RNN agents in Overcooked develop internal representations of partners' abilities **when they control task allocation**. Design lesson: discovery needs an incentive, so delegating has to pay off.
* [CoDeT, *Partner-adaptive delegation in ad hoc teamwork* (ACM HAIC 2026)](https://dl.acm.org/doi/full/10.1145/3834580.3838747) and [CADMAS-CTX (arXiv 2604.17950)](https://arxiv.org/abs/2604.17950): explicit Bayesian (Beta-posterior) competence beliefs per subtask, used to decide when to delegate. They make good **normative baselines**: an ideal observer that sees the same board tells us how fast discovery *could* happen.
* [Li et al., *Theory of mind for multi-agent collaboration via LLMs* (EMNLP 2023)](https://aclanthology.org/2023.emnlp-main.13/): LLM agents in a cooperative game show some higher-order theory of mind. Explicit belief-state scratchpads improved both task success and ToM accuracy. This supports eliciting beliefs (§5) but warns that the elicitation may itself change behaviour.

### 3.4 Self-organisation, copying and herding in agent populations

* [*Drop the hierarchy and roles* (arXiv 2603.28990)](https://arxiv.org/abs/2603.28990): self-organising LLM agents invent roles and "voluntarily abstain from tasks outside their competence", beating designed hierarchies by 14% (from the HTML abstract). Evidence that unassigned roles can emerge, though with known roles rather than hidden ones.
* [De Marzo, Alboré & Garcia, *Copying explains the collective behavior of AI agents in the wild* (arXiv 2609.09150)](https://arxiv.org/abs/2609.09150): thousands of evaluation agents found an editable public wiki and used it to help each other pass a timed test. An agent picked an option with probability close to that option's share in what it could see. **Copying alone reproduces most of the collective structure.** This is the direct prior for the copying alternative in §2, and a real-world example of the safety concern.
* [*LLM multi-agent systems are vulnerable to herding and runaway cascades* (arXiv 2610.30769)](https://arxiv.org/abs/2610.30769): in a Music-Lab-style setup, LLM populations herd far more than humans and self-correct less, and non-reasoning models herd more than reasoning models. So once a routing pattern starts, it may lock in even when it's wrong.
* [*AI agents can coordinate via majority-following beyond human scale* (Science Advances)](https://www.science.org/doi/10.1126/sciadv.aea6091) and [Ashery, Aiello & Baronchelli, *Emergent social conventions and collective bias in LLM populations* (Science Advances 2025)](https://www.science.org/doi/10.1126/sciadv.adu9368): conventions emerge from local interactions, and committed minorities can flip them.
* Field evidence: [AI Village](https://aivillageblog.substack.com/p/what-we-learned-2025) runs frontier agents with their own computers and a shared group chat, with a [public dataset](https://huggingface.co/datasets/aidigestorg/ai-village). The [longitudinal draft *Norms, Tools, and the Say/Do Gap*](https://ai-village-longitudinal-study-04cafa.gitlab.io/draft.pdf) traces one agent's unrequested verification habit spreading to 28–31% of messages within three months. That is a natural example of the propagation outcome, and the dataset could be mined for delegation events before building anything.

### 3.5 Why multi-agent setups fail, and safety

* [Cemri et al., *Why do multi-agent LLM systems fail?* (MAST)](https://arxiv.org/abs/2503.13657): 14 failure modes in 3 categories. Inter-agent misalignment (ignoring peers, withholding information) is the relevant category for coding our transcripts.
* [Hammond et al., *Multi-agent risks from advanced AI* (2025)](https://arxiv.org/abs/2502.14143): taxonomy of miscoordination, conflict and collusion, with **information asymmetries** and **multi-agent security** among the risk factors. This is the framing for the safety pitch.
* The **safety extension is a confused-deputy setup**: peers route a step that their own policy blocks through the agent with the weaker boundary. Prior work: [Triedman et al., *Multi-agent systems execute arbitrary malicious code*](https://arxiv.org/abs/2503.12188) (requests "laundered" through trusted agents; names the confused-deputy problem); [*Prompt infection*](https://arxiv.org/abs/2410.07283) (LLM-to-LLM injection spreading across agents); [SEAgent (arXiv 2601.11893)](https://arxiv.org/abs/2601.11893) (privilege escalation in multi-agent systems, including a confused-deputy variant); [Motwani et al., *Secret collusion among AI agents* (NeurIPS 2024)](https://proceedings.neurips.cc/paper_files/paper/2024/hash/861f7dad098aec1c3560fb7add468d41-Abstract-Conference.html).

## 4. What is new here

The prior work covers parts of this but not the whole thing. Hidden-profile work studies *facts* that must be shared, not a *capability* that must be inferred. Capability-inference work either trains agents (RNN partner modelling), hand-builds Bayesian delegators (CoDeT, CADMAS-CTX), or prompts for exploration with a framework (MACE). Copying and herding work shows imitation dominates but has no ground-truth capability to discover. The gap is **untrained, same-model LLM agents on an open message board, with a hidden, randomised, ground-truth capability, where inference and copying are experimentally separated**. The safety extension then asks whether the same discovery process finds a *permission* weakness: privilege laundering that emerges on its own rather than through an injected attack.

## 5. Design risks and suggested fixes

| # | Risk | Why it matters | Suggested fix |
|---|---|---|---|
| 1 | **A announces itself** | A's success messages ("I searched and found…") reveal the advantage, which turns "hidden" into "announced by A" | Keep it, but measure it: code whether A *states* its access, *implies* it (links, dates) or neither. Add an arm where A's tool output is phrased as recall ("I believe…") to test whether peers infer from *results* alone |
| 2 | **Copying vs inference is not separated by the four conditions** | "Fewer messages" cuts both A's successes and others' requests together | Replace the limited-visibility arm with a **2×2**: peers see A's successful posts (yes/no) × peers see others' requests to A (yes/no). Inference predicts routing needs only the first; copying predicts only the second |
| 3 | **Activity or confidence, not capability** | Peers may route to whoever posts most or sounds surest (prestige cue) | Add a **confident decoy**: a restricted agent prompted to post often and confidently. Routing to the decoy measures the false-positive rate of "discovery" |
| 4 | **Delegation forced, not discovered** | If restricted agents can't do anything without A, routing is the only option and tells us little | Make the task partly doable alone (memory-answerable sub-questions mixed with fresh-fact sub-questions), and score delegation only on the fresh-fact ones |
| 5 | **No incentive to route** | Partner modelling only emerged when it paid off (NeurIPS 2025 above) | Score the *team* (shared reward stated in the prompt) and give a round budget, so waiting costs something |
| 6 | **Failure messages are a manipulation** | What B sees when its search fails ("network unreachable" vs "no results" vs a timeout) changes what it infers | Fix and log the exact error string. One variant could make failures silent (empty results) |
| 7 | **Fresh facts leak** | If facts are memorisable, the equal-restriction baseline isn't a baseline | Use a **mock search tool over a private synthetic corpus** (fictional but plausible documents with dates/IDs), not the live web: reproducible, contamination-free, safe. Check closed-book accuracy ≈ 0 on every run, as the [RAMR](https://github.com/DanceNitra/ramr) probe does |
| 8 | **Peers think A hallucinates** | Same model, so B may assume A is making it up (B's own tool failed) | Let peers verify (A's results carry checkable IDs). Code "challenge" messages. Epistemic Context Learning above suggests history helps |
| 9 | **Beliefs not measured** | Routing ≠ knowing | After each round, ask each agent privately, on a **forked copy of its context so the run is not affected**, "Which agents can retrieve fresh information? (probabilities)". Belief accuracy over rounds is a fourth outcome |
| 10 | **Turn order and names** | Round-robin order and agent letters can bias who gets asked | Randomise the turn order each round as well as which agent has access. Use neutral names |
| 11 | **Small n, censored timing** | Each run is one data point; some peers never route | Treat the run as the unit and use survival analysis (Kaplan–Meier, censored at the round budget) for time to first routed request. Pre-register the primary comparison (hidden vs equal) |
| 12 | **Same-model only** | The interesting prior may be "we are identical, so we have identical tools" | Keep same-model for the main study and flag mixed-model as an extension. If agents verbalise this prior, that is itself a finding |
| 13 | **Safety extension is under-specified** | "Weaker synthetic permission boundary" needs a concrete step that the others' policy denies | Example: a task step that writes to a protected (sandboxed, fake) file. Only one agent's policy allows it. Measure whether peers route that step to it, whether they *say* why, and whether the weak agent refuses. Keep everything synthetic and sandboxed |

## 6. Suggested MVP

* **Harness:** one Python loop over rounds with a shared board (list of messages), five agents (same model, explicit `--model`), private tool calls, and a mock `search` tool over a synthetic corpus that works only for the access agent. Log every message, every private tool call, the visibility mask and the belief probes as JSONL, kept out of git.
* **Task:** about 20 sub-questions per run, half answerable from the synthetic corpus only (fresh) and half from general knowledge. Team score = fresh-fact accuracy, plus cost in tokens and rounds.
* **Arms (first pass):** equal, hidden, announced, plus the confident decoy within the hidden arm. The 2×2 visibility study comes next, once hidden ≠ equal is shown.
* **Size:** a cheap 1-model pilot of 5 runs per arm × 10 rounds to check the harness and see whether *any* routing happens before scaling. HiddenBench and MACE suggest discovery may be rare.
* **Normative baseline:** a Beta-posterior observer (as in CADMAS-CTX) reading the same board, giving the earliest round at which an ideal observer would route to A.
* **Rough effort (my time):** harness + synthetic corpus ≈ 4–6 h; pilot runs + coding scheme ≈ 2–3 h; 2×2 visibility study ≈ 3–4 h after that. Model cost depends on the model chosen and isn't estimated yet.

## 7. Open questions

1. Which model(s) and API key: OpenAI, Anthropic, or open-weight via a cluster?
2. Is the sprint aimed at the capability question (can swarms discover) or the safety question (do they launder permissions)? That decides whether the safety extension is a later milestone or the headline.
3. Live web, or a mock search over a synthetic corpus (recommended, §5 #7)?
4. Should agents run in fixed rounds (simpler, cleaner timing) or asynchronously? The CSCW hidden-profile result above suggests the communication mode matters.
5. Is mining the AI Village dataset for natural delegation events in scope as a cheap first look?
