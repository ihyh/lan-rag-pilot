# Personal Knowledge Base · Equipment Document Search and Q&A

A web app that runs on a Windows PC. Upload equipment manuals, communication protocols, alarm tables and wiring diagrams, then ask questions such as “How do I inspect this machine?” or “What does this alarm mean?”

The app searches your uploaded documents and uses Ollama to generate an answer with references to the files and passages it used. You can also search for keywords without generating an answer. [中文说明](README.md)

## What you can do

| Task | How it works |
| --- | --- |
| Look up operating procedures or faults | Select a device or specific documents in Chat, then ask your question |
| Check an answer against its source | Expand the references to read file names and source passages; users with permission can open the original file |
| Find commands, parameters or alarm codes | Enter keywords in Source search to see matching text and its location, without generating an answer |
| Organize manuals, scans and diagrams | Upload files, label their device type, review OCR text and add descriptions to images |
| Revisit questions and report mistakes | Browse conversation history and submit feedback; administrators record corrections and follow-up notes |
| Give colleagues access | Create accounts, assign roles and groups, and set document access permissions |

Supports PDF, Word (DOCX), Excel (XLSX), TXT, Markdown and PNG/JPEG. Review recognized text and check answers against the original documents.

## Screenshots

Demo screenshots use fictional documents and a mock answer. Click to enlarge.

| Chat, citations and history | Document management and device metadata |
| --- | --- |
| [![Chat](docs/screenshots/chat.jpg)](docs/screenshots/chat.jpg) | [![Documents](docs/screenshots/documents.jpg)](docs/screenshots/documents.jpg) |
| **Keyword search and source passages** | **Users, roles and groups** |
| [![Source search](docs/screenshots/source-search.jpg)](docs/screenshots/source-search.jpg) | [![Users and permissions](docs/screenshots/users.jpg)](docs/screenshots/users.jpg) |

## Quick start

On Windows 10/11, install Git, Python 3.12 and [Ollama](https://ollama.com/download/windows). Start Ollama, then run in PowerShell:

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
cd C:\rag
.\setup_windows.cmd
```

The first setup needs an internet connection to download dependencies and models. Save the initial `root` password shown during setup.

Open **http://127.0.0.1:8088** and sign in as `root`. Upload a file in Documents. Once its status is Ready, open Chat and ask a question.

For later starts, double-click `start_windows.cmd` in the installation folder.

## Your data

Documents and conversations are stored locally by default. Personal accounts, documents and databases are not included in this repository. Back up `data` and `.env` before updating.

## Help

[Installation and mobile access](docs/WINDOWS.md) · [Getting started](docs/GETTING_STARTED.md) · [Model settings](docs/MODELS.md) · [Backup and updates](docs/OPERATIONS.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [All documentation](docs/README.md)
