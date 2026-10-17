"""Exercise accept, edit, and fail on one practice visit and check the sheet."""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.geometry import rle_decode
from app.index_cases import ensure_index
from app.measure import COLUMNS, measurements
from app.session import ReviewSession


def main() -> None:
    index = ensure_index()
    case = next(item for item in index["cases"] if item["timepoint"] == "test" and item["first_visit"])
    session = ReviewSession(index, Path(tempfile.mkdtemp()) / "session.json")
    session.open_case(case["id"])
    expert = session.expert["rle"]
    if not expert:
        raise SystemExit(f"{case['id']} has no expert outline")
    slice_index = max(expert, key=lambda key: sum(run[1] for run in expert[key]))
    mask = rle_decode(expert[slice_index], case["rows"], case["cols"])
    rows, cols = np.nonzero(mask)
    y = float(np.median(rows))
    x = float(np.median(cols))
    slice_index = int(slice_index)

    session.propose(case["id"], slice_index, x, y)
    session.set_status(case["id"], 1, "accepted")
    session.propose(case["id"], slice_index, x + 8, y)
    session.paint_points(case["id"], 2, slice_index, [{"x": x, "y": y}], 6, "erase")
    session.propose(case["id"], slice_index, x, y + 8)
    session.set_status(case["id"], 3, "failed")
    session.started = time.time() - 32 * 60

    statuses = {nodule["id"]: nodule["status"] for nodule in session.nodules}
    if statuses != {1: "accepted", 2: "edited", 3: "failed"}:
        raise SystemExit(f"unexpected statuses {statuses}")

    text = session.csv_text(case["id"])
    rows_out = list(csv.reader(io.StringIO(text)))
    if rows_out[0] != COLUMNS:
        raise SystemExit(f"header mismatch: {rows_out[0]}")
    if len(rows_out) != 3:
        raise SystemExit(f"expected 2 nodule rows, got {len(rows_out) - 1}: {rows_out[1:]}")
    first, second = rows_out[1], rows_out[2]
    if first[15] != "32" or second[15] != "":
        raise SystemExit(f"minutes should be on the first row only: {first[15]!r} {second[15]!r}")
    if first[14] or second[14]:
        raise SystemExit(f"first visit match should be empty: {first[14]!r} {second[14]!r}")
    long_mm, short_mm, mean_mm = float(first[9]), float(first[10]), float(first[11])
    if abs(mean_mm - (long_mm + short_mm) / 2) > 0.05:
        raise SystemExit(f"mean diameter {mean_mm} is not the average of {long_mm} and {short_mm}")
    if not first[13] or not second[13]:
        raise SystemExit(f"volume missing: {first[13]!r} {second[13]!r}")
    if "3" in {first[5], second[5]}:
        raise SystemExit("failed nodule was exported")
    measured = measurements(session.nodules[0], case, session.expert["rle"])
    if measured["dice"] is None or measured["dice"] <= 0:
        raise SystemExit(f"proposal missed the expert outline (dice={measured['dice']})")
    print(
        case["id"],
        "rows",
        len(rows_out) - 1,
        "long",
        long_mm,
        "short",
        short_mm,
        "mean",
        mean_mm,
        "volume",
        first[13],
        "dice",
        round(measured["dice"], 3),
    )
    print("ok")


if __name__ == "__main__":
    main()
