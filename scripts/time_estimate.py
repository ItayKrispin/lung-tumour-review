#!/usr/bin/env python3
"""Write Itay's correction-time scenario for every practice visit.

The minutes are not a stopwatch and they are not Daniel's. Proposal seconds
and overlap come from the unedited batch. Finding time is excluded because the
expert outline was on screen.
"""

from __future__ import annotations

import csv
import shutil
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CACHE_CSV = ROOT / "data" / "cache" / "eval_proposals.csv"
BATCH_CSV = ROOT / "demo" / "eval_proposals.csv"
OUT_CSV = ROOT / "demo" / "itay_time_estimate.csv"

COLUMNS = [
    "Patient ID",
    "Timepoint",
    "Proposal seconds",
    "Overlap",
    "Correction minutes",
    "Sheet minutes",
    "Estimated minutes",
    "Finding minutes excluded",
    "Daniel outline minutes",
    "Basis",
]

FINDING = "10-20"
DANIEL_OUTLINE = "15-25"
SHEET_MINUTES = 2
BASIS = (
    "Scenario, not a stopwatch. Itay stood in for Daniel with the expert outline visible. "
    "Finding time is excluded."
)
NO_OUTLINE = BASIS + " No proposal, so the outline step stays at the bottom of the hand-outline range."


def correction_minutes(dice: str, error: str) -> tuple[int, str]:
    if error or not dice:
        return 15, NO_OUTLINE
    overlap = float(dice)
    if overlap >= 0.7:
        return 4, BASIS
    if overlap >= 0.4:
        return 8, BASIS
    return 15, BASIS


def load_rows() -> list[dict]:
    source = CACHE_CSV if CACHE_CSV.is_file() else BATCH_CSV
    if not source.is_file():
        raise SystemExit(f"No batch scores at {CACHE_CSV} or {BATCH_CSV}")
    if source == CACHE_CSV:
        BATCH_CSV.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CACHE_CSV, BATCH_CSV)
    with source.open(newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    rows = load_rows()
    written = []
    for row in rows:
        correction, basis = correction_minutes(row.get("dice", ""), row.get("error", ""))
        dice = row.get("dice", "")
        written.append(
            [
                row["patient_id"],
                row["timepoint"],
                row.get("seconds", ""),
                "" if not dice else f"{float(dice):.3f}",
                correction,
                SHEET_MINUTES,
                correction + SHEET_MINUTES,
                FINDING,
                DANIEL_OUTLINE,
                basis,
            ]
        )
    with OUT_CSV.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows(written)
    totals = [int(item[6]) for item in written]
    corrections = [int(item[4]) for item in written]
    bands = {4: 0, 8: 0, 15: 0}
    for minutes in corrections:
        bands[minutes] += 1
    print(f"visits {len(written)}")
    print(f"correction bands 4={bands[4]} 8={bands[8]} 15={bands[15]}")
    print(f"median correction {statistics.median(corrections):g} min")
    print(f"median estimated {statistics.median(totals):g} min")
    print(f"wrote {OUT_CSV}")


if __name__ == "__main__":
    main()
