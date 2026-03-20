"""
Confusion-case test runner for AWB OCR normalization quality.

Purpose:
- Stress-test OCR confusion handling (e.g., T->7, O->0, I/L->1, S->5, B->8, G->6)
- Measure precision / recall against the current AWB DB before tuning
- Catch regressions without changing runtime pipeline behavior

Usage examples:
  python Scripts/pipeline_confusion_test_runner.py
  python Scripts/pipeline_confusion_test_runner.py --sample-size 800 --negative-size 800
  python Scripts/pipeline_confusion_test_runner.py --include-tolerance
  python Scripts/pipeline_confusion_test_runner.py --case-file data/confusion_cases.csv
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import string
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
import Scripts.awb_hotfolder_V2 as hf


CONFUSION_BY_DIGIT: Dict[str, List[str]] = {
    "0": ["O", "Q", "D"],
    "1": ["I", "L"],
    "2": ["Z"],
    "5": ["S"],
    "6": ["G"],
    "7": ["T"],
    "8": ["B"],
}


@dataclass
class TestCase:
    text: str
    expected_awb: Optional[str]
    kind: str
    note: str = ""
    raw_token: Optional[str] = None


def _safe_ratio(num: int, den: int) -> float:
    return (num / den) if den else 0.0


def _format_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _mutate_awb_token(rng: random.Random, awb: str, max_subs: int = 2) -> Tuple[str, List[Tuple[int, str, str]]]:
    chars = list(awb)
    positions = [i for i, ch in enumerate(chars) if ch in CONFUSION_BY_DIGIT]
    if not positions:
        return awb, []

    n_subs = rng.randint(1, min(max_subs, len(positions)))
    chosen = rng.sample(positions, n_subs)
    edits: List[Tuple[int, str, str]] = []
    for idx in chosen:
        orig = chars[idx]
        repl = rng.choice(CONFUSION_BY_DIGIT[orig])
        chars[idx] = repl
        edits.append((idx, orig, repl))

    token = "".join(chars)
    if rng.random() < 0.35:
        sep = rng.choice([" ", "-", "/", "."])
        token = f"{token[:4]}{sep}{token[4:8]}{sep}{token[8:]}"
    return token, edits


def _make_positive_case(rng: random.Random, awb: str) -> TestCase:
    token, edits = _mutate_awb_token(rng, awb, max_subs=2)
    templates = [
        "AWB NUMBER: {token}",
        "AIRWAY BILL NO {token}",
        "Commercial Invoice\nTracking: {token}\nRef: ABC123",
        "Carrier: FEDEX\nTracking Number {token}",
        "Shipment docs\nAWB# {token}\nItem details below",
    ]
    text = rng.choice(templates).format(token=token)
    note = ", ".join([f"{o}->{r}@{i}" for i, o, r in edits]) if edits else "no_edit"
    return TestCase(text=text, expected_awb=awb, kind="positive", note=note, raw_token=token)


def _random_noise_token(rng: random.Random, length: int = 12) -> str:
    alphabet = string.digits + "OQDILZSBGTX"
    return "".join(rng.choice(alphabet) for _ in range(length))


def _make_negative_case(rng: random.Random, awb_set: Set[str]) -> TestCase:
    templates = [
        "Reference: {token} - not an airway bill",
        "Invoice line item code {token}",
        "Customer id {token}\nNo shipment details",
        "Random OCR fragment: {token}",
        "Tracking candidate {token} (invalid)",
    ]
    for _ in range(200):
        token = _random_noise_token(rng)
        norm = hf._norm_digits_12(token)  # deliberate use for quality testing
        if norm and norm in awb_set:
            continue
        text = rng.choice(templates).format(token=token)
        return TestCase(text=text, expected_awb=None, kind="negative", note="synthetic_noise", raw_token=token)
    return TestCase(text="NO AWB PRESENT IN THIS TEXT", expected_awb=None, kind="negative", note="fallback", raw_token=None)


def _forced_digit_cases(rng: random.Random, awbs: List[str]) -> List[TestCase]:
    """Guarantee at least one test for each configured confusion digit."""
    cases: List[TestCase] = []
    for digit, confs in CONFUSION_BY_DIGIT.items():
        candidates = [a for a in awbs if digit in a]
        if not candidates:
            continue
        awb = rng.choice(candidates)
        idxs = [i for i, ch in enumerate(awb) if ch == digit]
        idx = rng.choice(idxs)
        repl = rng.choice(confs)
        token = list(awb)
        token[idx] = repl
        token_s = "".join(token)
        text = f"AWB NUMBER: {token_s}"
        cases.append(
            TestCase(
                text=text,
                expected_awb=awb,
                kind="positive",
                note=f"forced_{digit}->{repl}@{idx}",
                raw_token=token_s,
            )
        )
    return cases


def _load_case_file(path: Path) -> List[TestCase]:
    """
    CSV format:
      text,expected_awb
    expected_awb empty/blank -> negative case
    """
    out: List[TestCase] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "text" not in reader.fieldnames:
            raise ValueError("case-file must contain a 'text' column")
        for row in reader:
            text = (row.get("text") or "").strip()
            expected = (row.get("expected_awb") or "").strip()
            if not text:
                continue
            if expected and (len(expected) != 12 or not expected.isdigit()):
                raise ValueError(f"Invalid expected_awb in case-file: {expected}")
            out.append(
                TestCase(
                    text=text,
                    expected_awb=expected or None,
                    kind="positive" if expected else "negative",
                    note="case_file",
                    raw_token=None,
                )
            )
    return out


def _predict_awb_full(
    text: str,
    awb_set: Set[str],
    by_prefix: Dict[str, List[str]],
    by_suffix: Dict[str, List[str]],
    include_tolerance: bool,
) -> Tuple[Optional[str], str]:
    high, standard = hf.extract_tiered_candidates(text, awb_set)
    stage_hits = {c: {"TEST_CASE"} for c in (high | standard)}
    res = hf.prioritize_db_match(
        high,
        standard,
        awb_set,
        by_prefix,
        by_suffix,
        include_tolerance=include_tolerance,
        candidate_stage_hits=stage_hits,
    )
    if res.get("status") == "matched":
        return res.get("awb"), res.get("method", "")
    return None, res.get("method", "")


def _normalize_only_candidates_from_text(text: str) -> Set[str]:
    """
    Candidate generation focused on normalization quality, without full tier/context gating.
    """
    out: Set[str] = set()
    s = (text or "").upper()
    if not s:
        return out

    # Broad alnum spans similar to OCR chunks.
    for m in re.finditer(r"(?<![A-Z0-9])([A-Z0-9][A-Z0-9\-\s:/._]{8,40}[A-Z0-9])(?![A-Z0-9])", s):
        d = hf._norm_digits_12(m.group(1))
        if d:
            out.add(d)

    # Exact/grouped numeric forms.
    for m in re.finditer(r"(?<!\d)(\d{12})(?!\d)", s):
        out.add(m.group(1))
    for m in re.finditer(r"(?<!\d)(\d{4}[\s\-]\d{4}[\s\-]\d{4})(?!\d)", s):
        d = re.sub(r"\D", "", m.group(1))
        if len(d) == 12:
            out.add(d)

    return out


def _predict_awb_norm_only(case: TestCase, awb_set: Set[str]) -> Tuple[Optional[str], str]:
    candidates = _normalize_only_candidates_from_text(case.text)
    if case.raw_token:
        d = hf._norm_digits_12(case.raw_token)
        if d:
            candidates.add(d)
    db_candidates = sorted(c for c in candidates if c in awb_set)
    if len(db_candidates) == 1:
        return db_candidates[0], "Norm-Exact"
    if len(db_candidates) > 1:
        return None, "Norm-Tie"
    return None, "Norm-None"


def _sample(values: Set[str], n: int = 8) -> List[str]:
    return sorted(values)[:n]


def _diagnose_full_case(
    case: TestCase,
    awb_set: Set[str],
    by_prefix: Dict[str, List[str]],
    by_suffix: Dict[str, List[str]],
    include_tolerance: bool,
) -> None:
    expected = case.expected_awb
    raw_norm = _normalize_only_candidates_from_text(case.text)
    if case.raw_token:
        d = hf._norm_digits_12(case.raw_token)
        if d:
            raw_norm.add(d)

    high, standard = hf.extract_tiered_candidates(case.text, awb_set)
    db_high = high & awb_set
    db_std = standard & awb_set
    stage_hits = {c: {"DIAG"} for c in (high | standard)}

    tol_high_pool = {
        c for c in high
        if not hf._is_disqualified_candidate(c, for_tolerance=True)
    }
    tol_high = hf.tolerance_match_with_details(
        tol_high_pool, awb_set, by_prefix, by_suffix, max_distance=hf.TOLERANCE_HIGH_MAX_DISTANCE
    ) if include_tolerance else {"status": "disabled"}

    tol_std = {"status": "disabled"}
    if include_tolerance and hf.ALLOW_STANDARD_TOLERANCE:
        tol_std_pool = {
            c for c in standard
            if not hf._is_disqualified_candidate(c, for_tolerance=True)
        }
        tol_std = hf.tolerance_match_with_details(
            tol_std_pool, awb_set, by_prefix, by_suffix, max_distance=hf.TOLERANCE_STANDARD_MAX_DISTANCE
        )

    print("  [DIAG] raw_norm_count=", len(raw_norm), "sample=", _sample(raw_norm))
    print("  [DIAG] tier_high_count=", len(high), "sample=", _sample(high))
    print("  [DIAG] tier_std_count=", len(standard), "sample=", _sample(standard))
    print("  [DIAG] db_exact_high=", sorted(db_high)[:8], "db_exact_std=", sorted(db_std)[:8])
    print("  [DIAG] tol_high=", tol_high)
    if include_tolerance:
        print("  [DIAG] tol_std=", tol_std)
    if expected:
        print(
            "  [DIAG] expected_presence:",
            {
                "in_raw_norm": expected in raw_norm,
                "in_tier_high": expected in high,
                "in_tier_std": expected in standard,
                "in_db_high": expected in db_high,
                "in_db_std": expected in db_std,
            },
        )


def _evaluate(
    cases: Iterable[TestCase],
    mode_name: str,
    predict_fn,
    show_failures: int,
    on_failure: Optional[Callable[[TestCase, Optional[str], str], None]] = None,
) -> int:
    tp = fp = tn = fn = 0
    shown = 0
    total = 0
    positives = 0
    negatives = 0

    for case in cases:
        total += 1
        pred, method = predict_fn(case)
        exp = case.expected_awb

        if exp:
            positives += 1
            if pred == exp:
                tp += 1
            else:
                fn += 1
                if pred:
                    fp += 1  # wrong AWB predicted still counts as false positive classification-wise
                if shown < show_failures:
                    print(
                        f"[MISS] expected={exp} predicted={pred} method={method} "
                        f"kind={case.kind} note={case.note} text={case.text!r}"
                    )
                    shown += 1
                    if on_failure is not None:
                        on_failure(case, pred, method)
        else:
            negatives += 1
            if pred is None:
                tn += 1
            else:
                fp += 1
                if shown < show_failures:
                    print(
                        f"[FP] expected=None predicted={pred} method={method} "
                        f"kind={case.kind} note={case.note} text={case.text!r}"
                    )
                    shown += 1
                    if on_failure is not None:
                        on_failure(case, pred, method)

    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)
    f1 = _safe_ratio(2 * precision * recall, precision + recall)
    fp_rate = _safe_ratio(fp, max(1, negatives))
    fn_rate = _safe_ratio(fn, max(1, positives))

    print(f"\n=== Confusion Runner Summary [{mode_name}] ===")
    print(f"Total cases: {total} (positive={positives}, negative={negatives})")
    print(f"TP={tp}  FP={fp}  TN={tn}  FN={fn}")
    print(f"Precision: {_format_pct(precision)}")
    print(f"Recall:    {_format_pct(recall)}")
    print(f"F1 score:  {_format_pct(f1)}")
    print(f"False-positive rate (negatives): {_format_pct(fp_rate)}")
    print(f"False-negative rate (positives): {_format_pct(fn_rate)}")

    return 0 if fn == 0 and fp == 0 else 1


def _build_synthetic_cases(
    awb_set: Set[str],
    sample_size: int,
    negative_size: int,
    seed: int,
) -> List[TestCase]:
    rng = random.Random(seed)
    awbs = sorted(awb_set)
    if not awbs:
        return []

    n = min(sample_size, len(awbs))
    picked = rng.sample(awbs, n)
    cases: List[TestCase] = []

    # Forced coverage for each confusion digit first.
    cases.extend(_forced_digit_cases(rng, picked))

    # Add random positives.
    for awb in picked:
        cases.append(_make_positive_case(rng, awb))

    # Add negatives.
    for _ in range(negative_size):
        cases.append(_make_negative_case(rng, awb_set))

    rng.shuffle(cases)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OCR confusion tests against AWB DB.")
    parser.add_argument("--sample-size", type=int, default=400, help="Number of AWBs to sample for positive synthetic cases.")
    parser.add_argument("--negative-size", type=int, default=400, help="Number of synthetic negative cases.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible synthetic generation.")
    parser.add_argument(
        "--mode",
        choices=["both", "norm", "full", "diag"],
        default="both",
        help="Evaluation mode: normalization-only, full-pipeline context, both, or full diagnostic.",
    )
    parser.add_argument("--include-tolerance", action="store_true", help="Enable tolerance matching during prediction.")
    parser.add_argument("--show-failures", type=int, default=20, help="Max failure examples to print.")
    parser.add_argument("--diagnose-limit", type=int, default=10, help="Max miss diagnostics to print in diag mode.")
    parser.add_argument("--case-file", type=str, default="", help="Optional CSV with columns: text,expected_awb")
    args = parser.parse_args()

    config.ensure_dirs()
    print(f"Loading AWB DB from: {config.AWB_EXCEL_PATH}")
    awb_set = hf.load_awb_set_from_excel(config.AWB_EXCEL_PATH)
    by_prefix, by_suffix = hf.build_buckets(awb_set)
    print(f"Loaded AWBs: {len(awb_set)}")

    cases: List[TestCase] = []
    if args.case_file:
        case_path = Path(args.case_file)
        if not case_path.exists():
            print(f"ERROR: case-file not found: {case_path}")
            return 2
        loaded = _load_case_file(case_path)
        cases.extend(loaded)
        print(f"Loaded case-file cases: {len(loaded)}")

    synthetic = _build_synthetic_cases(
        awb_set=awb_set,
        sample_size=max(0, args.sample_size),
        negative_size=max(0, args.negative_size),
        seed=args.seed,
    )
    cases.extend(synthetic)
    print(f"Synthetic cases: {len(synthetic)}")
    print(f"Total evaluation cases: {len(cases)}")

    if not cases:
        print("No cases to evaluate.")
        return 0

    rc = 0

    if args.mode in ("both", "norm"):
        rc_norm = _evaluate(
            cases=cases,
            mode_name="normalization-only",
            predict_fn=lambda case: _predict_awb_norm_only(case, awb_set),
            show_failures=max(0, args.show_failures),
        )
        rc = max(rc, rc_norm)

    if args.mode in ("both", "full"):
        rc_full = _evaluate(
            cases=cases,
            mode_name=f"full-pipeline (include_tolerance={args.include_tolerance})",
            predict_fn=lambda case: _predict_awb_full(
                case.text, awb_set, by_prefix, by_suffix, args.include_tolerance
            ),
            show_failures=max(0, args.show_failures),
        )
        rc = max(rc, rc_full)

    if args.mode == "diag":
        diag_limit = max(0, args.diagnose_limit)
        diag_counter = {"n": 0}

        def _on_failure(case: TestCase, pred: Optional[str], method: str) -> None:
            if diag_counter["n"] >= diag_limit:
                return
            diag_counter["n"] += 1
            print(f"  [DIAG] failure_index={diag_counter['n']} predicted={pred} method={method}")
            _diagnose_full_case(
                case=case,
                awb_set=awb_set,
                by_prefix=by_prefix,
                by_suffix=by_suffix,
                include_tolerance=args.include_tolerance,
            )

        rc_diag = _evaluate(
            cases=cases,
            mode_name=f"full-diagnostic (include_tolerance={args.include_tolerance})",
            predict_fn=lambda case: _predict_awb_full(
                case.text, awb_set, by_prefix, by_suffix, args.include_tolerance
            ),
            show_failures=max(0, args.show_failures),
            on_failure=_on_failure,
        )
        rc = max(rc, rc_diag)

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
