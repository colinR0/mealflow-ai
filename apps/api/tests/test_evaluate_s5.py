import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[3]
DATASET = ROOT / "data" / "s5_eval_queries.json"
SCRIPT = ROOT / "scripts" / "evaluate_s5.py"


def test_s5_dataset_is_utf8_json_with_expected_splits_and_markers() -> None:
    payload = json.loads(DATASET.read_bytes().decode("utf-8"))

    assert len(payload["dev"]) == 40
    assert len(payload["frozen"]) == 40
    assert payload["embedding"]["provider"] == "fake_embedding"
    assert payload["embedding"]["quality_claim"] is False


def test_evaluate_s5_rejects_invalid_utf8_without_emitting_metrics(tmp_path: Path) -> None:
    dataset = tmp_path / "broken.json"
    dataset.write_bytes(b"{\xff")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--dataset", str(dataset)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "failed to load S5 evaluation dataset" in completed.stderr


def test_evaluate_s5_rejects_invalid_json_without_emitting_metrics(tmp_path: Path) -> None:
    dataset = tmp_path / "broken.json"
    dataset.write_text("{\"dev\": []", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--dataset", str(dataset)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "failed to load S5 evaluation dataset" in completed.stderr
