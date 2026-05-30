### THIS PROJECT IS DEPRECIATED AS OF NOW. NO UPDATES ARE PLANNED AND THIS IS AN INCOMPLETE REPO.

# Locus

Locus is a local Perplexity Computer / Codex-style workspace assistant for
macOS and Windows, powered by plugins, Playwright, and optional Ollama models.

Local model use is **opt-in**. By default the dashboard starts in model-free
workspace mode so it can show plugins, uploads, connector status, and hardware
model recommendations without loading anything into RAM/VRAM.

External AI and cloud worker routing are also **off by default**. The default
runtime is local-only: local files, local plugins, local browser automation, and
local Ollama only when you explicitly enable models.

---

## Architecture

```
run.sh "goal"
  ├─ model-free default
  │    └─ scripts/workspace_agent.py     ← deterministic local workspace agent
  │         ├─ scripts/workspace_planner.py  ← natural task → plugin tools
  │         └─ scripts/plugin_runtime.py     ← filesystem/shell/git/upload/connectors
  └─ --allow-models
       └─ scripts/orchestrator.py          ← local research and synthesis loop
            ├─ scripts/router.py            ← local-first workflow/search/browse routing
            ├─ scripts/subagents.py         ← local Ollama dispatch; external routes require opt-in
            └─ scripts/navigation_agent.py  ← observe/decide/execute research loop
                 ├─ scripts/observer.py     ← DOM → structured state
                 ├─ scripts/executor.py     ← browser actions
                 ├─ scripts/agent_memory.py ← loop memory & stuck detection
                 ├─ scripts/claim_extractor.py
                 ├─ scripts/source_scoring.py
                 ├─ scripts/claim_cluster.py
                 └─ scripts/event_logger.py → outputs/agent_events.jsonl

run_dashboard.sh
  └─ scripts/ui_server.py          ← dashboard + websocket at http://localhost:8765
       └─ dashboard/index.html     ← live agent view

run_app.sh
  ├─ scripts/ui_server.py          ← local dashboard server
  └─ scripts/locus_macos_app.py    ← macOS menu-bar overlay host
```

---

## First-Time Setup

`./run.sh`, `./run_dashboard.sh`, `run_app.sh`, and `run.ps1` on Windows now
bootstrap automatically:

- detect macOS or Windows and choose OS-specific safety defaults
- install Python automatically when it is missing
- create `.venv`
- install `requirements.txt`
- install Playwright Chromium
- install Ollama when recommended model downloads are enabled
- download the recommended local model files
- start the dashboard

When the dashboard opens for the first time, it runs the remaining setup as a
system-style setup wizard and streams each step: folder creation, plugin
registry checks, hardware model recommendation, model downloads, workspace
indexing, safety limits, Full Disk Access status, and Accessibility status for
global shortcuts. Downloading models pulls model files only; Locus still does
not run inference until local model mode is explicitly enabled.

Automatic Python setup uses Homebrew on macOS and `winget` on Windows. Automatic
Ollama setup uses `brew install --cask ollama` on macOS and `winget install
Ollama.Ollama` on Windows. To disable automatic model/Ollama downloads for a
test run:

```bash
LOCAL_COMPUTER_AUTO_INSTALL_MODELS=0 LOCAL_COMPUTER_AUTO_INSTALL_OLLAMA=0 ./run.sh
```

```powershell
$env:LOCAL_COMPUTER_AUTO_INSTALL_MODELS="0"; $env:LOCAL_COMPUTER_AUTO_INSTALL_OLLAMA="0"; .\run.ps1
```

On macOS, `install_dock_app.sh` builds `~/Applications/Locus.app` as a menu-bar
accessory app and installs a login item. First run opens a full-screen
translucent setup surface over the desktop, then reveals the floating command
overlay instead of a browser tab. Option+Space summons the overlay; a quick
double-Command press is also wired as a summon gesture when Accessibility/Input
Monitoring permissions allow it.

The app is written for non-technical users: setup starts automatically, shows
plain-language progress, uses local Apple system fonts, and avoids
command-line-only instructions during normal use.

Resource warning: it is highly recommended not to use other apps while Locus is
running. Local GPU usage is capped at 95% through the legacy
`LOCAL_COMPUTER_MAX_GPU_PERCENT=95` setting; on macOS, Locus also sets
`PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.95`. RAM/model caps are applied separately
from the detected OS profile.

You can rerun the setup checks from the terminal:

```bash
python scripts/setup_manager.py --status
python scripts/setup_manager.py --app-setup
python scripts/setup_manager.py --open-full-disk-access
python scripts/setup_manager.py --open-accessibility
```

macOS requires the user to grant Full Disk Access and Accessibility in System
Settings; Locus can open the right settings screens and verify access afterward,
but macOS does not allow an app to grant these permissions to itself. Windows
does not use those macOS permission screens; keep Locus in folders you own and
approve Windows security prompts if protected-folder access is needed.

Press `Cmd+K` in the dashboard or `Option+Space` in the Mac app to open the
Locus Command Center. It can run setup, re-check permissions, open the Plugin
Center, index the current folder, inspect git status, show TODOs, and start
local workspace tasks without loading a model.

The Safety Center shows local-only state, model/cloud routing status, RAM and
GPU caps, enabled plugin risk categories, connector readiness, and runtime
warnings in one place.

The Plugin Center shows every local plugin, connector readiness, declared tools,
implemented tools, and risk labels. Plugin enable/disable state is persisted in
`configs/plugins.json`.

## App Icons

The committed Locus icon set lives under `assets/icons/`. The source artwork is
a neutral glass lotus mark made from layered petal shapes for a macOS-style
system identity.

- `assets/icons/locus-app-icon.svg` is the editable source artwork.
- `assets/icons/locus-app-icon-1024.png` is the high-resolution preview/export.
- `assets/icons/macos/Locus.icns` is used by `install_dock_app.sh`.
- `assets/icons/windows/Locus.ico` is used by `install_windows_shortcut.ps1`.

Regenerate the platform exports after changing the source art with:

```bash
python scripts/generate_app_icons.py
```

---

## Runtime Modes

| Mode | How to start | What runs |
|------|--------------|-----------|
| Model-free workspace mode | `./run.sh` or `./run.sh "show plugin status"` | No local model calls. Uses deterministic workspace, plugin, upload, connector, and model recommendation tools. |
| Local model mode | `./run.sh --allow-models "goal"` or `LOCAL_COMPUTER_ALLOW_MODELS=1 ./run.sh` | Uses Ollama-backed planning, synthesis, memory embeddings, and browser research. |
| External AI mode | `./run.sh --allow-external-ai --allow-models "goal"` | Allows browser chatbot routing. Off by default for local-only operation. |
| Cloud worker mode | `./run.sh --allow-cloud-workers --allow-models "goal"` | Allows remote worker dispatch. Off by default. |

## Model Routing

Run `python scripts/model_selector.py` to recommend models for the current OS,
CPU, GPU, VRAM, and RAM. The selector itself prints download commands only; the
first-run setup wizard can use that recommendation to pull the model files
automatically.

The selector is conservative on Apple Silicon unified memory and slightly more
reserved on Windows so the OS, browser automation, and app plugins keep room to
breathe:

| Installed RAM | Default local behavior |
|---------------|------------------------|
| 8 GB | tiny models, 2048 context, one loaded model, plugin-first routing |
| 12-16 GB | 1.7B/4B control models, one local job, bounded browser tabs |
| 24 GB+ | larger synthesis/heavy roles only when the RAM budget allows it |

Recommendations also check current available RAM and Windows NVIDIA VRAM. If the
machine is already under memory pressure, or if an RTX laptop has limited VRAM,
Locus lowers its effective model budget and downgrades roles before it downloads
anything.

Set a user RAM cap from the CLI, PowerShell, environment, or dashboard Model
Selector panel:

```bash
./run.sh --max-ram-gb 4.5 "show model recommendation"
LOCAL_COMPUTER_MAX_RAM_GB=6 python scripts/model_selector.py
python scripts/model_selector.py --simulate-ram-gb 16 --simulate-available-ram-gb 3
```

```powershell
.\run.ps1 -MaxRamGb 4.5 "show model recommendation"
```

Set a GPU cap, if needed:

```bash
LOCAL_COMPUTER_MAX_GPU_PERCENT=90 ./run.sh
```

Caps below 6 GB work, but are intentionally warned as slower and more fallback
heavy. Caps below 4 GB are expected to produce more errors.

Edit `configs/models.json` only when you want manual assignments, or write a
generated recommendation to `configs/models.recommended.json` with:

```bash
python scripts/model_selector.py --write-config
```

## Plugins

Plugins are manifest-driven contracts under `plugins/*/plugin.json`. The
registry is loaded by `scripts/plugin_manager.py` and surfaced in the dashboard.
Current built-ins:

- `filesystem` for local files and workspace context
- `shell` for local command execution
- `git` for repository inspection
- `github` for `gh`/token-backed GitHub workflows
- `email` for IMAP/SMTP or browser-backed email workflows
- `browser` for Playwright web automation
- `uploads` for dashboard file uploads
- `memory` for local query history
- `workspace` for repo indexing, project briefing, health reports, TODO reports, and run history
- `automations` for local scheduled Locus task definitions and due-task execution

Inspect the registry without running a model:

```bash
python scripts/plugin_manager.py --json
```

Run an implemented plugin tool directly:

```bash
python scripts/plugin_runtime.py filesystem.search_text '{"query":"plugin_runtime"}'
python scripts/plugin_runtime.py shell.run_command '{"command":"pwd"}'
python scripts/plugin_runtime.py workspace.workspace_brief '{}'
python scripts/plugin_runtime.py workspace.health_report '{}'
python scripts/plugin_runtime.py workspace.plugin_diagnostics '{}'
python scripts/plugin_runtime.py automations.list_automations '{}'
```

In the dashboard or one-shot workspace mode, precise tool calls use:

```text
@tool filesystem.read_file {"path":"README.md","max_chars":1200}
@tool git.git_status {}
@tool workspace.workspace_brief {}
@tool email.draft_email {"to":"person@example.com","subject":"Draft","body":"Nothing is sent automatically."}
@tool browser.open_page {"url":"http://127.0.0.1:8765"}
@tool automations.create_automation {"name":"Daily repo check","prompt":"workspace health","schedule":"every 1 day"}
```

Run the local acceptance suite without starting models:

```bash
LOCAL_COMPUTER_ALLOW_MODELS=0 LOCAL_COMPUTER_SKIP_MODEL_VALIDATE=1 python scripts/locus_acceptance.py
```

Natural model-free requests also route to tools:

```bash
./run.sh "search for plugin_runtime"
./run.sh "run command pwd"
./run.sh "write file notes/todo.txt with Review plugin runtime"
./run.sh "git status"
./run.sh "what is this repo"
./run.sh "show todos"
./run.sh "run history"
```

### External AI Policy

This project is local-only by default. High-complexity tasks are staged into
smaller local model calls and plugin steps instead of silently opening ChatGPT,
Claude, Gemini, Copilot, or Perplexity.

Optional browser chatbot automation still exists for users who explicitly opt in:

```bash
LOCAL_COMPUTER_ALLOW_EXTERNAL_AI=1 ./run.sh --allow-models "task"
```

Remote worker dispatch is also disabled unless `LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS=1`
or `./run.sh --allow-cloud-workers` is used.

---

## Setup (first time)

```bash
git clone https://github.com/arpituppal2/Locus.git
cd Locus
chmod +x run.sh run_dashboard.sh open_dashboard.sh
./run.sh "show model recommendation"
```

`run.sh` creates the venv, installs deps, and installs Playwright Chromium
automatically. Without `--allow-models`, one-shot tasks use model-free workspace
mode.

### Pull the local models

```bash
python scripts/model_selector.py
# Then pull only the models printed in the download plan.
# Nothing is downloaded unless you run ollama pull or pass --pull.
```

---

## Usage

```bash
# Standard local research mission
./run.sh --allow-models "Research the best open-source LLMs in 2026 and write a markdown summary"

# M1/M2 Air 8 GB style budget
./run.sh --max-ram-gb 4.5 "show model recommendation"

# Model-free workspace / plugin checks
./run.sh "show plugin status"
./run.sh "show model recommendation"
./run.sh "list files"

# Watch the agent live (second terminal)
./run_dashboard.sh && ./open_dashboard.sh
```

---

## Requirements

- macOS on Apple Silicon; 8 GB RAM is supported with the safe tier
- Python 3.11+
- [Ollama](https://ollama.com) only when `--allow-models` is used
- Playwright Chromium (auto-installed by `run.sh`)

---

## File Layout

```
Locus/
├── configs/
│   ├── models.json          ← manual model assignments
│   ├── model_catalog.json   ← hardware-aware recommendation catalog
│   ├── plugins.json         ← plugin registry settings
│   └── runtime.json         ← ports, timeouts, browser choice
├── plugins/                 ← plugin manifests and connector contracts
├── assets/icons/            ← source, macOS .icns, and Windows .ico app icons
├── scripts/
│   ├── hardware_profile.py     ← CPU/GPU/RAM detection
│   ├── setup_manager.py        ← first-run setup checks and installers
│   ├── model_selector.py       ← safe model recommendation and pull plan
│   ├── plugin_manager.py       ← plugin registry + connector status
│   ├── plugin_runtime.py       ← executable built-in plugin tools
│   ├── workspace_index.py      ← durable repo index + project briefing
│   ├── run_history.py          ← persistent per-workspace run history
│   ├── workspace_planner.py    ← deterministic model-free tool planner
│   ├── workspace_agent.py      ← model-free workspace fallback
│   ├── upload_store.py         ← dashboard file upload storage
│   ├── ai_chatbot_subagent.py  ← optional external AI automation, off by default
│   ├── subagents.py            ← local dispatch plus explicit opt-in external routes
│   ├── router.py               ← local-first route selection
│   ├── orchestrator.py         ← local research and synthesis loop
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

- **Change RAM budget**: use dashboard Model Selector, `--max-ram-gb`, or `LOCAL_COMPUTER_MAX_RAM_GB`
- **New optional chatbot backend**: add entry to `BACKENDS` in `scripts/ai_chatbot_subagent.py`
- **New tool**: add `scripts/tools/my_tool.py`, import in `navigation_agent.py`
- **Swap a local model**: update `configs/model_catalog.json` or disable auto-selection and edit `configs/models.json`
- **New browser action**: add a branch to `executor.py`
