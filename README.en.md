# LAN RAG Pilot

[中文](README.md) | [English](README.en.md)

Import PDF, Word, Excel, and text files into a knowledge base, ask questions, and inspect the source passages cited by the answers. The runtime needs three parts: the **RAG app** (FastAPI, SQLite, document storage), a **local BGE embedding model** (retrieval), and an **Ollama generation model** (answers). Users connect through a browser.

## Choose a local development OS

| Development OS | Run locally | Setup |
| --- | --- | --- |
| Windows | RAG app, BGE, and Ollama | [Windows local setup](#windows-import-and-run) |
| Ubuntu | The same RAG app, BGE, and Ollama | [Ubuntu local setup](#ubuntu-import-and-run) |

These are **parallel local development options**. Use either OS, or install separately on both to test cross-platform compatibility. Do not copy `.venv` between systems. For a future Linux server, see the [Ubuntu server guide](docs/UBUNTU.md) (Chinese only).

## Windows: import and run

Prerequisites: Windows 11, Python 3.12, and [Ollama](https://ollama.com/download/windows). Run the commands in PowerShell. Internet access is only for **preparing source, dependencies, and models**. For a fully isolated runtime machine, first obtain those materials using the [offline packaging guide](docs/OFFLINE_PACKAGE.md) (Chinese only); do not run download commands there.

1. Import the source. On a preparation machine with GitHub access, run:

   ```powershell
   git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
   Set-Location C:\rag
   ```

   On an isolated machine, unpack the prepared source bundle into `C:\rag` instead. In VS Code, PyCharm, or another IDE, **open the `C:\rag` folder**, not an individual Python file. Check that it contains `app`, `scripts`, and `requirements.txt`.

2. In `C:\rag`, create the project environment and prepare the models. Set the IDE interpreter to `C:\rag\.venv\Scripts\python.exe` afterwards.

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ollama pull qwen3:1.7b
   .\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu').save('models/bge-small-zh-v1.5')"
   ```

   Installing dependencies, pulling with Ollama, and downloading BGE require an approved connected preparation phase. On an isolated machine, import matching wheels and **complete** model directories instead; see [Windows offline installation](docs/WINDOWS.md) (Chinese only). Check that `ollama list` contains `qwen3:1.7b` and `models\bge-small-zh-v1.5\modules.json` exists.

3. Create `.env` (not `.env.txt`) in the project root. Replace both `REPLACE` values with a strong password and a separate random secret; run `py -3.12 -c "import secrets; print(secrets.token_urlsafe(48))"` twice for independent URL-safe values. Do not reuse the old experimental IP in `.env.example`. The same configuration example applies to local Ubuntu development.

   ```dotenv
   DEEPSEEK_API_KEY=ollama
   DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1
   DEEPSEEK_MODEL=qwen3:1.7b
   RAG_EMBED_MODEL=models/bge-small-zh-v1.5
   RAG_SECRET_KEY=REPLACE_WITH_RANDOM_SECRET
   RAG_ROOT_PASSWORD=REPLACE_WITH_STRONG_INITIAL_PASSWORD
   RAG_HOST=127.0.0.1
   RAG_PUBLIC_ORIGIN=http://127.0.0.1:8088
   HF_HUB_OFFLINE=1
   TRANSFORMERS_OFFLINE=1
   ```

4. With Ollama running, execute this from `C:\rag`:

   ```powershell
   powershell -NoProfile -File .\scripts\start_local.ps1 -Python C:\rag\.venv\Scripts\python.exe
   ```

   Open [http://127.0.0.1:8088](http://127.0.0.1:8088). For a new database, sign in as `root` with the initial password above. Upload a test document, ask a question, and expand “查看引用来源” (View citations) to check the supporting source. Stop the service with Ctrl+C in the launch window. See [troubleshooting](docs/TROUBLESHOOTING.md) (Chinese only) if the models or citations fail.

## Ubuntu: import and run

Prepare Ubuntu 24.04, Python 3.12 (including `venv`), and [Ollama for Linux](https://docs.ollama.com/linux). Like Windows, this is **local development**: Docker, Nginx, and HTTPS are not needed. Without GPU passthrough, an Ubuntu VM may run the model on CPU. Run these commands in an Ubuntu terminal.

1. Import the source during a preparation phase with GitHub access, then open the project root in your IDE:

   ```bash
   git clone https://github.com/ihyh/lan-rag-pilot.git ~/lan-rag-pilot
   cd ~/lan-rag-pilot
   ```

   On a fully isolated computer, unpack verified source into `~/lan-rag-pilot` instead. Dependencies and models must also be prepared in advance; do not download them on the isolated computer.

2. Create an **OS-specific** virtual environment in the project root and prepare models:

   ```bash
   python3.12 -m venv .venv
   ./.venv/bin/python -m pip install -r requirements.txt
   ollama pull qwen3:1.7b
   ./.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu').save('models/bge-small-zh-v1.5')"
   ```

   Run installation and download commands only during an approved connected preparation phase. On an isolated development machine, import matching Linux wheels and complete model directories instead. Check that `ollama list` contains `qwen3:1.7b`, and set the IDE interpreter to `~/lan-rag-pilot/.venv/bin/python`.

3. Create `.env` in the project root with the **same keys and values** as [Windows step 3](#windows-import-and-run). On Ubuntu, run `python3.12 -c "import secrets; print(secrets.token_urlsafe(48))"` twice for the secret and initial password. With Ollama running, execute this in the project root:

   ```bash
   set -a
   . ./.env
   set +a
   ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8088
   ```

   Open [http://127.0.0.1:8088](http://127.0.0.1:8088). For a new database, sign in as `root` with the initial password, upload a test document, and verify a citation. Stop with Ctrl+C. This `.env` is a Shell configuration file you created yourself: use the example's space-free values and URL-safe secret/password, and never source an untrusted `.env`.

A production server also needs service management, persistence, internal HTTPS, and egress controls; see [Ubuntu server deployment](docs/UBUNTU.md) (Chinese only). A successful local setup is not a server deployment acceptance test.

The current version **has no department-level or per-person document access control**: signed-in users can access the shared document library. Do not use it unchanged where such isolation is required. Strict internet isolation also needs enforced host/network egress rules; `.env` flags alone are insufficient. [Security requirements](docs/SECURITY.md) (Chinese only) · [Model selection](docs/MODELS.md) (Chinese only) · [Full documentation index](docs/IT_handover.md) (Chinese only)
