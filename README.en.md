# Personal Knowledge Base

Store manuals, spreadsheets and images locally, retrieve supporting passages, and use Ollama to answer with citations. [中文说明](README.md)

For Windows 10/11, with support for individual use and team access.

## Screenshots

Demo screenshots use fictional documents and a mock answer. Click to enlarge.

| Chat, citations and history | Document management and device metadata |
| --- | --- |
| [![Chat](docs/screenshots/chat.jpg)](docs/screenshots/chat.jpg) | [![Documents](docs/screenshots/documents.jpg)](docs/screenshots/documents.jpg) |
| **Keyword search and source passages** | **Users, roles and groups** |
| [![Source search](docs/screenshots/source-search.jpg)](docs/screenshots/source-search.jpg) | [![Users and permissions](docs/screenshots/users.jpg)](docs/screenshots/users.jpg) |

## Quick start

Install Git, Python 3.12 and [Ollama](https://ollama.com/download/windows). Start Ollama, then run in PowerShell:

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
cd C:\rag
.\setup_windows.cmd
```

The first setup needs an internet connection to download dependencies and models. Save the initial `root` password shown during setup.

Open **http://127.0.0.1:8088**, sign in, upload a document and wait for processing to finish. You can then ask questions.

For later starts, double-click `start_windows.cmd` in the installation folder.

## Features

| Feature | What you can do |
| --- | --- |
| Chat | Ask questions about your documents, check references and revisit conversations |
| Documents | Upload files and images, organize by device and review recognized text |
| Source search | Find keywords and matching passages in your documents |
| Users and permissions | Manage accounts and control access to documents |

Supports PDF, Word (DOCX), Excel (XLSX), TXT, Markdown and PNG/JPEG. Review text recognized from scans and images before using it.

## Your data

Documents and conversations are stored locally by default. Personal accounts, documents and databases are not included in this repository. Back up `data` and `.env` before updating.

## Help

[Installation and mobile access](docs/WINDOWS.md) · [Getting started](docs/GETTING_STARTED.md) · [Model settings](docs/MODELS.md) · [Backup and updates](docs/OPERATIONS.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [All documentation](docs/README.md)
