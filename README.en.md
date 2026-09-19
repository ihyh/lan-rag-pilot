# LAN RAG Pilot

[中文](README.md) | [English](README.en.md)

Upload documents, select a device, and ask a question. The app retrieves relevant passages, generates an answer with local Ollama, and shows citations. Runtime components are the RAG app (FastAPI, SQLite), a BGE retrieval model, and an Ollama generation model.

| Mode | Where the services run | How users connect |
| --- | --- | --- |
| Windows personal knowledge base | All three components on your Windows computer | Local browser at `http://127.0.0.1:8088` |
| Linux enterprise knowledge base | All three components on a Linux server | Employees join company Wi-Fi and open a fixed internal HTTPS URL (usually port 443) |

Both paths follow “import source → create a virtual environment → prepare models”, then configure and start the selected system. The repository does not include model files, passwords, or business documents. Dependencies and models may be downloaded during an approved connected preparation phase.

## Windows: personal knowledge base

Prepare Windows, [Git for Windows](https://git-scm.com/download/win), Python 3.12, PowerShell, and [Ollama](https://ollama.com/download/windows). If `git --version` or `py -3.12 --version` fails, install the missing program and reopen PowerShell. Run these commands in PowerShell and open the entire `C:\rag` folder in your IDE:

```powershell
git --version
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
Set-Location C:\rag
py -3.12 --version
if (-not (Test-Path .\.venv\Scripts\python.exe)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --timeout 120 --retries 10 -r .\requirements.txt
.\.venv\Scripts\python.exe -m pip check
ollama pull qwen3:1.7b
```

Large dependencies such as PyTorch and SciPy can spend several minutes installing without new output. Keep waiting while Python still shows CPU or disk activity in Task Manager. If pip reports `Read timed out`, rerun the same command with `--timeout 120 --retries 10`; pip reuses its download cache.

Prefer the project's [offline BGE model release](https://github.com/ihyh/lan-rag-pilot/releases/tag/bge-small-zh-v1.5-7999e1d). It requires access to GitHub only, without access to Hugging Face or a Hugging Face proxy:

```powershell
$BgeZip="$env:TEMP\bge-small-zh-v1.5-7999e1d.zip"
Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/ihyh/lan-rag-pilot/releases/download/bge-small-zh-v1.5-7999e1d/bge-small-zh-v1.5-7999e1d.zip' -OutFile $BgeZip
$ExpectedSha256='0edacc059c0d792466da7b83569c0406aef88b334f6b297d11f5ee5bbf4499c2'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $BgeZip).Hash.ToLowerInvariant() -ne $ExpectedSha256) { throw 'BGE package checksum failed; delete it and download again' }
New-Item -ItemType Directory -Path .\models -Force | Out-Null
Expand-Archive -LiteralPath $BgeZip -DestinationPath .\models -Force
.\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('models/bge-small-zh-v1.5', local_files_only=True, device='cpu'); print(m.get_sentence_embedding_dimension())"
```

The expected output is `512`. The archive includes its upstream revision and MIT license. If the GitHub release is unavailable, prepare the model directly from Hugging Face instead. Choose the network setup that applies to this computer, and do not copy one computer's proxy port to another:

```powershell
# This computer can reach Hugging Face directly or does not use a proxy
Remove-Item Env:HTTP_PROXY,Env:HTTPS_PROXY -ErrorAction SilentlyContinue

# When a proxy is required, enter the complete proxy URL available to this computer; use the LAN IP if the proxy runs on another computer
$ProxyUrl=Read-Host 'Proxy URL (format: http://address:port)'
$env:HTTP_PROXY=$ProxyUrl
$env:HTTPS_PROXY=$ProxyUrl
$env:NO_PROXY='127.0.0.1,localhost'
```

Run only the applicable setup above, then download and save a self-contained model directory:

```powershell
.\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu').save('models/bge-small-zh-v1.5')"
```

Set the IDE interpreter to `C:\rag\.venv\Scripts\python.exe`. Create `C:\rag\.env` with the following values, replacing both `REPLACE` placeholders. Run `py -3.12 -c "import secrets; print(secrets.token_urlsafe(48))"` twice for independent random values. Do not reuse the old model URL in `.env.example`.

```dotenv
DEEPSEEK_API_KEY=ollama
DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1
DEEPSEEK_MODEL=qwen3:1.7b
RAG_EMBED_MODEL=models/bge-small-zh-v1.5
RAG_SECRET_KEY=REPLACE_WITH_RANDOM_SECRET
RAG_ROOT_PASSWORD=REPLACE_WITH_STRONG_INITIAL_PASSWORD
RAG_HOST=127.0.0.1
RAG_PUBLIC_ORIGIN=http://127.0.0.1:8088
NO_PROXY=127.0.0.1,localhost
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

Model preparation note: the BGE download command must reach Hugging Face. If it reports `WinError 10060` or a connection timeout, press `Ctrl+C`; this is a network failure, not a Python dependency failure. If the target has neither direct access nor a usable proxy, run that command on a connected preparation machine and copy the entire `models\bge-small-zh-v1.5` directory to `C:\rag\models\bge-small-zh-v1.5` on the target. For an offline target, keep `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` in `.env`, then verify the local model with:

```powershell
.\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('models/bge-small-zh-v1.5', local_files_only=True, device='cpu'); print(m.get_sentence_embedding_dimension())"
```

These offline variables apply when starting the service; do not set them to `1` while downloading the model.

Start Ollama from the Start menu, then open a new PowerShell and verify both its local API and the model. If the computer uses `HTTP_PROXY`, set `NO_PROXY` for the current process so localhost requests do not go through that proxy:

```powershell
$env:NO_PROXY='127.0.0.1,localhost'
Invoke-RestMethod http://127.0.0.1:11434/api/tags
ollama list
Set-Location C:\rag
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_local.ps1 -Python C:\rag\.venv\Scripts\python.exe
```

`-ExecutionPolicy Bypass` applies only to this child process; it does not change the system or user execution policy. If an organization policy still blocks the script, ask the administrator to review and allow it.

Open [http://127.0.0.1:8088](http://127.0.0.1:8088). For a new database, sign in as `root` with the initial password above. Upload a test document, select a device, ask a question, and expand “查看引用来源” (View citations). Stop the service with Ctrl+C in its terminal.

## Linux: enterprise knowledge base

IT prepares Python 3.12, [Ollama](https://docs.ollama.com/linux), an internal DNS name, and an HTTPS certificate on the Linux server. This Ubuntu example uses `/opt/rag`; IT must create a writable project directory first. Run the download commands below only during an approved connected installation phase. If the server must never reach the internet, IT must prepare the materials using the [Ubuntu guide](docs/UBUNTU.md) (Chinese only) instead of running these downloads there.

```bash
git clone https://github.com/ihyh/lan-rag-pilot.git /opt/rag
cd /opt/rag
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
ollama pull qwen3:1.7b
./.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu').save('models/bge-small-zh-v1.5')"
```

Create `/opt/rag/.env` with the same local Ollama URL and model as in the Windows example, but set `RAG_EMBED_MODEL=/opt/rag/models/bge-small-zh-v1.5`, set `RAG_PUBLIC_ORIGIN` to the actual internal HTTPS URL, and add `RAG_COOKIE_SECURE=true`. Generate separate new values for `RAG_SECRET_KEY` and `RAG_ROOT_PASSWORD`. Verify startup locally on the server first:

```bash
set -a
. ./.env
set +a
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8088
```

This command is for local acceptance only. Before employees connect, IT must manage the app as a service and provide **internal HTTPS** through a reverse proxy such as Nginx. Allow only the dedicated employee Wi-Fi subnet to reach the entry point; block public internet access. Do not expose ports 8088 or 11434 directly to employee devices. A fixed URL does not replace login: root creates individual employee accounts, assigns roles, and deactivates leavers. See the [Ubuntu guide](docs/UBUNTU.md), [security requirements](docs/SECURITY.md), and [operations guide](docs/OPERATIONS.md) (Chinese only) for server deployment, hardening, and backup.

| Role assigned by an administrator | Current permissions |
| --- | --- |
| Regular `user` (tester/developer/employee) | Ask about a selected device and see citation excerpts; cannot open full files or manage documents |
| Document administrator `kb_admin` | Above, plus open full files, upload, reindex, and delete documents |
| System administrator `root` | All of the above, plus accounts, roles, settings, and audit management |

Device selection scopes retrieval by filename; unmatched files need manual selection. It is **not access control**. All authorized users can still ask about the shared document library; department-level and per-person isolation are not implemented. Regular users cannot download a complete file directly, but a short file may fit in one citation excerpt, and repeated questions may reveal much of its content. Do not use this version unchanged where users must be prevented from learning one another's documents.

If runtime internet access must be prohibited after model download, enforce this with host and network egress policy; `.env` offline flags are not a firewall. [First use](docs/GETTING_STARTED.md) (Chinese only) · [Troubleshooting](docs/TROUBLESHOOTING.md) (Chinese only)
