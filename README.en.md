# LAN RAG Pilot

[中文](README.md) | [English](README.en.md)

Import PDF, Word, Excel, and text files into a knowledge base, ask questions, and inspect the source passages cited by the answers. The runtime needs three parts: the **RAG app** (FastAPI, SQLite, document storage), a **local BGE embedding model** (retrieval), and an **Ollama generation model** (answers). Users connect through a browser.

## Choose one runtime host

| Use case | What to run | Start here |
| --- | --- | --- |
| Windows development or single-user use | RAG, BGE, and Ollama on Windows | Follow the steps below |
| Ubuntu LAN server for multiple users | RAG, BGE, and Ollama on Ubuntu; Windows only needs a browser | [Ubuntu deployment guide](docs/UBUNTU.md) (Chinese only) |

**You do not need two RAG deployments.** If Ubuntu is a VM on Windows and you want Ollama to use the Windows GPU, that is a split-host setup: configure the VM-to-host model URL and firewall separately. The single-host example below does not cover it.

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

3. Create `.env` (not `.env.txt`) in the project root. Replace both `REPLACE` values with a strong password and a separate random secret; `py -3.12 -c "import secrets; print(secrets.token_urlsafe(48))"` can generate the secret. Do not reuse the old experimental IP in `.env.example`.

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

## Ubuntu: import and deploy

Clone the repository on a connected preparation machine, or obtain a verified source bundle, then import the source into a new `/opt/rag` directory on Ubuntu. You also need Docker/Compose, RAG and Ollama images, complete BGE and Ollama models, `.env`, persistent data volumes, and an internal HTTPS entry point. The repository's `docker-compose.yml` **starts only RAG; it does not install or start Ollama** or supply offline images and models. Do not treat `docker compose up` as a complete deployment. Follow the [Ubuntu offline deployment guide](docs/UBUNTU.md) (Chinese only).

The current version **has no department-level or per-person document access control**: signed-in users can access the shared document library. Do not use it unchanged where such isolation is required. Strict internet isolation also needs enforced host/network egress rules; `.env` flags alone are insufficient. [Security requirements](docs/SECURITY.md) (Chinese only) · [Model selection](docs/MODELS.md) (Chinese only) · [Full documentation index](docs/IT_handover.md) (Chinese only)
