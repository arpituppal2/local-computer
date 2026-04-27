# local-computer

A local Perplexity Computer / Claude Cowork clone powered by Ollama + Playwright.  
Runs entirely on your Mac. **No cloud APIs. No paid subscriptions.**

When a task is too complex for a local model, the system automatically opens a
Playwright browser tab and uses a cloud AI chatbot (Gemini, ChatGPT, Claude,
Copilot, or Perplexity) as a subagent — just like a human would.

---

## Architecture

```
run.sh "goal"
  └─ scripts/orchestrator.py          ← plans mission; routes stages to local or chatbot
       ├─ scripts/router.py            ← complexity scoring + chatbot/workflow/browse routing
       ├─ scripts/subagents.py         ← parallel dispatch: local Ollama | chatbot UI | cloud worker
       │    └─ scripts/ai_chatbot_subagent.py  ← Playwright UI agent for Gemini/GPT/Claude/etc
       └─ scripts/navigation_agent.py  ← observe/decide/execute research loop
            ├─ scripts/observer.py     ← DOM → structured state
            ├─ scripts/executor.py     ← browser actions
            ├─ scripts/agent_memory.py ← loop memory & stuck detection
            ├─ scripts/claim_extractor.py
            ├─ scripts/source_scoring.py
            ├─ scripts/claim_cluster.py
            └─ scripts/event_logger.py → outputs/agent_events.jsonl

run_dashboard.sh
  └─ scripts/localhost_server.py   ← dashboard at http://localhost:8765
       └─ dashboard/index.html     ← live agent view
```

---

## Model Routing

| Role     | Model          | Trigger                                      |
|----------|----------------|----------------------------------------------|
| router   | qwen3:4b       | Quick task classification                    |
| actor    | qwen3:4b       | Per-step browser action decisions            |
| planner  | qwen3:8b       | Mission planning & claim extraction          |
| analyst  | qwen3:8b       | Evidence synthesis & summarization           |
| heavy    | qwen3:14b      | Hard math / deep reasoning (RAM permitting)  |
| **chatbot** | **Gemini / ChatGPT / Claude / Copilot / Perplexity** | **Complexity ≥ 7/10 or explicit request** |

Edit `configs/models.json` to change models or the `chatbot_threshold`.

### Chatbot routing logic

When `complexity_score(goal) >= chatbot_threshold` (default 7), the system opens a
Playwright-controlled Chromium browser, navigates to the best-matched AI chatbot,
types the prompt, waits for the response, and returns the text — exactly as a
human would. This gives you GPT-4o / Gemini 2.5 / Claude Opus reasoning for free,
using your existing browser sessions.

Automatic backend selection:
- **Code / refactor / git** → Claude  
- **Math / proof / statistics** → ChatGPT  
- **Latest news / current events / search** → Perplexity  
- **Microsoft tools (Office, Teams, Excel)** → Copilot  
- **Everything else** → Gemini  

---

## Setup (first time)

```bash
git clone https://github.com/arpituppal2/local-computer.git
cd local-computer
chmod +x run.sh run_dashboard.sh open_dashboard.sh
./run.sh "Find the latest UCLA math department news and summarize it"
```

`run.sh` creates the venv, installs deps, and installs Playwright Chromium automatically.

### Pull the local models

```bash
ollama pull qwen3:4b    # router + actor (~2.6 GB)
ollama pull qwen3:8b    # planner + analyst (~5 GB)
# qwen3:14b is optional — tasks that need it auto-route to chatbot UI
```

---

## Usage

```bash
# Standard research mission (auto-routes heavy stages to chatbot)
./run.sh "Research the best open-source LLMs in 2026 and write a markdown summary"

# Force all stages to run as parallel subagents
./run.sh --parallel "Compare GPT-5 and Gemini 2.5 Pro"

# Direct chatbot subagent (bypass planning entirely)
./run.sh --chatbot gemini    "Prove the Cauchy-Schwarz inequality"
./run.sh --chatbot claude    "Refactor this Python file: ..."
./run.sh --chatbot chatgpt   "Solve: find all integer solutions to x^3 + y^3 = z^3"
./run.sh --chatbot perplexity "Latest news about Anthropic model releases"
./run.sh --chatbot copilot   "Generate an Excel formula for compound interest"

# Watch the agent live (second terminal)
./run_dashboard.sh && ./open_dashboard.sh
```

---

## Chatbot Login

The chatbot subagent opens a **visible** (non-headless) browser so you can log in
on first use. Once logged in, Playwright reuses the session for the duration of the
run. Sessions are NOT persisted between runs — you may need to log in again.

To avoid re-logging-in: run the mission once manually with `--chatbot <backend>`,
complete the login, then run your actual task immediately after in the same session.

---

## Requirements

- macOS 14 Sonoma+ (tested on M4 MacBook Pro 16 GB)
- Python 3.11+
- [Ollama](https://ollama.com) running locally
- Playwright Chromium (auto-installed by `run.sh`)

---

## File Layout

```
local-computer/
├── configs/
│   ├── models.json          ← model assignments + chatbot_threshold
│   └── runtime.json         ← ports, timeouts, browser choice
├── scripts/
│   ├── ai_chatbot_subagent.py  ← ★ NEW: Playwright UI agent for cloud chatbots
│   ├── subagents.py            ← parallel dispatch: local | chatbot | cloud
│   ├── router.py               ← complexity scoring + route selection
│   ├── orchestrator.py         ← mission planner + chatbot-aware dispatch
│   ├── navigation_agent.py     ← main research loop
│   ├── ollama_client.py        ← Ollama wrapper (bug fix: call_json arg order)
│   ├── observer.py             ← DOM → structured state
│   ├── executor.py             ← action → browser effect
│   ├── agent_memory.py         ← loop memory & stuck detection
│   ├── long_term_memory.py     ← persistent cross-session memory
│   ├── event_logger.py         ← JSONL event stream
│   ├── claim_extractor.py      ← LLM factual claim extraction
│   ├── source_scoring.py       ← source trust heuristic
│   ├── claim_cluster.py        ← Jaccard dedup & clustering
│   └── localhost_server.py     ← dashboard HTTP server
├── dashboard/
│   └── index.html              ← live dashboard
├── outputs/                    ← screenshots + agent_events.jsonl (gitignored)
├── logs/                       ← run logs (gitignored)
└── legacy/                     ← old scripts kept for reference
```

## Extending

- **New chatbot backend**: add entry to `BACKENDS` in `scripts/ai_chatbot_subagent.py`  
- **Change complexity threshold**: edit `chatbot_threshold` in `configs/models.json`  
- **New tool**: add `scripts/tools/my_tool.py`, import in `navigation_agent.py`  
- **Swap a local model**: edit `configs/models.json`  
- **New browser action**: add a branch to `executor.py`  
