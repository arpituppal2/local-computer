# local-computer

A local browser-agent platform inspired by Perplexity Computer + Claude Cowork, running entirely on your machine via [Ollama](https://ollama.ai) and [Playwright](https://playwright.dev).

## Models (your current Ollama list)

| Model | Role |
|---|---|
| `qwen3:4b` | Fast router, planners, small tasks |
| `qwen3:14b` | Smart planner, claim extraction |
| `deepseek-r1:14b` | Heavy reasoning / math |
| `llama3.1:8b` | General fallback |
| `qwen2.5:7b` | Alternative general |
| `gemma3:4b` | Lightweight alternative |

## Quick Start

```bash
# 1. Clone
git clone https://github.com/arpituppal2/local-computer.git
cd local-computer

# 2. Create venv + install deps
python3 -m venv .venv
source .venv/bin/activate
pip install playwright requests fastapi uvicorn httpx psutil
python -m playwright install chromium

# 3. Run the agent on a goal
python scripts/orchestrator.py "research the latest AI news and summarize in 5 bullets"

# 4. (Optional) Open the live dashboard in a second terminal
python scripts/localhost_server.py
# Then open http://localhost:8765
```

## Project Structure

```
local-computer/
├── scripts/                  # All agent Python modules
│   ├── orchestrator.py       # Top-level mission planner + entrypoint
│   ├── observer.py           # Reads page state into structured dict
│   ├── executor.py           # Executes structured actions via Playwright
│   ├── agent_memory.py       # Loop memory: states, actions, failures, evidence
│   ├── memory.py             # Simpler memory + loop-escape helper
│   ├── router.py             # Routes goal → mode (search/browse/workflow)
│   ├── subagents.py          # Picks subagent from goal + URL context
│   ├── search_controller.py  # Search-page specific action helpers
│   ├── candidate_policy.py   # Obvious actions (cookie banners etc.)
│   ├── page_skills.py        # Detects page type + provides hints
│   ├── query_rewriter.py     # Rewrites search queries for corroboration
│   ├── plan_browser.py       # LLM → deterministic JSON step plan
│   ├── plan_prose.py         # LLM → PROSE-specific step plan
│   ├── navigationbot.py      # Executes JSON step plans (deterministic)
│   ├── scraper.py            # Schema-based structured data extractor
│   ├── formfiller.py         # Fills web forms from JSON field manifests
│   ├── logintemplate.py      # Login + cookie capture template
│   ├── hnscraper.py          # HN top-5 scraper demo
│   ├── claim_extractor.py    # Extracts factual claims from article text
│   ├── claim_cluster.py      # Clusters + contradiction-flags claims
│   ├── source_scoring.py     # Domain trust + text quality scoring
│   ├── event_logger.py       # JSONL event stream writer
│   ├── live_dashboard.py     # Writes HTML dashboard from events
│   ├── localhost_server.py   # Serves dashboard at http://localhost:8765
│   ├── tab_manager.py        # Tracks open Playwright pages
│   ├── sys_limits.py         # Detects RAM/CPU and sets model options
│   └── ollama_hybrid.py      # CPU/GPU routing for Ollama prompts
├── app.py                    # FastAPI streaming agent server (port 8000)
├── webui/
│   ├── server.js             # Express API bridge for webui (port 5173)
│   ├── index.html            # Original dashboard UI
│   ├── package.json
│   └── package-lock.json
├── tasks/                    # JSON task/step plan files (git-ignored outputs)
├── outputs/                  # Screenshots, scraped data, events (git-ignored)
├── prompts/
│   ├── browser_steps.txt     # System prompt for step planner
│   └── browser_steps_prose.txt  # System prompt for PROSE planner
├── examples/
│   └── auto_mission.json     # Example mission JSON
├── run.sh                    # Bootstrap venv + run orchestrator
├── run_dashboard.sh          # Start localhost_server.py
└── open_dashboard.sh         # Open dashboard in browser
```

## Architecture Overview

```
User Goal
   │
   ▼
orchestrator.py  ──► plan_mission() via qwen3:14b  ──► auto_mission.json
   │
   ▼
[navigation_agent loop - coming soon: navigation_agent.py]
   │
   ├── observer.py         reads page → structured state
   ├── subagents.py        picks mode (search / browse / workflow)
   ├── search_controller.py  handles search-page specifics
   ├── candidate_policy.py   handles cookie banners etc.
   ├── page_skills.py        detects page type, provides hints
   ├── executor.py         executes action → Playwright
   ├── agent_memory.py     tracks history, detects loops
   ├── claim_extractor.py  pulls facts from articles
   ├── claim_cluster.py    groups + flags contradictions
   ├── source_scoring.py   scores source trust
   └── event_logger.py     writes outputs/agent_events.jsonl
                               │
                               ▼
                       localhost_server.py  →  http://localhost:8765
```

## What's Missing (next steps)

- [ ] `scripts/navigation_agent.py` — the main agentic loop that wires observer → subagent → executor → memory → evidence
- [ ] `dashboard/index.html`, `dashboard/app.js`, `dashboard/styles.css` — polished dashboard UI
- [ ] `scripts/file_agent.py` — local file reading/summarization (Claude Cowork side)
- [ ] Config file (`config.yaml`) to set models, ports, and paths without editing code

## Requirements

- macOS (tested) or Linux
- Python 3.10+
- [Ollama](https://ollama.ai) running locally with at least one model pulled
- Chromium installed via `playwright install chromium`
