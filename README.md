# local-computer

A local Perplexity Computer / Claude Cowork clone powered entirely by your own
Ollama models. No cloud APIs. No paid subscriptions.

## Architecture

```
run.sh "goal"
  └─ scripts/orchestrator.py       ← plans multi-stage mission via qwen3:14b
       └─ scripts/navigation_agent.py   ← observe / decide / execute loop
            ├─ scripts/observer.py      ← reads page DOM → structured state
            ├─ scripts/executor.py      ← executes browser actions
            ├─ scripts/agent_memory.py  ← tracks state history & failures
            ├─ scripts/claim_extractor.py ← extracts factual claims from pages
            ├─ scripts/source_scoring.py  ← scores source trustworthiness
            ├─ scripts/claim_cluster.py   ← deduplicates & clusters claims
            └─ scripts/event_logger.py    ← streams events → outputs/agent_events.jsonl

run_dashboard.sh
  └─ scripts/localhost_server.py   ← serves dashboard at http://localhost:8765
       └─ dashboard/index.html     ← live agent view
```

## Model routing

| Role     | Model              | When used                             |
|----------|--------------------|---------------------------------------|
| router   | qwen3:4b           | Quick task classification             |
| actor    | qwen3:4b           | Per-step browser action decisions     |
| planner  | qwen3:14b          | Mission planning & claim extraction   |
| analyst  | qwen3:14b          | Evidence synthesis & summarization    |
| heavy    | deepseek-r1:14b    | Hard math / deep reasoning tasks      |

Edit `configs/models.json` to swap any model.

## Setup (first time only)

```bash
git clone https://github.com/arpituppal2/local-computer.git
cd local-computer
chmod +x run.sh run_dashboard.sh open_dashboard.sh
./run.sh "Find the latest UCLA math department news and summarize it"
```

`run.sh` creates the venv, installs deps, and installs Playwright Chromium automatically.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally with models from `configs/models.json`
- Arc browser **or** headless Chromium (auto-fallback)

## Usage

```bash
# Run a research mission
./run.sh "Research the best open-source LLMs in 2025 and write a markdown summary"

# In a second terminal — watch the agent live
./run_dashboard.sh
./open_dashboard.sh   # opens http://localhost:8765
```

## File layout

```
local-computer/
├── configs/
│   ├── models.json          ← model assignments (edit freely)
│   └── runtime.json         ← ports, timeouts, browser choice
├── scripts/
│   ├── ollama_client.py     ← single Ollama wrapper (all scripts import this)
│   ├── orchestrator.py      ← mission planner + handoff
│   ├── navigation_agent.py  ← main agent loop  ← YOU ADD THIS
│   ├── observer.py          ← DOM → structured state
│   ├── executor.py          ← action → browser effect
│   ├── agent_memory.py      ← loop memory & stuck detection
│   ├── event_logger.py      ← JSONL event stream
│   ├── claim_extractor.py   ← LLM factual claim extraction
│   ├── source_scoring.py    ← source trust heuristic
│   ├── claim_cluster.py     ← Jaccard dedup & clustering
│   └── localhost_server.py  ← dashboard HTTP server
├── dashboard/
│   └── index.html           ← live dashboard
├── outputs/                 ← screenshots + agent_events.jsonl (gitignored)
├── logs/                    ← run logs (gitignored)
├── legacy/                  ← old scripts kept for reference
├── requirements.txt
├── run.sh
├── run_dashboard.sh
└── open_dashboard.sh
```

## Extending

- **New tool**: add `scripts/tools/my_tool.py`, import in `navigation_agent.py`
- **Swap a model**: edit `configs/models.json`
- **New browser action**: add a branch to `executor.py`
