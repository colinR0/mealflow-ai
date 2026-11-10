"""Chunk the research parent corpus with the local BGE tokenizer.

Run from apps/api:
  python -m app.cli.chunk_research_recipes --model-dir ../../models/bge-small-zh-v1.5

A missing model stops the command. Chunks that cannot fit without truncation are
recorded and left out of the vector file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.cli.register_recipe_sources import ROOT
from app.services.research_chunks import POLICY_VERSION, build_research_corpus
from app.services.research_index import token_counter

DEFAULT_INPUT = ROOT / "output/howtocook-research-v1/parents.jsonl"
DEFAULT_OUTPUT = ROOT / "output/recipe-research-chunks-v1"
DEFAULT_MODEL = ROOT / "models/bge-small-zh-v1.5"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--max-chars", type=int, default=450)
    parser.add_argument("--policy-version", default=POLICY_VERSION)
    args = parser.parse_args(argv)
    if args.max_chars < 80:
        parser.error("--max-chars must be at least 80")
    input_path = args.input if args.input.is_absolute() else ROOT / args.input
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    model_dir = args.model_dir if args.model_dir.is_absolute() else ROOT / args.model_dir
    try:
        count_tokens, max_tokens = token_counter(model_dir)
        summary = build_research_corpus(
            input_path,
            output_dir,
            max_chars=args.max_chars,
            max_tokens=max_tokens,
            count_tokens=count_tokens,
            policy_version=args.policy_version,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"research chunking failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
