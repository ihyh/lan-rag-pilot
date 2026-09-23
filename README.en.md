# LAN RAG Pilot

[中文](README.md) | [English](README.en.md)

Upload documents, select a device, and ask a question. The app retrieves relevant passages, generates an answer with local Ollama, and shows citations. Runtime components are the RAG app (FastAPI, SQLite), a BGE retrieval model, and an Ollama generation model.

| Mode | Where the services run | How users connect |
| --- | --- | --- |
| Windows personal knowledge base | All three components on your Windows computer | Local browser at `http://127.0.0.1:8088` |
| Linux enterprise knowledge base | All three components on the Linux server, with no Windows model or app dependency | Employees open the fixed internal HTTPS URL |

The repository does not include model files, passwords, or business documents. Windows personal and Linux enterprise installations keep separate data, BGE, and Ollama instances; documents are not synchronized automatically. Connected setup scripts download dependencies and models on their first run, while an offline enterprise server imports an administrator-verified package.

`192.168.136.128` was only the address of a VMware test VM; it is not a project URL. A company deployment uses the Linux server's actual internal IP and, after DNS and TLS are configured, the internal URL provided by IT.

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

The RAG app, SQLite database, BGE retrieval model, and Ollama generation model all run on the Linux server. Employees use a browser on a network that can reach it. Runtime does not depend on Windows applications, models, shared folders, or proxies.

### 1. Identify the deployment

An existing migration uses `/opt/rag/compose.yaml`. Application source, data, BGE, and Ollama models are stored under `/opt/rag/source`, `data`, `models`, and `ollama`, respectively. If migration is complete, start at step 3; do not clone again or import the old data again.

For a new server, follow the [offline package guide](docs/OFFLINE_PACKAGE.md) and [Ubuntu deployment guide](docs/UBUNTU.md) (Chinese only). That guide uses `/opt/rag/docker-compose.yml`, a different filename from the migration package. Select the matching file in the commands below.

The repository's original `docker-compose.yml` contains only the RAG service. It does not replace the enterprise configuration containing both RAG and Ollama. `setup_linux.sh` is for connected local acceptance with host Python and Ollama, not for starting this container deployment.

### 2. Prepare a new server

An administrator completes the following steps using the commands in the [Ubuntu guide](docs/UBUNTU.md):

1. Install Docker Engine, the Compose plugin, and operational tools required by the guide, including Nginx and Python 3. Images provide the app's Python dependencies and Ollama.
2. Verify and import the RAG image, Ollama image, complete BGE model, and generation model. Back up existing data before following the migration procedure.
3. Save the deployment Compose file and `.env`. Point the model API at `http://ollama:11434/v1` inside the container network and match the model name to the imported model. The current migration package uses `qwen3:1.7b`; the offline guide's `qwen3:4b` is a separate delivery example.
4. For a new instance, configure a random session secret and a separate initial `root` password. Migrated instances retain existing account passwords. Set `.env` permissions to `600`, allowing only its owner to read and write it.
5. Configure the server URL. For company access, enable internal HTTPS following the guide, set `RAG_PUBLIC_ORIGIN` to the actual URL, and set `RAG_COOKIE_SECURE=true`.

### 3. Start and check services

Run these commands in the **Linux server terminal**. Select the actual deployment file first. For a new installation following the offline guide, change the first line to `RAG_COMPOSE=/opt/rag/docker-compose.yml`.

```bash
RAG_COMPOSE=/opt/rag/compose.yaml
sudo systemctl enable --now docker
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" config --quiet
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" up -d --pull never --no-build
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" ps
```

Images, models, and configuration must already be prepared. Missing images cause an error; these commands do not download or build them. Services run in the background. Once the prompt returns, you can close the terminal; do not also run `app/main.py`.

### 4. Open the website and verify the installation

1. Confirm that `ps` shows both `rag` and `ollama` running, then wait for `rag` to become `healthy`.
2. Open the actual deployment URL in a browser. The VMware migration test uses [http://192.168.136.128:8088](http://192.168.136.128:8088), which applies only while the VM retains that IP and port mapping. A company server uses the internal HTTPS URL provided by IT.
3. For a new instance, sign in with the configured initial `root` password. For a migrated instance, use an existing account and password.
4. Verify existing documents or upload a test document. Select a device or document scope, ask a question, and confirm that both the answer and citations appear.

Employee computers do not need Python, Docker, BGE, or Ollama. The VMware test requires both its host and VM to be running; an independent company Linux server requires only the server itself. Starting containers does not establish that login, complete question answering, or HTTPS acceptance has passed.

### 5. Start, stop, and update later

Use step 3 for subsequent starts. Docker startup and `restart: unless-stopped` restore containers that were not manually stopped. After a manual stop, run `up -d` again.

To stop services in the same terminal:

```bash
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" stop
```

In a new terminal, set `RAG_COMPOSE` as shown in step 3 first. A normal stop preserves data. Do not use `down -v` or delete data directories as a routine stop procedure.

For updates, follow the [operations guide](docs/OPERATIONS.md): back up and verify recovery, import the new image, check configuration, and start and validate the release. Running `git pull` alone does not update running containers. Routine startup does not require rerunning migration scripts.

### 6. Inspect logs and model status

In a terminal with `RAG_COMPOSE` set, run:

```bash
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" logs --tail 100 rag ollama
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" exec ollama ollama list
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" exec ollama ollama ps
```

Servers without GPUs can generate answers on CPU, and initial model loading also takes time. If the page keeps waiting, inspect loading, generation, queuing, and API errors in the logs; waiting alone does not prove retrieval is slow. BGE encoding currently runs on CPU. Validate model and parameter changes with real questions; see [retrieval validation](docs/RETRIEVAL_VALIDATION.md).

For company access, follow the [Ubuntu guide](docs/UBUNTU.md) to provide internal HTTPS through Nginx and allow only approved employee subnets. Keep port 8088 on server loopback and do not expose port 11434 to employee devices. The `root` administrator creates individual employee accounts, assigns roles, and deactivates leavers. See the [security requirements](docs/SECURITY.md).

| Role assigned by an administrator | Current permissions |
| --- | --- |
| Regular `user` (tester/developer/employee) | Ask about a selected device and see citation excerpts; cannot open full files or manage documents |
| Document administrator `kb_admin` | Above, plus open full files, upload, reindex, and delete documents |
| System administrator `root` | All of the above, plus accounts, roles, settings, and audit management |

Device selection scopes retrieval by filename; unmatched files need manual selection. It is **not access control**.

Per-document visibility is set by `root`: the default is "everyone", and a document can be switched to "only the accounts listed here". A restricted document is invisible to unauthorized accounts across **retrieval, the document list, original-file access and conversation history** — it cannot be retrieved at all, and after a grant is revoked the turn that cited it is hidden as well. `root` and `kb_admin` are exempt (otherwise restricted documents could not be managed); opening a restricted original as an administrator is written to the audit log.

Note the library is still **shared by default and there is no department/group isolation**. Regular users cannot download a complete file directly, but an **unrestricted** short file may fit in one citation excerpt, and repeated questions may reveal much of its content — so mark anything confidential as restricted instead of relying on "no download".

If runtime internet access must be prohibited after model download, enforce this with host and network egress policy; `.env` offline flags are not a firewall. [First use](docs/GETTING_STARTED.md) (Chinese only) · [Troubleshooting](docs/TROUBLESHOOTING.md) (Chinese only)

## Startup configuration guard

At startup the application validates key configuration and **refuses to start** when it is missing or unsafe, instead of only logging a warning:

- `RAG_SECRET_KEY` must be a random value (otherwise session tokens fall back to a development key that is public in the source);
- the `DEEPSEEK_BASE_URL` host must be internal (private range, loopback, a single-label host such as `ollama`, an internal suffix, or listed explicitly in `RAG_LLM_TRUSTED_HOSTS`); public addresses are rejected;
- `RAG_PUBLIC_ORIGIN` must not contradict the transport settings (for example an HTTPS origin with `RAG_COOKIE_SECURE=false`).

The error message names the missing item and how to fix it. For local debugging only, `RAG_ALLOW_INSECURE_START=1` skips every check and the startup banner prints a prominent **INSECURE MODE** warning.

The bundled `docker-compose.yml` publishes port 8088 on loopback only (`127.0.0.1:8088`); external access must go through a host reverse proxy, see the [Ubuntu guide](docs/UBUNTU.md) (Chinese only).
