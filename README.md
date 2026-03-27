# Ollama Manager Pro

A commercial-grade desktop application for managing local Ollama LLM models. Built with PyQt6, it provides a professional dark UI with eight specialised tabs covering the full local-model lifecycle — from discovery and installation through benchmarking, monitoring, and custom model creation.

---
<img width="1256" height="739" alt="Screenshot" src="https://github.com/user-attachments/assets/b3088464-c091-47c3-9edb-20cbc648c716" />


## Features

### Installed Models
Browse all locally installed Ollama models in a sortable table. Select any model to see a live detail panel showing format, family, parameter size, disk footprint, and the raw `/api/show` JSON. Multi-select for batch deletion with mandatory typed confirmation.

### Running Models
Live view of models currently loaded in memory via `/api/ps`. Optional auto-refresh every 5 seconds. Shows model name, size, processor (CPU/GPU), context length, and memory expiry time alongside the raw API response.

### Pull & Backup
Stream model downloads from the Ollama registry with a live progress bar and token-by-token log output. Cancel in-flight pulls. Export selected models or the entire model store to a ZIP archive (manifest + blobs only, no redundancy). Import archives back with duplicate-skipping.

### Registry Browser
Browse the full `ollama.com/library` catalogue without leaving the app. Fetches the same JSON search API the Ollama CLI uses. Live search/filter across all models. Click **View Tags** on any model to see available size variants with their disk footprints; click **Pull** on any tag to jump straight to the Pull tab with the model name pre-filled.

### Benchmark
Select any combination of installed models (Ctrl+click), enter a prompt, and race them head-to-head. Results show tokens/second, time-to-first-token, total wall time, and eval token count — using Ollama's internal `eval_duration` nanosecond field for accuracy rather than wall-clock estimation. The fastest model is highlighted. Each model is automatically evicted from VRAM after its run so later models aren't competing for memory.

### Resource Monitor
Live gauges for CPU usage, RAM (used/total), GPU VRAM (used/total), and GPU utilisation. Supports NVIDIA via `nvidia-smi` and AMD via `rocm-smi`. CPU and RAM require `psutil`. A custom-painted sparkline chart plots the last 60 samples of each metric simultaneously. Also shows currently loaded models pulled live from `/api/ps`.

### Modelfile Editor
Syntax-highlighted editor for Ollama Modelfiles with keyword colouring for `FROM`, `SYSTEM`, `PARAMETER`, `TEMPLATE`, and all standard parameter names. Quick-parameter panel lets you adjust temperature, top_p, top_k, num_ctx, and repeat_penalty visually, then sync them into the editor in one click. Load and save Modelfiles from disk. One-click **Create Model** runs `ollama create` in the background and refreshes the installed list on success.

### Disk Analyser
Scans the Ollama models directory and shows a proportional bar breakdown of disk usage per installed model. Detects **orphaned blobs** — files present in the `blobs/` directory but not referenced by any manifest (common after failed pulls or incomplete deletes). Reports total reclaimable space and offers a single confirmed purge action.

---

## Requirements

| Requirement | Version |
|---|---|
| Python | ≥ 3.11 |
| PyQt6 | ≥ 6.5 |
| requests | ≥ 2.28 |
| psutil *(optional)* | ≥ 5.9 |
| Ollama | running locally or remotely |

GPU monitoring requires `nvidia-smi` (NVIDIA) or `rocm-smi` (AMD) to be on the system `PATH`. The app runs without them — affected gauge cards display "No GPU detected".

---

## Installation

```bash
# 1. Clone or extract the project
git clone https://github.com/keystoneai/ollama-manager-pro.git
cd ollama-manager-pro

# 2. Create a virtual environment
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

> **Windows note:** If you see a blank white window on startup, ensure you are running Python 3.11+ and that PyQt6 is installed into the active virtual environment, not the system Python.

---

## Project Structure

```
src/
├── main.py              Entry point — app setup, palette, stylesheet
├── theme.py             STYLESHEET constant (dark navy theme)
├── utils.py             Pure helpers: human_bytes, parse_time, system stats
├── client.py            OllamaClient — all Ollama REST API calls
├── models.py            Dataclasses, QAbstractTableModels, manifest helpers
├── workers.py           QObject/QThread background workers
├── widgets.py           Custom QWidgets, dialogs, syntax highlighter
├── window.py            MainWindow shell, installed/running tabs, core logic
├── tab_transfer.py      Pull & Backup tab (TransferMixin)
├── tab_registry.py      Registry Browser tab (RegistryMixin)
├── tab_benchmark.py     Benchmark tab (BenchmarkMixin)
├── tab_monitor.py       Resource Monitor tab (MonitorMixin)
├── tab_modelfile.py     Modelfile Editor tab (ModelfileMixin)
└── tab_disk.py          Disk Analyser tab (DiskMixin)
```

`MainWindow` composes its functionality via multiple inheritance from six mixin classes — one per specialised tab. Each mixin owns its tab's `_tab_*` builder method and all associated `_xxx_*` logic methods. `window.py` handles the shell, the two core tabs (Installed, Running), shared helpers, and shutdown.

---

## Architecture Notes

**Threading model.** Every network or filesystem operation runs in a `QThread` via the `Worker`/`start_worker` pattern. Signals cross the thread boundary back to the main thread for all UI updates. The `MainWindow` keeps a strong reference set (`_active_threads`) and waits up to 3 seconds for all threads to finish on close.

**Ollama API surface used.**

| Endpoint | Used for |
|---|---|
| `GET /api/tags` | Installed model list |
| `POST /api/show` | Model detail panel |
| `DELETE /api/delete` | Model deletion |
| `GET /api/ps` | Running models, monitor |
| `POST /api/pull` (streaming) | Model download |
| `POST /api/generate` (streaming) | Benchmark, VRAM eviction (`keep_alive: 0`) |

**Registry fetching.** Uses `https://ollama.com/search?q=&p=N&per_page=50` with `Accept: application/json` — the same endpoint the Ollama CLI calls internally. A dedicated `requests.Session` is created for external calls to avoid sending the `Content-Type: application/json` header that the Ollama local API session carries globally.

**VRAM management in benchmarks.** After each model's benchmark run, the worker sends `keep_alive: 0` to `/api/generate`. This is Ollama's documented mechanism for immediately evicting a model from VRAM, ensuring each subsequent model in a benchmark run loads into clean memory.

---

## Configuration

No configuration file is required. All settings are session-local:

- **Server URL** — set in the header bar; defaults to `http://localhost:11434`
- **Models directory** — set per-tab in Pull & Backup and Disk Analyser; auto-detected from `OLLAMA_MODELS` environment variable or `~/.ollama/models`
- **Monitor interval** — 1 s / 2 s / 5 s / 10 s selector in the Monitor tab

---

## Platform Support

| Platform | Status |
|---|---|
| Windows 10/11 | ✓ Primary target |
| macOS 12+ | ✓ Tested |
| Linux (Ubuntu 22.04+) | ✓ Tested |

GPU monitoring is tested on Windows with NVIDIA GPUs. ROCm support (AMD) is implemented but less tested. Apple Silicon GPU metrics are not currently exposed by `nvidia-smi` or `rocm-smi`; the VRAM gauge will show "No GPU detected" on macOS.

---

### Contribution Policy

Feedback, bug reports, and suggestions are welcome.

You may submit:

- Issues
- Design feedback
- Pull requests for review

However:

- Contributions do not grant any license or ownership rights
- The author retains full discretion over acceptance and future use
- Contributors receive no rights to reuse, redistribute, or derive from this code

---

### License
This project is not open-source.

It is licensed under a private evaluation-only license.
See LICENSE.txt for full terms.
