# LAN RAG Pilot

[中文](README.md) | [English](README.en.md)

Upload documents, select a device, and ask a question. The app retrieves relevant passages, generates an answer with local Ollama, and shows citations. Runtime components are the RAG app (FastAPI, SQLite), a BGE retrieval model, and an Ollama generation model.

| Mode | Where the services run | How users connect |
| --- | --- | --- |
| Windows personal knowledge base | All three components on your Windows computer | Local browser at `http://127.0.0.1:8088` |
| Linux enterprise knowledge base | All three components on a Linux server | Employees join company Wi-Fi and open a fixed internal HTTPS URL (usually port 443) |

The repository does not include model files, passwords, or business documents. The Windows setup script downloads dependencies and models on its first run.

## Windows: personal knowledge base

Install [Git for Windows](https://git-scm.com/download/win), [Python 3.12](https://www.python.org/downloads/), and [Ollama](https://ollama.com/download/windows), then start Ollama from the Start menu. Open PowerShell and run only:

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
Set-Location C:\rag
.\setup_windows.cmd
```

The script creates `.venv`, installs and checks the Python dependencies, pulls `qwen3:1.7b`, downloads and verifies BGE from the [GitHub release](https://github.com/ihyh/lan-rag-pilot/releases/tag/bge-small-zh-v1.5-7999e1d), creates `.env` and a random initial password, and starts the app. The first run downloads large dependencies and models. If the network is interrupted, run the same command again to reuse downloaded files. An existing `.env` is preserved.

Save the initial `root` password shown in the window, then open the URL printed by the script (normally [http://127.0.0.1:8088](http://127.0.0.1:8088)). Press Ctrl+C to stop. Run `setup_windows.cmd` again for later starts. The IDE interpreter is `C:\rag\.venv\Scripts\python.exe`. See the [Windows guide](docs/WINDOWS.md) and [troubleshooting guide](docs/TROUBLESHOOTING.md) (Chinese only) for offline or manual setup.

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
