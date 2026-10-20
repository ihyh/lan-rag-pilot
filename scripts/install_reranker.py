"""Download only the official reranker to an explicit local directory; no private data sent."""
import argparse
import hashlib
import json
from pathlib import Path

MODEL = "BAAI/bge-reranker-v2-m3"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    manifest_path = destination / "RERANKER_SOURCE.json"
    download_path = destination / "RERANKER_DOWNLOAD.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["model"] != MODEL:
            raise ValueError("destination contains another model")
        for name, expected in manifest["files"].items():
            with (destination / name).open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != expected:
                    raise ValueError("local model hash mismatch; preserve it and select a new directory")
        print("Existing pinned local reranker hashes verified; no download or runtime change.")
        return
    if destination.exists() and any(destination.iterdir()) and not download_path.exists():
        raise ValueError("destination is nonempty and not an owned resumable download; choose a new directory")
    from huggingface_hub import HfApi, snapshot_download
    prior = json.loads(download_path.read_text(encoding="utf-8")) if download_path.exists() else None
    if prior and prior["model"] != MODEL:
        raise ValueError("download marker model mismatch")
    info = HfApi().model_info(MODEL, revision=prior["revision"] if prior else None, files_metadata=True)
    names = [s.rfilename for s in info.siblings if s.rfilename in {
        "config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
        "sentencepiece.bpe.model", "model.safetensors", "README.md"}]
    assert "model.safetensors" in names and "config.json" in names
    size = sum(s.size or 0 for s in info.siblings if s.rfilename in names)
    print("Official model", MODEL, "revision", info.sha, "bytes", size, flush=True)
    destination.mkdir(parents=True, exist_ok=True)
    if prior is None:
        with download_path.open("x", encoding="utf-8") as handle:
            json.dump({"model": MODEL, "revision": info.sha}, handle, indent=2)
    snapshot_download(MODEL, revision=info.sha, local_dir=destination, allow_patterns=names)
    hashes = {}
    for name in names:
        with (destination / name).open("rb") as handle:
            hashes[name] = hashlib.file_digest(handle, "sha256").hexdigest()
    manifest = {"model": MODEL, "revision": info.sha, "files": hashes}
    if manifest_path.exists():
        assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    else:
        with manifest_path.open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print("Prepared local reranker. Set RAG_RERANK_MODEL to", destination, "; enabling is a separate step.")


if __name__ == "__main__":
    main()
