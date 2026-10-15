"""Direct Windows Task Scheduler entry; resolve all paths from this checkout."""
import os
import site
import sys
from datetime import datetime
from pathlib import Path


def main():
    project = Path(__file__).resolve().parents[1]
    os.chdir(project)
    packages = project / ".venv" / "Lib" / "site-packages"
    if not packages.is_dir() or not (project / ".env").is_file():
        raise SystemExit("Run setup_windows.cmd first to create .venv and .env.")
    sys.path[:0] = [str(packages), str(project)]
    site.addsitedir(str(packages))
    log_dir = project / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    sys.stdout = (log_dir / f"service-{stamp}.out.log").open("a", encoding="utf-8", buffering=1)
    sys.stderr = (log_dir / f"service-{stamp}.err.log").open("a", encoding="utf-8", buffering=1)
    from dotenv import load_dotenv
    import uvicorn
    load_dotenv(project / ".env", override=True)
    uvicorn.run("app.main:app", host=os.environ.get("RAG_HOST", "127.0.0.1"),
                port=int(os.environ.get("RAG_PORT", "8088")))


if __name__ == "__main__":
    main()
