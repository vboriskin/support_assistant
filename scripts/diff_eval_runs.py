"""Сравнение двух eval-прогонов.

    python -m scripts.diff_eval_runs <old_run_id> <new_run_id>
    python -m scripts.diff_eval_runs --old evals/reports/a.json --new evals/reports/b.json

Считает per-case дельты по faithfulness / helpfulness / recall_at_5 и
адverbsarial pass, выводит regressions и improvements. Используется
перед мерджем правок промпта.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPORTS_DIR = Path(__file__).resolve().parent.parent / "evals" / "reports"

REGRESS_FAITH = (0.8, 0.6)         # с > 0.8 упало < 0.6
REGRESS_HELP = (0.6, 0.3)          # с > 0.6 упало < 0.3
RECALL_FALL = (1.0, 0.0)           # 1.0 -> 0.0


def _load(arg: str) -> dict[str, Any]:
    p = Path(arg)
    if not p.exists():
        p = REPORTS_DIR / f"{arg}.json"
    if not p.exists():
        print(f"report not found: {arg}", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(p.read_text(encoding="utf-8"))


def _by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["case_id"]: r for r in report.get("results", [])}


def compare(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_by = _by_id(old)
    new_by = _by_id(new)
    common = sorted(set(old_by) & set(new_by))
    only_old = sorted(set(old_by) - set(new_by))
    only_new = sorted(set(new_by) - set(old_by))

    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []

    for cid in common:
        o, n = old_by[cid], new_by[cid]
        # faithfulness
        if o["faithfulness"] > REGRESS_FAITH[0] and n["faithfulness"] < REGRESS_FAITH[1]:
            regressions.append(
                {"case_id": cid, "metric": "faithfulness",
                 "old": o["faithfulness"], "new": n["faithfulness"]}
            )
        if o["faithfulness"] < REGRESS_FAITH[1] and n["faithfulness"] > REGRESS_FAITH[0]:
            improvements.append(
                {"case_id": cid, "metric": "faithfulness",
                 "old": o["faithfulness"], "new": n["faithfulness"]}
            )
        # helpfulness
        if o["helpfulness"] > REGRESS_HELP[0] and n["helpfulness"] < REGRESS_HELP[1]:
            regressions.append(
                {"case_id": cid, "metric": "helpfulness",
                 "old": o["helpfulness"], "new": n["helpfulness"]}
            )
        if o["helpfulness"] < REGRESS_HELP[1] and n["helpfulness"] > REGRESS_HELP[0]:
            improvements.append(
                {"case_id": cid, "metric": "helpfulness",
                 "old": o["helpfulness"], "new": n["helpfulness"]}
            )
        # recall@5
        if o["recall_at_5"] == RECALL_FALL[0] and n["recall_at_5"] == RECALL_FALL[1]:
            regressions.append(
                {"case_id": cid, "metric": "recall_at_5", "old": 1.0, "new": 0.0}
            )
        if o["recall_at_5"] == 0.0 and n["recall_at_5"] == 1.0:
            improvements.append(
                {"case_id": cid, "metric": "recall_at_5", "old": 0.0, "new": 1.0}
            )
        # adversarial passed (None | bool)
        old_adv = o.get("adversarial_passed")
        new_adv = n.get("adversarial_passed")
        if old_adv is True and new_adv is False:
            regressions.append(
                {"case_id": cid, "metric": "adversarial", "old": True, "new": False}
            )
        if old_adv is False and new_adv is True:
            improvements.append(
                {"case_id": cid, "metric": "adversarial", "old": False, "new": True}
            )

    return {
        "old_run_id": old.get("run_id"),
        "new_run_id": new.get("run_id"),
        "common_cases": len(common),
        "only_old": only_old,
        "only_new": only_new,
        "regressions": regressions,
        "improvements": improvements,
        "aggregate_old": old.get("aggregate") or {},
        "aggregate_new": new.get("aggregate") or {},
    }


def _print_section(title: str, items: list[dict[str, Any]]) -> None:
    print(f"\n== {title} ({len(items)}) ==")
    for r in items:
        print(f"  {r['case_id']:<24} {r['metric']:<14} {r['old']!s:>6}  →  {r['new']!s}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Diff two eval runs")
    p.add_argument("old", nargs="?", help="старый run_id или путь к .json")
    p.add_argument("new", nargs="?", help="новый run_id или путь к .json")
    p.add_argument("--old", dest="old_opt", help="как старый позиционный")
    p.add_argument("--new", dest="new_opt", help="как новый позиционный")
    p.add_argument("--json", action="store_true", help="вывод в JSON")
    args = p.parse_args(argv)

    old_arg = args.old or args.old_opt
    new_arg = args.new or args.new_opt
    if not old_arg or not new_arg:
        p.error("usage: diff_eval_runs <old> <new>")

    diff = compare(_load(old_arg), _load(new_arg))

    if args.json:
        print(json.dumps(diff, ensure_ascii=False, indent=2))
        return 0

    print(f"old: {diff['old_run_id']}  ({len(diff['only_old'])} only-old)")
    print(f"new: {diff['new_run_id']}  ({len(diff['only_new'])} only-new)")
    print(f"common: {diff['common_cases']} cases")
    _print_section("REGRESSIONS", diff["regressions"])
    _print_section("IMPROVEMENTS", diff["improvements"])

    ao = diff["aggregate_old"]
    an = diff["aggregate_new"]
    print("\n== AGGREGATE DELTA ==")
    for k in ("recall_at_5", "mrr", "faithfulness", "helpfulness",
              "adversarial_pass_rate", "no_answer_pass_rate"):
        if k in ao or k in an:
            o = ao.get(k)
            n = an.get(k)
            print(f"  {k:<24} {o!s:>8}  →  {n!s}")
    return 1 if diff["regressions"] else 0


if __name__ == "__main__":
    sys.exit(main())
