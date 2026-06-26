# Ollama Manager Pro

A professional desktop application for managing local Ollama LLM models. Built with PyQt6, it provides a dark-navy UI across 14 specialised tabs covering the full local-model lifecycle — from discovery and installation through benchmarking, monitoring, chat, and custom model creation.

> **Default server:** `http://192.168.0.163:11434` (LAN AI rig). Change via the header bar or the Servers tab.

---

<img width="1920" height="1040" alt="Screenshot" src="https://github.com/user-attachments/assets/e4d3af29-f6ef-4b2f-b90d-733b3ddb3123" />

---

## Tabs

### Installed Models
Browse all locally installed Ollama models in an **alphabetically sorted** table with live search/filter. Select any model to open a full **LLM Profile panel** showing:

- **Capability tags** — auto-detected from model name and family: `👁 Vision`, `🖼 Multimodal`, `🔧 Tools`, `🤔 Thinking`, `💻 Code`, `🧮 Math`, `🔗 Embed`, `🌐 Multilingual`, `📝 Text Only`
- **Stat cards** — size, parameter count, context window, quantisation, family
- **Quick Usage section** — recommended API endpoint, temperature range, tool-calling notes, image input format, Ollama model ID
- **Model Info section** — architecture, attention heads, KV heads, layer count, embedding dimension (from `/api/show` `model_info`)
- **Built-in System Prompt, Parameters, Chat Template** previews (if present in Modelfile)

**Selection actions** (enabled when one or more models are selected):

| Button | Action |
|---|---|
| ⬆ Export Selected… | Backup to ZIP (blobs + manifests). Single-model exports are named after the model; multi-model exports use a timestamp prefix. |
| ⎘ Copy to Server… | Push selected models to another Ollama server. Picks from registered servers or accepts a manual URL. |
| ✕ Delete Selected | Permanent deletion with typed confirmation. |
| ✕ Delete Filtered | Bulk-delete all models matching the current search filter. |

### Running Models
Live view of models currently loaded in memory via `/api/ps`. Optional auto-refresh. Shows model name, size, processor (CPU/GPU), context length, and VRAM expiry time.

### Servers
Register multiple Ollama servers (LAN rigs, remote instances). Switch the active server from the header bar or this tab. Server list persists to `%APPDATA%/7h3v01d/ollama_manager_servers.json`.

### Pull & Backup
Stream model downloads with a live progress bar and token-by-token log. Cancel in-flight pulls. Export selected or all models to a ZIP archive; import archives back with duplicate-skipping.

### Registry Browser
Browse the full `ollama.com/library` catalogue without leaving the app. Live search/filter. Click **View Tags** on any model to see size variants and disk footprints; click **Pull** to jump to the Pull tab with the name pre-filled.

### Benchmark
Select any combination of installed models (Ctrl+click), choose a **target GPU** from the GPU selector (populated from `nvidia-smi` / `rocm-smi`), enter a prompt, and race them head-to-head. Results show tokens/second, time-to-first-token, total wall time, and eval token count. Fastest model is highlighted. Each model is automatically evicted from VRAM after its run.

### Chat
Full streaming chat interface with persistent history:

- **Session sidebar** — browse, resume, rename, or delete any previous conversation
- **Auto-title** — sessions are named from the first message automatically
- **System prompt editor** — set per-session persona and instructions
- **Temperature and context controls** — in the toolbar
- **Prompt library integration** — load saved prompts or system prompts from the library
- **🔊 TTS toggle** — send assistant replies to Voice Gateway (configure URL, endpoint, and payload key in ⚙ Settings)
- **Export** — save conversations as Markdown, JSON, or plain text

### Prompts
Saved prompt and system-prompt library with tags, search, and import/export. Used by the Chat tab's 📚 buttons.

### Batch
Run a single prompt against multiple models simultaneously and compare outputs side-by-side.

### Monitor
Live gauges for CPU, RAM, GPU VRAM, and GPU utilisation. Supports NVIDIA (`nvidia-smi`) and AMD (`rocm-smi`). Sparkline chart for the last 60 samples of each metric.

### Modelfile Editor
Syntax-highlighted Modelfile editor. Quick-parameter panel for temperature, top_p, top_k, num_ctx, repeat_penalty. One-click **Create Model**.

### Disk Analyser
Proportional bar breakdown of disk usage per model. Detects orphaned blobs and reports reclaimable space with a single confirmed purge action.

### ⚙ Settings
Persistent app configuration (saved to `%APPDATA%/7h3v01d/app_settings.json`):

**Voice Gateway (TTS)**
| Field | Description |
|---|---|
| Enable | Master toggle — also controls the 🔊 TTS checkbox default in Chat |
| Base URL | e.g. `http://192.168.0.163:8050` |
| Endpoint | e.g. `/tts`, `/speak`, `/synthesize` |
| Method | `POST` (default) or `GET` |
| JSON text key | The payload field name the gateway expects — e.g. `text`, `input`, `content` |
| ▶ Send Test | Fires a test request and shows the full HTTP response in the log box |
| Auto-enable TTS | Pre-tick the 🔊 checkbox whenever Chat tab is opened |

### About
Version info, keyboard shortcuts, and architecture overview.

---

## Requirements

| Requirement | Version |
|---|---|
| Python | ≥ 3.11 |
| PyQt6 | ≥ 6.5 |
| requests | ≥ 2.28 |
| psutil *(optional)* | ≥ 5.9 |
| Ollama | running locally or on LAN |

GPU monitoring requires `nvidia-smi` (NVIDIA) or `rocm-smi` (AMD) on the system `PATH`. The app runs without them — affected gauges show "No GPU detected".

---

## Installation

```bash
# 1. Clone or extract the project
git clone https://github.com/7h3v01d/ollama_model_manager
cd ollama_manager_pro

# 2. Create a virtual environment (or use venv-bat-gen)
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python src/main.py
```

> **Windows note:** If you see a blank white window on startup, ensure you are running Python 3.11+ and that PyQt6 is installed into the active virtual environment, not system Python.

---

## Data & Settings Locations (Windows)

| File | Purpose |
|---|---|
| `%APPDATA%\7h3v01d\ollama_manager_servers.json` | Registered Ollama servers |
| `%APPDATA%\7h3v01d\prompt_library.db` | Saved prompts (SQLite) |
| `%APPDATA%\7h3v01d\chat_history.db` | Persistent chat sessions (SQLite) |
| `%APPDATA%\7h3v01d\app_settings.json` | Voice Gateway and app preferences |

On first run, any existing files under `%APPDATA%\KeystoneAI\` are automatically migrated to the new path.

---

## Project Structure

```
src/
├── main.py              Entry point — app init, palette, stylesheet
├── theme.py             STYLESHEET constant (dark navy theme)
├── utils.py             Helpers: human_bytes, parse_time, ServerRegistry
├── client.py            OllamaClient — all Ollama REST API calls
├── models.py            Dataclasses, QAbstractTableModels, manifest helpers
├── workers.py           QObject/QThread background workers
├── widgets.py           Custom widgets, DetailPanel (LLM Profile), dialogs
├── window.py            MainWindow shell, Installed/Running tabs, core logic
├── tab_transfer.py      Pull & Backup tab
├── tab_registry.py      Registry Browser tab
├── tab_benchmark.py     Benchmark tab (with per-GPU targeting)
├── tab_monitor.py       Resource Monitor tab
├── tab_modelfile.py     Modelfile Editor tab
├── tab_disk.py          Disk Analyser tab
├── tab_chat.py          Chat tab (with persistent history and TTS)
├── tab_prompts.py       Prompt Library tab
├── tab_batch.py         Batch runner tab
├── tab_settings.py      Settings tab (Voice Gateway config)
├── tab_about.py         About tab
├── chat_history_db.py   SQLite chat session/message store
├── prompt_library.py    SQLite prompt library store
├── app_settings.py      JSON app settings store
├── migrate_settings.py  One-shot KeystoneAI → 7h3v01d path migration
└── logger.py            Rotating file + console logger
```

---

## Architecture Notes

**Threading model.** Every network or filesystem operation runs in a `QThread` via the `Worker`/`start_worker` pattern. Signals cross the thread boundary back to the main thread for all UI updates. `MainWindow` keeps a strong reference set (`_active_threads`) and waits up to 3 seconds for threads to finish on close.

**Ollama API surface used.**

| Endpoint | Used for |
|---|---|
| `GET /api/tags` | Installed model list |
| `POST /api/show` | Model detail / LLM Profile panel |
| `DELETE /api/delete` | Model deletion |
| `GET /api/ps` | Running models, Monitor tab |
| `POST /api/pull` (streaming) | Model download |
| `POST /api/generate` (streaming) | Benchmark, VRAM eviction (`keep_alive: 0`) |
| `POST /api/chat` (streaming) | Chat tab |
| `POST /api/embeddings` | (noted in profile for embed models) |
| `POST /api/copy` | Copy model to another server |

**Registry fetching.** Uses `https://ollama.com/search?q=&p=N&per_page=50` with `Accept: application/json` — the same endpoint the Ollama CLI uses internally.

**VRAM management in benchmarks.** After each model's run the worker sends `keep_alive: 0` to `/api/generate`, evicting it from VRAM so later models load into clean memory.

**Capability tag inference.** The LLM Profile panel infers capability tags from the model name and the `family`/`families` fields returned by `/api/show`. Tags are matched in priority order (specific model names before generic keywords) so e.g. `deepseek-r1` gets `🤔 Thinking` rather than falling through to a generic match. Models with no substantive tag get `📝 Text Only`.

---

## Licence

Apache 2.0 — Leon Priest (7h3v01d).
