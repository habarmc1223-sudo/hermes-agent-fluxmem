#!/usr/bin/env python3
"""Index, search, and evaluate allowed Markdown knowledge roots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge.obsidian_index import KnowledgeIndex, openai_embedder


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync")
    sync.add_argument("roots", nargs="+", type=Path)
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("cases", type=Path)
    args = parser.parse_args()

    index = KnowledgeIndex(embedder=openai_embedder())
    try:
        if args.command == "sync":
            result = index.sync(args.roots)
        elif args.command == "search":
            result = [hit.__dict__ for hit in index.search(args.query, args.limit)]
        else:
            cases = json.loads(args.cases.read_text(encoding="utf-8"))
            result = index.evaluate(cases)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        index.close()


if __name__ == "__main__":
    raise SystemExit(main())
