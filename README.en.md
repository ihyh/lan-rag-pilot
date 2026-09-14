# LAN RAG Pilot

[中文](README.md) | [English](README.en.md)

Import PDF, Word, Excel, and text documents into a local knowledge base, ask questions in natural language, and open the source passages cited by each answer. The application uses FastAPI, SQLite, a local BGE embedding model, and an Ollama generation model. A browser provides the interface; documents and models stay on the computer or LAN server running the service.

`Upload → parse and chunk → retrieve passages with BGE → generate with Ollama → show citations`

The Windows steps below take a developer through a real question and citation check. See the linked guides for server deployment and ongoing operations.

## Development requirements

- Windows 11 x64, PowerShell, Python 3.12 x64, Ollama, and enough memory and disk for a local generation model. This example uses `qwen3:1.7b`; see [model selection](docs/MODELS.md) (Chinese) for other hardware.
- Use a **new** `C:\rag` project directory; models and data go in `C:\rag-local`. Run the commands in PowerShell. If you choose other paths, update every step consistently.
- A connected development computer may download source, dependencies, and models during preparation, then must be disconnected from the internet before running the service. A fully isolated computer needs an administrator-supplied package containing source, Python/Ollama installers, a matching wheelhouse, a complete BGE directory, and the Ollama model store. See [offline package preparation](docs/OFFLINE_PACKAGE.md) (Chinese).
- Ollama must already be installed. First startup also requires a random session key and initial root password in `.env`. The repository does not contain secrets, business data, or model files.

## Windows quick start

### 1. Get the source and open it in an IDE

During an **approved, connected preparation phase**, clone the repository:

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
Set-Location C:\rag
```

On a fully isolated computer, import the verified package. The example assumes its source root is `C:\rag-kit\project`:

```powershell
New-Item -ItemType Directory C:\rag -ErrorAction Stop
Copy-Item C:\rag-kit\project\* C:\rag -Recurse -ErrorAction Stop
Set-Location C:\rag
Test-Path .\app\main.py
```

Choose one source path. The final command must print `True`. Open the `C:\rag` folder in any IDE. After creating the virtual environment, select `C:\rag\.venv\Scripts\python.exe` as the project interpreter. Do not copy a virtual environment from another computer.

### 2. Install Python dependencies

Run these commands in `C:\rag`:

```powershell
py -3.12 --version
py -3.12 -m venv .venv
```

If the development computer is still in its **approved, connected preparation phase**:

```powershell
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If the computer is always isolated, use the administrator's bundle for the same Windows/Python architecture:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --find-links C:\rag-kit\windows\wheelhouse -r C:\rag-kit\windows\requirements.lock.txt
```

Finally, run `.\.venv\Scripts\python.exe -m pip check`; it should report no dependency conflicts. Missing wheels must be added on the preparation computer, never downloaded by the isolated computer.

### 3. Prepare both local models

Create model directories and configure Ollama for the **current Windows user**. Quit any running Ollama tray process first:

```powershell
New-Item -ItemType Directory C:\rag-local\models,C:\rag-local\ollama-models -Force | Out-Null
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS', 'C:\rag-local\ollama-models', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_HOST', '127.0.0.1:11434', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_NO_CLOUD', '1', 'User')
```

Sign back in to Windows, launch Ollama from the Start menu, and open a new PowerShell window. During an **approved, connected preparation phase**, acquire the example models:

```powershell
ollama pull qwen3:1.7b
Set-Location C:\rag
.\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu').save('C:/rag-local/models/bge-small-zh-v1.5')"
```

On a fully isolated computer, do not run those download commands. Import the complete, verified model bundle instead:

```powershell
Copy-Item C:\rag-kit\models\bge-small-zh-v1.5 C:\rag-local\models -Recurse -ErrorAction Stop
Copy-Item C:\rag-kit\ollama-models\* C:\rag-local\ollama-models -Recurse -ErrorAction Stop
```

The supplied Ollama directory must directly contain `blobs` and `manifests`. Verify:

```powershell
ollama list
Test-Path C:\rag-local\models\bge-small-zh-v1.5\modules.json
```

You should see `qwen3:1.7b` and `True`. A weight file alone is not a complete embedding model. If files are missing, the offline package must be rebuilt. Disconnect the internet after preparation, before starting the application.

### 4. Configure the local service

Create `C:\rag\.env` (not `.env.txt`) with the following values, replacing both `REPLACE` placeholders. Do not use the historical experimental address in `.env.example`:

```dotenv
DEEPSEEK_API_KEY=ollama
DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1
DEEPSEEK_MODEL=qwen3:1.7b
DEEPSEEK_TIMEOUT_S=180
RAG_SECRET_KEY=REPLACE_WITH_RANDOM_SECRET
RAG_ROOT_PASSWORD=REPLACE_WITH_STRONG_INITIAL_PASSWORD
RAG_COOKIE_SECURE=false
RAG_PUBLIC_ORIGIN=http://127.0.0.1:8088
RAG_HOST=127.0.0.1
RAG_PORT=8088
RAG_DATA_DIR=C:/rag-local/data
RAG_DB_PATH=C:/rag-local/data/rag.db
RAG_UPLOAD_DIR=C:/rag-local/data/uploads
RAG_MODELS_DIR=C:/rag-local/models
RAG_EMBED_MODEL=C:/rag-local/models/bge-small-zh-v1.5
RAG_EMBED_BACKEND=st
RAG_MAX_CONCURRENT_LLM=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

Run `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"` to generate a session key, and set a separate strong initial root password. Never commit or share either value. The Windows launcher reads everything after `=` literally: do not add surrounding quotes or inline comments. `DEEPSEEK_*` are legacy names for an OpenAI-compatible interface; this configuration connects only to local Ollama.

### 5. Start the service and check readiness

In `C:\rag`, run:

```powershell
powershell -NoProfile -File .\scripts\start_local.ps1 -Python C:\rag\.venv\Scripts\python.exe
```

Keep this window open. If your organization's execution policy blocks the script, follow its approved procedure rather than disabling the policy globally. In another PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8088/api/health
Invoke-RestMethod http://127.0.0.1:8088/api/ready
```

`ready` may return HTTP 503 while BGE loads. Once it returns 200, open [http://127.0.0.1:8088](http://127.0.0.1:8088). For a new database, log in as `root` with the password set above. Changing `RAG_ROOT_PASSWORD` does not reset an existing account. Stop the service with Ctrl+C in its startup window; quit Ollama separately.

## Complete a real question and citation check

After signing in, open “管理” (Admin) and upload a UTF-8 file named `入门演示.txt` containing the following fictional test data:

```text
演示设备：DEMO-01。
DEMO-01 的每日巡检时间是上午 09:00。
巡检记录由值班员填写。
```

Wait until its status is “可用” (ready). Start a new conversation scoped to this document and ask “DEMO-01 每日巡检时间是几点？” (“At what time is DEMO-01 inspected daily?”). Wait for streaming to finish, then expand “查看引用来源” (View citations). The answer should give 09:00, and the citation should point to supporting text in `入门演示.txt`. If the answer, document status, or citation is wrong, installation has not passed this check; see [troubleshooting](docs/TROUBLESHOOTING.md) (Chinese). `/api/ready` only checks embedding readiness, not the entire generation path.

`tests/smoke_runner.ps1` uses mock embeddings and a mock generation service for API regression tests. It does not replace the real-model citation check above.

## Models and runtime

| Stage | Current implementation | When changing it |
|---|---|---|
| Tokenization and chunking | BGE tokenizer plus project rules; default maximum 400 tokens, 60-token overlap | There is no separate chunking model; existing documents need reprocessing after rule changes |
| Retrieval | Chinese BGE-small v1.5, 512 dimensions, encoded on CPU | Changing embedding weights requires rebuilding document vectors |
| Generation | Local Ollama; this example uses `qwen3:1.7b` | A generation-model change usually needs no document rebuild, but answers and resource usage must be reevaluated |

Larger models may improve retrieval or expression at the cost of RAM, VRAM, and latency; download size is not runtime memory use. See the [model guide](docs/MODELS.md) (Chinese) for hardware tiers and replacement procedures.

## Detailed documentation

The following guides are currently available **in Chinese only**:

- [Windows personal offline installation](docs/WINDOWS.md), [Ubuntu LAN server deployment](docs/UBUNTU.md), [offline package preparation](docs/OFFLINE_PACKAGE.md)
- [First use](docs/GETTING_STARTED.md), [backup, migration, and upgrade](docs/OPERATIONS.md), [troubleshooting](docs/TROUBLESHOOTING.md)
- [Development and configuration index](docs/IT_handover.md), [retrieval validation](docs/RETRIEVAL_VALIDATION.md), [business quality evaluation](eval/README.md)
- [Security deployment requirements](docs/SECURITY.md), [roadmap](ROADMAP.md), [security reporting](SECURITY.md)

## Security and deployment limitations

This HTTP example binds only to `127.0.0.1`. Access from other computers requires approved LAN HTTPS, account management, port restrictions, and outbound network controls. The current version **does not enforce department-level or per-person document access**: authenticated users can read the shared document library. It must not be used as a production system where those access boundaries are required.

`HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, and `OLLAMA_NO_CLOUD` are not firewall rules. If the local model URL is omitted, the application also has a public-service default. Strict offline operation therefore requires both enforced network policy and explicit local configuration. Protect SQLite, uploaded files, and backups, and verify important answers against the original documents. See [security requirements](docs/SECURITY.md) (Chinese).
