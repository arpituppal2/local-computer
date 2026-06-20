## CHANGES.md

### Production readiness pass
- Added `scripts/release_check.py`, a model-free production gate covering JSON configs, plugin manifests, connector defaults, icon assets, shell/model launch guards, hardware model matrix, dashboard JavaScript, Python compilation, and the acceptance harness.
- Added GitHub Actions release checks on macOS and Windows with local models, automatic model downloads, external AI, and cloud workers disabled.
- Upgraded production runtime requirements and setup checks to Python 3.12+.
- Added safe free-port selection through `scripts/networking.py`; launchers now choose the first free localhost port instead of killing or failing on an occupied `8765`.
- Updated connector readiness so browser workflows are visible but do not count as configured cloud credentials.

### True Locus identity and local intelligence controls
- Replaced the in-app mark and generated macOS/Windows icon exports with the supplied blue-and-gold glass orbital Locus icon.
- Added a dashboard Intelligence slider (`xlow` through `max`), Learn Step-by-Step toggle, and Settings feature-gate summary for Browser Control, uploads/artifacts, Deep Research, and Model Council.
- Added a first-run shortcut rehearsal for double-Command summon and fn voice handoff without starting either action.
- Changed the default compute cap from 95% to 90%, exposed 50-99% settings control, and warn below 75% or above 90%.
- Updated local model tiers to `qwen2.5:3b`, `qwen2.5:14b`, and `llama3.1:70b` with model-free verification still safe by default.
- Added off-by-default cloud connector stubs for GitHub, Google Drive, Gmail, and Outlook in `configs/cloud_connectors.json`, plus a Google Drive plugin readiness card.

### Active control safety aura
- Added a glassy full-surface control veil for browser, app, and system control states, with live cursor motion, click rings, a trail, and a clear do-not-touch status pill.
- Wired browser and shell tool lifecycle events to automatically show and dismiss the control veil while local control is running.
- Exposed `window.locusControlVeil` hooks so native app-control surfaces can drive the same motion/click/aura layer.

### Reference-grade Mac setup and taskbar
- Reworked the native setup surface to cycle through full-screen desktop-style scenes for shortcut, taskbar, local files, app control, context awareness, voice mode, capabilities, security, and model orchestration.
- Collapsed first-run setup progress into a compact bottom rail so install/check status stays visible without making the setup feel like a normal app window.
- Refined the summon overlay into a single floating glass taskbar with a Locus mark, large `Start a task...` input, plus affordance, and icon-only context tools.

### Cinematic first-run setup
- Rebuilt first-run setup as a full-screen translucent desktop surface with staged launch, hotkey, command bar, voice, and local-first moments.
- Switched the dashboard to local Apple system fonts and removed CDN font/script dependencies from the app shell.
- Added subtle Web Audio feedback hooks for setup, completion, and voice mode; they are low-volume and never block setup.
- Updated the macOS menu-bar host so first run opens an interactive full-screen setup overlay before revealing the command surface.

### Native Mac menu-bar overlay
- Reworked the macOS app wrapper into a menu-bar accessory app with a floating transparent WebKit panel.
- Added a full-screen launch veil, menu-bar status item, Option+Space and double-Command summon hooks, and click-away poof/back behavior.
- Added microphone and speech-recognition usage descriptions for Voice Mode.

### Apple-style command surface
- Removed the purple visual system in favor of neutral glass, graphite, sage, ivory, and warm accent tones.
- Rebuilt the main task surface around a translucent command bar with 3D icon buttons for app context, folders, files, attachments, file creation, models, Plan Mode, and Voice Mode.
- Added Voice Mode UI with macOS speech hooks, local voice model recommendations, live transcript fill, and speech playback without starting local models by default.
- Added overlay placement settings for center, top, and bottom positions.

### Lotus logo and setup overlay polish
- Replaced the dashboard's rounded-square badge with a transparent layered lotus logo.
- Added `assets/icons/locus-logo.svg` as the standalone Locus logo source.
- Reworked first-time setup styling so it floats as a glowy system overlay instead of a modal app window.

### Cross-platform app icon assets
- Replaced the initial target-style icon with a lotus-flower mark built from layered petal shapes.
- Added a custom 2D Locus app icon source and generated exports for macOS and Windows.
- Wired `install_dock_app.sh` to install the committed macOS `.icns` asset instead of drawing a placeholder icon at install time.
- Added `install_windows_shortcut.ps1` so Windows shortcuts use the committed `.ico` asset.
- Shifted the dashboard visual language to lilac, violet, and lotus-glow surfaces.

### Locus repository rename
- Renamed the Python project metadata to `locus`.
- Updated setup documentation to use the `arpituppal2/Locus` repository name while keeping existing `LOCAL_COMPUTER_*` environment variables compatible.

### Locus Mac app polish
- Rebranded visible app surfaces to Locus, including the dashboard, Dock bundle wrapper, setup copy, and legacy launcher text.
- Reworked first-run setup into a full-screen system-style overlay with animated material, setup scan, status pulses, and success/failure states.
- Added a Locus Command Center on Cmd+K and Option+Space with setup, permission, plugin, workspace, git, TODO, upload, and recent-run actions.
- Added a local Plugin Center with per-plugin enable/disable, connector readiness, implemented-tool badges, and risk labels.
- Added a Safety Center that summarizes local-only state, model/cloud routing, RAM/GPU caps, plugin risk counts, connectors, and runtime warnings.
- Added in-app and native-window Option+Space support while the Mac app is active.
- Added Full Disk Access and Accessibility setup steps, settings openers, and permission recheck paths. macOS still requires the user to approve access in System Settings.

### Beginner-first app safety
- Added a plain-language dashboard resource warning telling users it is highly recommended not to use other apps while Locus is running.
- Added `max_gpu_percent` runtime policy with a default 90% cap, surfaced through setup status, model recommendations, and dashboard computer-fit text.
- Exported `LOCAL_COMPUTER_MAX_GPU_PERCENT=90` and `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.90` from launchers and resource policy.
- Simplified dashboard labels for non-technical users: tools, files, this folder, recent runs, references, computer fit, and setup wording.

### First-time setup flow
- Added `scripts/setup_manager.py` for repeatable setup status, launcher bootstrap, app-level setup, and streamed setup events.
- Updated `run.sh`, `run_dashboard.sh`, and `run_app.sh` to automatically create/repair `.venv`, install Python requirements, and install Playwright Chromium before launch.
- Added `/api/setup` plus WebSocket `setup`, `setup_status`, `setup_step`, `setup_done`, and `setup_error` handling in `scripts/ui_server.py`.
- Added a dashboard first-time setup panel that auto-starts when required setup is incomplete and shows each local install/check step as it runs.
- Setup writes local model recommendations and workspace indexes only; it does not pull models or run local inference.

### Apple Silicon low-memory optimization pass
- Added `scripts/resource_policy.py` for conservative unified-memory budgets, including 8 GB MacBook Air defaults, user RAM caps, model-usable RAM estimates, and Ollama/PyTorch environment defaults.
- Added current-memory-pressure detection so the effective RAM budget shrinks when a Mac is already low on available unified memory.
- Added `LOCAL_COMPUTER_MAX_RAM_GB`, `./run.sh --max-ram-gb`, and the dashboard Model Selector RAM control.
- Expanded `configs/model_catalog.json` into M-series tiers from 8 GB safe mode through 48 GB+ workstation mode, with role-level downgrades when a user RAM cap is too low.
- Updated `scripts/model_selector.py`, `scripts/ollama_client.py`, `scripts/orchestrator.py`, `scripts/subagents.py`, and planner/prose helpers to use the effective auto-selected model config instead of static defaults.
- Reduced low-RAM browser pressure with one sub-query at a time, one source tab at a time, fewer result URLs, source text caps, and optional automatic headless browser mode.
- Made browser chatbot and cloud worker routing explicit opt-in through `allow_external_ai`, `allow_cloud_workers`, `LOCAL_COMPUTER_ALLOW_EXTERNAL_AI`, and `LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS`; local-only is the default.

### Model-free platform mode
- Added `scripts/runtime_policy.py` and made local model use opt-in through `LOCAL_COMPUTER_ALLOW_MODELS=1`, `./run.sh --allow-models`, or `configs/runtime.json`.
- Updated `scripts/ollama_client.py` and `scripts/long_term_memory.py` so dashboard startup, memory listing, and workspace commands do not call or validate Ollama while models are disabled.
- Updated `run.sh`, `run_dashboard.sh`, `run_app.sh`, and `LocalComputer.py` to start safely without requiring or launching Ollama by default.

### Hardware model selector
- Added `scripts/hardware_profile.py`, `scripts/model_selector.py`, and `configs/model_catalog.json`.
- The selector detects CPU, GPU, RAM, and recommends role-based Ollama models plus pull commands without downloading or running models unless `--pull` is passed.

### Plugin and connector foundation
- Added manifest-driven plugin registry under `plugins/*/plugin.json`.
- Added `scripts/plugin_manager.py` for plugin discovery, goal matching, and connector status.
- Added built-in plugin contracts for filesystem, shell, git, GitHub, email, browser, uploads, and memory.
- Added `workspace.plugin_diagnostics` to report implemented vs pending plugin tools, connector readiness, and permission policy state.
- Implemented all declared built-in plugin tools, including local Playwright browser control, memory read/write/retrieval, GitHub `gh` issue/PR context, local Mail search, model recommendation, plan-only mode, and automations.
- Added the `automations` plugin plus `scripts/automation_store.py` and `scripts/automation_runner.py` for local scheduled task definitions and due-task execution.
- Added `scripts/locus_acceptance.py`, a local acceptance suite covering plugin diagnostics, model selection, workspace planning, plan mode, uploads, memory, automations, safety/permissions, and browser control without running local models.

### Uploads and model-free workspace agent
- Added `scripts/upload_store.py` for dashboard file upload storage under `uploads/`.
- Added `scripts/workspace_agent.py`, a deterministic fallback that can inspect plugins, model recommendations, uploads, git status, files, and local text search without local inference.
- Extended `scripts/ui_server.py` with `/api/runtime`, `/api/plugins`, `/api/models/recommendation`, and `/api/uploads`, plus WebSocket upload handling.
- Added `scripts/plugin_runtime.py` with executable built-in plugin tools for filesystem, shell, git, uploads, connector status, and email draft creation.
- Added `scripts/workspace_planner.py` for deterministic routing from model-free natural-language requests to plugin tool calls.
- Added compound model-free planning plus `workspace.health_report` for local project health, git, TODO, and verification guidance without inference.
- Added direct `@tool plugin.tool {json}` syntax for precise manual tool execution while models are disabled.
- Added `/api/tools` and WebSocket `tool` messages, plus dashboard tool activity streaming.
- Added `scripts/workspace_index.py` and the `workspace` plugin to build a durable repo index, project brief, TODO/FIXME report, and likely verification command list.
- Added `scripts/run_history.py` with per-workspace SQLite run history under `~/.local-computer/runs.db`.
- Added `/api/workspace/index` and `/api/runs`, and surfaced Workspace + Run History panels in the dashboard.

### dashboard/index.html
- Added model policy, plugin, model selector, and upload panels.
- Added a Settings surface for runtime, workspace, model selection, and automation status.
- Added a Plan Mode control that shows tool steps without executing them.
- Updated WebSocket URL selection to use the current dashboard host first.
- Removed decorative radial backgrounds and tightened radii for a more utilitarian tool UI.

### scripts/navigation_agent.py
- Replaced legacy API-first/browser fallback loop with Playwright Chromium-only multi-source retrieval.
- Added required Chromium launch flags for Apple Silicon (`--use-angle=metal`, sandbox/dev-shm flags, automation flag).
- Implemented Google organic top-result extraction (top 5), per-result tab fetching (max 3 concurrent), canonical URL capture, and clean visible text extraction.
- Added strict timeout hygiene (`page.goto(..., timeout=15000, wait_until="domcontentloaded")`, `page.evaluate(..., timeout=5000)`).
- Added per-tab `try/except` failure logging through `EventLogger`.
- Added guaranteed cleanup in `finally`: close all pages, context, and browser.

### configs/models.json
- Replaced role mappings with Apple Silicon-safe model assignments:
  - orchestrator/planner/synthesizer: `qwen2.5:14b`
  - navigator/executor/critic/router: `qwen2.5:3b`
  - memory: `nomic-embed-text`
- Removed larger-model assignments and retained memory-safe 3B/14B defaults.

### scripts/ollama_client.py
- Rebuilt the client to centralize model roles and compatibility aliases.
- Added `DEFAULT_OLLAMA_OPTIONS` and merged into all generate/chat/embedding requests:
  - `num_ctx: 4096`
  - `num_thread: 8`
  - `num_gpu: 999`
- Added async helpers (`async_call`, `async_call_json`, `async_stream_chat`, `async_embed_text`) used by async orchestration.
- Added streaming chat helper for token emission to the UI.

### scripts/orchestrator.py
- Replaced old threaded role-graph orchestration with async research orchestration tailored to Perplexity-style workflow.
- Added global `OLLAMA_SEMAPHORE = asyncio.Semaphore(2)` and wrapped every Ollama call path with it.
- Added query decomposition stage via planner (`decompose_query`).
- Added parallel sub-query execution with bounded concurrency.
- Added source enrichment pipeline (claim extraction + credibility scoring).
- Added synthesis prompt with inline citation requirements and streamed token output support.
- Added critic-driven one-hop follow-up searches and “Additional context” append flow.
- Added persistent memory recall/write integration around synthesis.

### scripts/task_planner.py
- Added `decompose_query(query: str) -> list[str]` with planner-model JSON prompting and fallback decomposition.
- Simplified and retained compatibility helpers (`CapabilityPlan`, `build_task_graph`, `tasks_to_stages`, `_can_use_heavy`) for existing imports.
- Removed larger-model defaults.

### scripts/claim_extractor.py
- Reimplemented claim extraction to produce 3–8 short factual claims from source text.
- Added executor-model JSON extraction prompt and normalization/dedupe.
- Added deterministic fallback extraction when model output is sparse.

### scripts/source_scoring.py
- Rebuilt source scoring around required 0.0–1.0 scale.
- Added `SourceScore` dataclass with fields: `url`, `score`, `domain_tier`, `claim_count`.
- Implemented domain authority tiering, content length bonus, and claim-density bonus.

### scripts/long_term_memory.py
- Replaced text-file workflow with SQLite persistence at `~/.local-computer/memory.db`.
- Added storage of every completed query + answer with timestamp.
- Added entity extraction and persistence to `memory_facts` table.
- Added similarity-based top-3 memory retrieval using `nomic-embed-text` embeddings + cosine similarity.
- Preserved compatibility entry points (`should_read`, `read_relevant`, `manage_memory`, `should_write`, `write_entry`).

### scripts/ui_server.py
- Replaced Flask/SSE server with WebSocket-first server.
- Added WebSocket endpoints at both `ws://localhost:8765` and `ws://localhost:8765/stream`.
- Added event broadcasting for required payload types: `token`, `source`, `thinking`, `done`.
- Added memory snapshot delivery (`memory`) and lightweight status signaling.
- Added static serving of `dashboard/index.html` from same port for convenience.

### dashboard/index.html
- Fully replaced dashboard with single-file HTML/CSS/JS app (no build step).
- Implemented dark-first Nexus visual system and responsive 3-column layout.
- Added query bar with Cmd+Enter, run-state pulse, and auto-focus.
- Added collapsible thinking log with animated spinners and completion stop behavior.
- Added token-streamed markdown answer rendering via `marked.js` CDN.
- Added inline citation linking (`[N]`) to source cards.
- Added right-side source cards with favicon, score bar color tiers, truncated URL, and “View” action.
- Added left memory sidebar with last 5 query chips and click-to-rerun behavior.
- Added connection-offline banner: “Agent offline — run ./run.sh to start”.

### run.sh
- Added required Apple Silicon env vars before Python invocation:
  - `PYTORCH_ENABLE_MPS_FALLBACK=1`
  - `OLLAMA_NUM_PARALLEL=1`
  - `OLLAMA_FLASH_ATTENTION=1`
  - `TOKENIZERS_PARALLELISM=false`
- Added fail-fast preflight checks with required messages for:
  - missing Ollama in PATH
  - missing `qwen2.5:14b`
  - missing `nomic-embed-text`
  - occupied port `8765`
- Kept venv/dependency/bootstrap behavior and switched default no-arg run mode to UI server launch.

### requirements.txt
- Added `websockets>=12.0`.
- Reason: required to implement true WebSocket server endpoints (`/` and `/stream`) and live token/source/thinking streaming.

### scripts/cloud_dispatcher.py
- Removed larger-model default fallback and aligned heavy fallback to `qwen2.5:14b`.
- Fixed local dispatch JSON call argument order (`call_json(prompt, model=...)`).

### scripts/subagents.py
- Removed larger-model fallback and aligned to `qwen2.5:3b` safe default.
- Updated memory threshold comments/logic to match staged 3B/14B routing assumptions.

### scripts/ollama_hybrid.py
- Simplified wrapper to local Ollama path only with 4096 context and Apple Silicon-friendly options.
- Removed larger-model-specific routing language.

### scripts/ai_chatbot_subagent.py
- Updated module description wording to remove larger-model-specific mention.
