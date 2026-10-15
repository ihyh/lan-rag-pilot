# Windows personal knowledge base

A local document RAG service for Windows, with optional private phone access through Tailscale. Defaults: Python 3.12, Ollama `qwen3:4b-instruct`, and BGE-small-zh-v1.5 (512 dimensions, CPU embeddings).

Install Git for Windows, Python 3.12 and [Ollama for Windows](https://ollama.com/download/windows), and start Ollama. In PowerShell:

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
cd C:\rag
.\setup_windows.cmd
```

First-time setup downloads dependencies and models, creates `.env` and a random initial root password, and starts the service at `http://127.0.0.1:8088`. Save the displayed password. Existing configuration and data are preserved; setup does not force an existing installation onto a different model. Use `start_windows.cmd` for subsequent starts.

The repository contains source, public documentation and synthetic tests, not private documents, databases, passwords or model weights. A fresh clone requires installation and importing your own documents.

Features include keyword/vector hybrid retrieval, citations, document permissions, conversation history and feedback. CEID records are kept as atomic events with a structured catalog. Definitions from different scenarios remain separate; conflicting IDs are disclosed rather than arbitrated, and label-only records are identified as incomplete. Existing documents need an explicit backed-up reprocessing operation to adopt new chunking.

Before updating, stop the service and back up the complete `data` directory and `.env`. Then run `git pull --ff-only` and `setup_windows.cmd`. Updating source does not deploy another running copy or rebuild its index.

For remote access, keep the host awake and online and use private Tailscale HTTPS Serve. Do not expose the service publicly or enable Funnel.

Development checks (Python 3.12, Node.js 22):

```powershell
python -m pip install -r requirements-smoke.txt
python tests/run_checks.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/smoke_runner.ps1 -Python (Get-Command python).Source
```

These use synthetic data and mock models, not a real-model accuracy benchmark. See the [Chinese guide](README.md), [Windows setup](docs/WINDOWS.md), [operations](docs/OPERATIONS.md), and [CEID details](docs/ceid-events.md).
