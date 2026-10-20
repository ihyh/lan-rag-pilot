"""Layered retrieval metrics must require every evidence unit, not any hit."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("retrieval_eval", ROOT / "scripts/retrieval_eval.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
from app.index import VectorIndex


def main():
    case = {"id": "multi", "split": "development", "group": "fact-a", "question": "问题",
            "required_evidence": [{"chunk_ids": [1], "quotes": ["must"]}, {"chunk_ids": [2, 3], "quotes": ["condition"]}]}
    sources = [{"chunk_id": 1, "content": "must"}, {"chunk_id": 2, "content": "condition"}]
    assert evaluation.evidence_metrics(case, sources, sources)["evidence_complete"]
    missing = evaluation.evidence_metrics(case, sources, sources[:1])
    assert not missing["evidence_complete"] and missing["candidate_recall"] == 1
    assert missing["evidence_recall"] == .5 and "selection_lost_evidence" in missing["failure_stages"]
    truncated = [sources[0], {"chunk_id": 2, "content": "con"}]
    assert not evaluation.evidence_metrics(case, sources, truncated)["evidence_complete"]
    assert evaluation.evidence_metrics(case, sources, truncated)["evidence_recall"] == .5
    upstream = dict(case, index_evidence_complete=False, upstream_failures=["chunk_relation_broken"])
    assert not evaluation.evidence_metrics(upstream, sources, sources)["evidence_complete"]
    assert "selection_lost_evidence" not in evaluation.evidence_metrics(upstream, sources, sources)["failure_stages"]
    unknown = evaluation.evidence_metrics(dict(case, required_evidence=[]), sources, sources)
    assert unknown["unanswerable"] and not unknown["evidence_complete"]
    assert unknown["fact_correct"] is unknown["citation_supported"] is None
    assert evaluation.peak_memory_bytes() > 0
    with tempfile.TemporaryDirectory(prefix="retrieval-eval-") as directory:
        path = Path(directory) / "cases.json"
        path.write_text(json.dumps([case]), encoding="utf-8")
        command = [sys.executable, str(ROOT / "scripts/retrieval_eval.py"), "--cases", str(path),
                   "--db", str(Path(directory) / "missing.db"), "--out", str(Path(directory) / "unused.json"),
                   "--split", "development", "--validate-only"]
        assert subprocess.run(command, capture_output=True).returncode == 0
        assert not (Path(directory) / "missing.db").exists()
        path.write_text(json.dumps([case, dict(case, id="held", split="acceptance")]), encoding="utf-8")
        try:
            evaluation.load_cases(path)
        except AssertionError:
            pass
        else:
            raise AssertionError("shared fact family crossed train/held-out splits")
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE documents(id INTEGER,status TEXT,filename TEXT);"
                     "CREATE TABLE chunks(id INTEGER,document_id INTEGER,content TEXT,vector BLOB);")
    db.execute("INSERT INTO documents VALUES (1,'ready','manual.txt')")
    db.execute("INSERT INTO chunks VALUES (1,1,'P11 parameter',?)", (np.array([1, 0], dtype=np.float32).tobytes(),))
    index = VectorIndex()
    index.reload(db)
    trace = {}
    expected = index.search(np.array([1, 0]), 3, query_text="P11")
    assert expected == index.search(np.array([1, 0]), 3, query_text="P11", trace=trace)
    assert trace["channels"]["semantic"] == trace["channels"]["technical"] == trace["fused"] == [1]
    assert index.search(np.array([1, 0]), 3, document_ids=set(), trace={}) == []
    print("Layered retrieval evaluator, complete-evidence denominator, split and trace controls passed.")


if __name__ == "__main__":
    main()
