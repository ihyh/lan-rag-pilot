# LAN RAG Pilot

[中文](README.md) | [English](README.en.md)

Upload documents, select a device, and ask a question. The app retrieves relevant passages, generates an answer with local Ollama, and shows citations. Runtime components are the RAG app (FastAPI, SQLite), a BGE retrieval model, and an Ollama generation model.

| Mode | Where the services run | How users connect |
| --- | --- | --- |
| Windows personal knowledge base | All three components on your Windows computer | Local browser at `http://127.0.0.1:8088` |
| Linux enterprise knowledge base | All three components on a Linux server | Employees join company Wi-Fi and open a fixed internal HTTPS URL (usually port 443) |

The repository does not include model files, passwords, or business documents. The Windows and Linux setup scripts download dependencies and models on their first run.

## Windows: personal knowledge base

### 1. Prepare the required software

Install [Git for Windows](https://git-scm.com/download/win) and [Ollama](https://ollama.com/download/windows), then start Ollama from the Start menu.

### 2. Install for the first time

Open PowerShell and run these three commands:

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
Set-Location C:\rag
.\setup_windows.cmd
```

The first run downloads large dependencies and models. Keep the PowerShell window open and wait for the script to finish. When you run the same command again, the script reuses files that have already been downloaded.

### 3. Wait for setup to finish

The script automatically:

1. Installs Python 3.12 through Windows Python Installation Manager or `winget` if it is missing.
2. Creates `.venv`, then installs and checks the Python dependencies.
3. Pulls `qwen3:1.7b`.
4. Downloads and verifies BGE from the [GitHub release](https://github.com/ihyh/lan-rag-pilot/releases/tag/bge-small-zh-v1.5-7999e1d).
5. Creates `.env` and a random initial password; an existing `.env` is preserved.
6. Starts the app.

### 4. Confirm that the installation works

When the terminal shows that the app has started, keep the window open. Save the initial `root` password and the URL printed in the window, normally [http://127.0.0.1:8088](http://127.0.0.1:8088).

Open the URL in a browser and sign in as `root` with the initial password. Upload a test document and ask a question. The installation and complete question-answering flow are ready when you can see both the generated answer and its citations. To stop the app, press Ctrl+C in the running window.

### 5. Start or update an existing installation

If `C:\rag` already exists, do not run `git clone` again. Enter that directory, run `git pull --ff-only` when you want to update, and then run `setup_windows.cmd`. Use `setup_windows.cmd` for later starts as well, and make sure Ollama is already running.

The IDE interpreter is `C:\rag\.venv\Scripts\python.exe`. See the [Windows guide](docs/WINDOWS.md) and [troubleshooting guide](docs/TROUBLESHOOTING.md) (Chinese only) for offline installation, manual configuration, and troubleshooting.

## Linux: enterprise knowledge base

IT prepares Python 3.12 with its venv module, [Ollama](https://docs.ollama.com/linux), an internal DNS name, and an HTTPS certificate on the Linux server. This Ubuntu example uses `/opt/rag`; IT must create a writable project directory first. During an approved connected installation phase, run only:

```bash
git clone https://github.com/ihyh/lan-rag-pilot.git /opt/rag
cd /opt/rag
./setup_linux.sh
```

`setup_linux.sh` creates `.venv`, installs and checks dependencies, pulls `qwen3:1.7b`, downloads and verifies BGE from the GitHub release, creates a mode-600 `.env` with a random initial password, and starts the app on loopback. It preserves an existing `.env`. If `/opt/rag` already exists, skip `git clone`, enter the directory, run `git pull --ff-only`, and then run the script.

Save the initial root password and use the local URL printed by the script for acceptance testing. Press Ctrl+C to stop. If the server must never reach the internet, do not run this connected preparation flow; IT must use the [Ubuntu guide](docs/UBUNTU.md) (Chinese only).

The process started by the script is for local acceptance only. Before employees connect, IT must set `RAG_PUBLIC_ORIGIN` to the actual internal HTTPS URL, set `RAG_COOKIE_SECURE=true`, manage the app as a service, and provide **internal HTTPS** through a reverse proxy such as Nginx. Allow only the dedicated employee Wi-Fi subnet to reach the entry point; block public internet access. Do not expose ports 8088 or 11434 directly to employee devices. A fixed URL does not replace login: root creates individual employee accounts, assigns roles, and deactivates leavers. See the [Ubuntu guide](docs/UBUNTU.md), [security requirements](docs/SECURITY.md), and [operations guide](docs/OPERATIONS.md) (Chinese only) for server deployment, hardening, and backup.

| Role assigned by an administrator | Current permissions |
| --- | --- |
| Regular `user` (tester/developer/employee) | Ask about a selected device and see citation excerpts; cannot open full files or manage documents |
| Document administrator `kb_admin` | Above, plus open full files, upload, reindex, and delete documents |
| System administrator `root` | All of the above, plus accounts, roles, settings, and audit management |

Device selection scopes retrieval by filename; unmatched files need manual selection. It is **not access control**. All authorized users can still ask about the shared document library; department-level and per-person isolation are not implemented. Regular users cannot download a complete file directly, but a short file may fit in one citation excerpt, and repeated questions may reveal much of its content. Do not use this version unchanged where users must be prevented from learning one another's documents.

If runtime internet access must be prohibited after model download, enforce this with host and network egress policy; `.env` offline flags are not a firewall. [First use](docs/GETTING_STARTED.md) (Chinese only) · [Troubleshooting](docs/TROUBLESHOOTING.md) (Chinese only)
