#!/usr/bin/env python3
"""检索评测：对比 bm25 / hybrid / rerank 的 recall@k、MRR 与负例误召。

用法：
  python scripts/eval_retrieval.py                       # 默认 config.yaml + ~/.shibei/eval/golden.yaml
  python scripts/eval_retrieval.py --modes bm25,hybrid --min-scores 0.0,0.5,0.6
  python scripts/eval_retrieval.py --modes rerank --verbose

golden.yaml 格式：
  queries:
    - query: "HSBC 47150 薪资锚"
      expect: ["HSBC_47150_复习清单.md"]   # 任一 basename 命中即算召回
    - query: "红烧肉怎么做"
      expect: []                          # 负例：期望零结果
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shibei.config import load_config  # noqa: E402
from shibei.searcher import Searcher  # noqa: E402

DEFAULT_GOLDEN = Path.home() / ".shibei" / "eval" / "golden.yaml"


def load_golden(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    queries = data.get("queries") or []
    if not queries:
        raise SystemExit(f"评测集为空: {path}")
    return queries


def result_name(item: dict) -> str:
    path = item.get("file_path") or item.get("source") or ""
    return Path(path).name


def evaluate(cfg_path: str, golden: list[dict], mode: str, min_score: float,
             add_new_rows: bool, k: int) -> dict:
    cfg = load_config(cfg_path)
    cfg.vector.enabled = mode in {"hybrid", "rerank"}
    cfg.rerank.enabled = mode == "rerank"
    cfg.vector.min_score = min_score
    cfg.vector.add_new_rows = add_new_rows

    positives = [g for g in golden if g.get("expect")]
    negatives = [g for g in golden if not g.get("expect")]
    hits = 0
    rr_sum = 0.0
    neg_fp = 0

    for item in positives:
        expected = {Path(name).name for name in item["expect"]}
        results = Searcher(cfg).search(
            item["query"], limit=k, include_memories=False
        ).get("results", [])
        first = next(
            (rank for rank, r in enumerate(results, start=1)
             if result_name(r) in expected),
            0,
        )
        if first:
            hits += 1
            rr_sum += 1.0 / first

    for item in negatives:
        results = Searcher(cfg).search(
            item["query"], limit=k, include_memories=False
        ).get("results", [])
        if results:
            neg_fp += 1

    return {
        "mode": mode,
        "min_score": min_score,
        "add_new_rows": add_new_rows,
        f"R@{k}": hits / len(positives) if positives else 0.0,
        "MRR": rr_sum / len(positives) if positives else 0.0,
        "neg_fp": f"{neg_fp}/{len(negatives)}" if negatives else "-",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="检索评测")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--modes", default="bm25,hybrid")
    parser.add_argument("--min-scores", default="0.0,0.5,0.6")
    parser.add_argument("--add-new-rows", action="store_true",
                        help="额外测试向量引入新文档（默认只重排）")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    golden_path = Path(args.golden).expanduser()
    if not golden_path.exists():
        raise SystemExit(
            f"找不到评测集: {golden_path}\n"
            f"可参考 eval/golden.example.yaml 创建"
        )
    golden = load_golden(golden_path)

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    min_scores = [float(s) for s in args.min_scores.split(",") if s.strip()]
    add_modes = [False, True] if args.add_new_rows else [False]

    rows = []
    for mode in modes:
        for add_new in add_modes:
            scores = min_scores if mode in {"hybrid", "rerank"} else [0.0]
            for min_score in scores:
                rows.append(evaluate(
                    args.config, golden, mode, min_score, add_new, args.k
                ))

    header = f"{'mode':8} {'min':>5} {'new':>5} {'R@'+str(args.k):>6} {'MRR':>6} {'neg_fp':>7}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['mode']:8} {row['min_score']:5.2f} "
            f"{str(row['add_new_rows']):>5} "
            f"{row[f'R@{args.k}']:6.3f} {row['MRR']:6.3f} {row['neg_fp']:>7}"
        )

    if args.verbose:
        print()
        for item in golden:
            expected = {Path(n).name for n in item.get("expect", [])}
            results = Searcher(load_config(args.config)).search(
                item["query"], limit=args.k, include_memories=False
            ).get("results", [])
            names = [result_name(r) for r in results]
            mark = "NEG" if not expected else ("HIT" if any(n in expected for n in names) else "MISS")
            print(f"[{mark}] {item['query']} -> {names[:args.k]}")


if __name__ == "__main__":
    main()
