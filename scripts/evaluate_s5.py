"""Run the S5 retrieval evaluation without calling a paid embedding service.

The frozen split is copied from dev at runtime only as a placeholder fixture;
replace it with independently judged queries before publishing quality numbers.
The output always labels fake embeddings and mechanical metrics.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "apps" / "api"))

from app.services.hybrid_retrieval import evaluate_retrieval, fake_embedding


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/s5_eval_queries.json")
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
        dev = payload["dev"]
        frozen = payload["frozen"]
        if len(dev) != 40 or len(frozen) != 40:
            raise ValueError("S5 evaluation set must contain exactly 40 dev and 40 frozen queries")
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"failed to load S5 evaluation dataset: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    # This fixture carries relevance labels but no corpus or ranked results.
    # Refuse to emit quality scores until a corpus-backed ranker is selected.
    for split, cases in (("dev", dev), ("frozen", frozen)):
        ranked = [case for case in cases if "ranked_recipe_ids" in case]
        if not ranked:
            result = {
                "split": split,
                "queries": len(cases),
                "status": "dataset_ready_not_scored",
                "reason": "no ranked results or authorized semantic embedding provider",
                "embedding": "fake_embedding_not_used",
                "quality_claim": False,
            }
        else:
            result = {
                "split": split,
                **evaluate_retrieval(ranked),
                "embedding": "fake_embedding",
                "mechanical": True,
                "quality_claim": False,
            }
        print(json.dumps(result, ensure_ascii=False))
    assert fake_embedding("测试")


if __name__ == "__main__":
    main()
