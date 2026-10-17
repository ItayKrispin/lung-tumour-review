#!/usr/bin/env python3
"""Score an unedited MedSAM2 outline on every practice visit.

The click is the center of the expert tumor. Rows are not written into
Daniel's sheet. Results resume from data/cache/eval_proposals.csv.
"""

from __future__ import annotations

import argparse
import csv
import gc
import sys
import time
from pathlib import Path

import numpy as np
import pydicom

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.geometry import rle_decode
from app.index_cases import ROOT as APP_ROOT
from app.index_cases import ensure_index, load_expert
from app.measure import dice, measurements
from app.medsam2_proposer import propose_mask

CSV_PATH = APP_ROOT / "data" / "cache" / "eval_proposals.csv"
REPORT_PATH = APP_ROOT / "demo" / "out_of_box_results.md"

FIELDS = [
    "case_id",
    "patient_id",
    "timepoint",
    "click_slice",
    "click_x",
    "click_y",
    "seconds",
    "includes_model_load",
    "dice",
    "tool_volume_mm3",
    "expert_volume_mm3",
    "volume_error_mm3",
    "volume_error_pct",
    "tool_mean_mm",
    "expert_mean_mm",
    "diameter_error_mm",
    "extra_slices",
    "error",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Score at most this many new visits")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    if args.summary_only:
        write_report(read_rows())
        return
    index = ensure_index()
    done = {row["case_id"] for row in read_rows()}
    first_in_file = not done
    scored = 0
    for case in index["cases"]:
        if case["id"] in done:
            continue
        if args.limit and scored >= args.limit:
            break
        row = score_case(case, includes_model_load=first_in_file and scored == 0)
        append_row(row)
        scored += 1
        status = row["error"] or f"dice={row['dice']} extra={row['extra_slices']} {row['seconds']}s"
        print(f"[{scored}] {case['id']} {status}", flush=True)
    write_report(read_rows())
    print(f"wrote {REPORT_PATH}", flush=True)


def score_case(case: dict, includes_model_load: bool) -> dict:
    row = {key: "" for key in FIELDS}
    row.update(
        {
            "case_id": case["id"],
            "patient_id": case["patient_id"],
            "timepoint": case["timepoint"],
            "includes_model_load": "1" if includes_model_load else "0",
        }
    )
    expert = load_expert(case).get("rle", {})
    expert_slices = slices_from_rle(expert, case["rows"], case["cols"])
    if not expert_slices:
        row["error"] = "no expert outline"
        return row
    click_slice, click_y, click_x = click_center(expert_slices)
    row["click_slice"] = click_slice
    row["click_x"] = click_x
    row["click_y"] = click_y
    volume = None
    try:
        volume = load_volume(case)
        started = time.perf_counter()
        mask = propose_mask(
            volume,
            click_slice,
            click_y,
            click_x,
            (
                float(case["spacing_z"]),
                float(case["pixel_spacing"][0]),
                float(case["pixel_spacing"][1]),
            ),
        )
        row["seconds"] = f"{time.perf_counter() - started:.2f}"
        tool_slices = sparse_mask(mask)
        fill_metrics(row, case, tool_slices, expert_slices, expert)
    except Exception as exc:  # noqa: BLE001 - one visit must not stop the batch
        row["error"] = str(exc).replace("\n", " ")
    finally:
        del volume
        gc.collect()
    return row


def fill_metrics(row: dict, case: dict, tool_slices: dict, expert_slices: dict, expert_rle: dict) -> None:
    tool = measure_mask(tool_slices, case, expert_rle)
    expert = measure_mask(expert_slices, case, expert_rle)
    overlap = dice(tool_slices, expert_rle, case["rows"], case["cols"])
    row["dice"] = fmt(overlap)
    row["tool_volume_mm3"] = fmt(tool["volume_mm3"], 1)
    row["expert_volume_mm3"] = fmt(expert["volume_mm3"], 1)
    row["tool_mean_mm"] = fmt(tool["mean_mm"])
    row["expert_mean_mm"] = fmt(expert["mean_mm"])
    if tool["volume_mm3"] is not None and expert["volume_mm3"]:
        error = tool["volume_mm3"] - expert["volume_mm3"]
        row["volume_error_mm3"] = fmt(error, 1)
        row["volume_error_pct"] = fmt(100.0 * error / expert["volume_mm3"])
    if tool["mean_mm"] is not None and expert["mean_mm"] is not None:
        row["diameter_error_mm"] = fmt(tool["mean_mm"] - expert["mean_mm"])
    extra = set(tool_slices) - set(expert_slices)
    row["extra_slices"] = len(extra)


def measure_mask(slices: dict, case: dict, expert_rle: dict) -> dict:
    return measurements({"slices": slices, "axis_override": None}, case, expert_rle)


def click_center(slices: dict[int, np.ndarray]) -> tuple[int, int, int]:
    slice_index = max(slices, key=lambda index: int(slices[index].sum()))
    rows, cols = np.nonzero(slices[slice_index])
    center_row = float(rows.mean())
    center_col = float(cols.mean())
    nearest = int(np.argmin((rows - center_row) ** 2 + (cols - center_col) ** 2))
    return slice_index, int(rows[nearest]), int(cols[nearest])


def slices_from_rle(expert_rle: dict, rows: int, cols: int) -> dict[int, np.ndarray]:
    slices = {}
    for key, runs in expert_rle.items():
        if not runs:
            continue
        mask = rle_decode(runs, rows, cols)
        if mask.any():
            slices[int(key)] = mask
    return slices


def sparse_mask(mask: np.ndarray) -> dict[int, np.ndarray]:
    slices = {}
    for index in range(mask.shape[0]):
        if mask[index].any():
            slices[index] = mask[index]
    return slices


def load_volume(case: dict) -> np.ndarray:
    volume = np.empty((case["n_slices"], case["rows"], case["cols"]), dtype=np.float32)
    slope = float(case["rescale_slope"])
    intercept = float(case["rescale_intercept"])
    for index, item in enumerate(case["slices"]):
        dataset = pydicom.dcmread(APP_ROOT / item["file"])
        volume[index] = dataset.pixel_array.astype(np.float32) * slope + intercept
    return volume


def read_rows() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(newline="") as handle:
        return list(csv.DictReader(handle))


def append_row(row: dict) -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()


def write_report(rows: list[dict]) -> None:
    ok = [row for row in rows if not row.get("error") and row.get("dice")]
    failed = [row for row in rows if row.get("error")]
    lines = [
        "# Out-of-box proposal results",
        "",
        "Each number below is an unedited MedSAM2 outline. The click is the center of the expert tumor, which simulates Daniel having already found it. The outline was not painted and was not accepted. These rows are not in his sheet.",
        "",
        f"Scored visits: {len(ok)}. Failed visits: {len(failed)}.",
        "",
        "## Agreement",
        "",
        f"- Median overlap with the expert outline: {pct(median(num(row, 'dice') for row in ok))}.",
        f"- Median absolute volume error: {plain(median(abs(num(row, 'volume_error_mm3')) for row in ok), 0)} mm³ ({plain(median(abs(num(row, 'volume_error_pct')) for row in ok), 1)}%).",
        f"- Median absolute mean-diameter error: {plain(median(abs(num(row, 'diameter_error_mm')) for row in ok), 1)} mm.",
        "",
        "## Same-day wobble",
        "",
        wobble_paragraph(ok),
        "",
        "## Time",
        "",
        timing_paragraph(rows),
        "",
        "## Failure gallery",
        "",
        "The worst five visits by overlap. A low overlap, a large volume error, or many extra slices means Daniel would still have to paint.",
        "",
        *gallery_lines(ok),
        "",
        "## The pair already shown to the lab",
        "",
        "On RIDER-1129164940 the click was made by hand in the viewer, not at the measured center used above. The test outline overlapped the expert by 64% (volume 135,253 mm³, mean diameter 84.2 mm). The retest overlapped by 60% (volume 130,610 mm³, mean diameter 94.2 mm). The volume barely changed. The mean diameter moved by about 10 mm. Those scans were taken the same day, so that gap is measurement wobble. Outlining that tumor by hand takes Daniel 15–25 minutes. These batch numbers measure agreement. They are not his minutes.",
        "",
        "## What this does not show",
        "",
        "The click is given. The report does not show that the tool can find every nodule. A proposal that continues onto slices the expert left empty is counted in extra slices.",
        "",
    ]
    if failed:
        lines.extend(["Visits that did not produce an outline:", ""])
        for row in failed:
            lines.append(f"- {row['patient_id']} {row['timepoint']}: {row['error']}")
        lines.append("")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines))


def wobble_paragraph(rows: list[dict]) -> str:
    paired = pairs(rows)
    if not paired:
        return "No patient had both a test and a retest result."
    volume_change = []
    diameter_change = []
    for test, retest in paired:
        volume_change.append(abs(num(test, "tool_volume_mm3") - num(retest, "tool_volume_mm3")))
        diameter_change.append(abs(num(test, "tool_mean_mm") - num(retest, "tool_mean_mm")))
    return (
        f"Across {len(paired)} patients with both scans, the median absolute change in the tool's volume "
        f"was {plain(median(volume_change), 0)} mm³, and the median absolute change in mean diameter was "
        f"{plain(median(diameter_change), 1)} mm. The tumor has not changed between these scans, so that change is wobble."
    )


def timing_paragraph(rows: list[dict]) -> str:
    timed = [row for row in rows if row.get("seconds")]
    if not timed:
        return "No click was timed."
    first = next((row for row in timed if row.get("includes_model_load") == "1"), timed[0])
    later = [num(row, "seconds") for row in timed if row is not first and row.get("seconds")]
    later_text = f"{plain(median(later), 1)} seconds" if later else "not measured"
    return (
        f"The first click took {plain(num(first, 'seconds'), 1)} seconds, including the model load. "
        f"The median of the later clicks was {later_text}."
    )


def gallery_lines(rows: list[dict]) -> list[str]:
    ranked = sorted(rows, key=lambda row: num(row, "dice"))
    if not ranked:
        return ["No successful visit to list."]
    lines = []
    for row in ranked[:5]:
        lines.append(
            f"- {row['patient_id']} {row['timepoint']}: overlap {pct(num(row, 'dice'))}, "
            f"volume error {plain(num(row, 'volume_error_mm3'), 0)} mm³ "
            f"({plain(num(row, 'volume_error_pct'), 1)}%), "
            f"mean-diameter error {plain(num(row, 'diameter_error_mm'), 1)} mm, "
            f"{row['extra_slices']} extra slices."
        )
    return lines


def pairs(rows: list[dict]) -> list[tuple[dict, dict]]:
    grouped: dict[str, dict[str, dict]] = {}
    for row in rows:
        grouped.setdefault(row["patient_id"], {})[row["timepoint"]] = row
    found = []
    for visits in grouped.values():
        if "test" in visits and "retest" in visits:
            found.append((visits["test"], visits["retest"]))
    return found


def num(row: dict, key: str) -> float:
    value = row.get(key, "")
    if value in ("", None):
        return float("nan")
    return float(value)


def median(values) -> float:
    clean = [value for value in values if value == value]
    if not clean:
        return float("nan")
    clean.sort()
    mid = len(clean) // 2
    if len(clean) % 2:
        return clean[mid]
    return (clean[mid - 1] + clean[mid]) / 2


def fmt(value: float | None, digits: int = 4) -> str:
    if value is None or value != value:
        return ""
    return f"{value:.{digits}f}"


def plain(value: float, digits: int) -> str:
    if value != value:
        return "n/a"
    return f"{value:,.{digits}f}"


def pct(value: float) -> str:
    if value != value:
        return "n/a"
    return f"{100 * value:.0f}%"


if __name__ == "__main__":
    main()
