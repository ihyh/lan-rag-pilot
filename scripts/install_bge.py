from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path


URL = "https://github.com/ihyh/lan-rag-pilot/releases/download/bge-small-zh-v1.5-7999e1d/bge-small-zh-v1.5-7999e1d.zip"
EXPECTED_SHA256 = "0edacc059c0d792466da7b83569c0406aef88b334f6b297d11f5ee5bbf4499c2"


def model_is_valid(model_dir: Path) -> bool:
    if not (model_dir / "model.safetensors").is_file():
        return False
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(str(model_dir), local_files_only=True, device="cpu")
        if hasattr(model, "get_embedding_dimension"):
            return model.get_embedding_dimension() == 512
        return model.get_sentence_embedding_dimension() == 512
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()

    project = args.project.resolve()
    models_dir = project / "models"
    model_dir = models_dir / "bge-small-zh-v1.5"
    if model_is_valid(model_dir):
        print(f"BGE model is ready (512 dimensions): {model_dir}")
        return 0

    print("Downloading BGE model from GitHub Release...")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temporary:
        zip_path = Path(temporary.name)
        with urllib.request.urlopen(URL, timeout=120) as response:
            shutil.copyfileobj(response, temporary)

    try:
        actual_sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        if actual_sha256 != EXPECTED_SHA256:
            raise RuntimeError("BGE package checksum failed. Run the script again.")

        models_dir.mkdir(parents=True, exist_ok=True)
        models_root = models_dir.resolve()
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                destination = (models_dir / member.filename).resolve()
                if not destination.is_relative_to(models_root):
                    raise RuntimeError("BGE package contains an unsafe path.")
            archive.extractall(models_dir)
    finally:
        zip_path.unlink(missing_ok=True)

    if not model_is_valid(model_dir):
        raise RuntimeError("BGE model validation failed after extraction.")
    print(f"BGE model is ready (512 dimensions): {model_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
