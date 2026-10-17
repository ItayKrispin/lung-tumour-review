"""Diameters, volume, overlap, and Daniel's spreadsheet."""

from __future__ import annotations

import csv
import io
from math import hypot

import numpy as np

from app.geometry import rle_decode, rle_encode

COLUMNS = [
    "Patient ID",
    "Scan date",
    "Timepoint",
    "Series #",
    "Slice thickness (mm)",
    "Nodule ID",
    "Lobe",
    "Slice #",
    "Type",
    "Long axis (mm)",
    "Short axis (mm)",
    "Mean diameter (mm)",
    "Solid component (mm)",
    "Volume (mm3)",
    "Match to prior",
    "Minutes on scan",
    "Measured by",
    "Notes",
]


def voxel_count(slice_masks: dict[int, np.ndarray]) -> int:
    return int(sum(int(mask.sum()) for mask in slice_masks.values()))


def volume_mm3(slice_masks: dict[int, np.ndarray], row_spacing: float, col_spacing: float, z_spacing: float) -> float:
    return voxel_count(slice_masks) * row_spacing * col_spacing * z_spacing


def dice(tool_masks: dict[int, np.ndarray], expert_rle: dict[str, list], rows: int, cols: int) -> float | None:
    if not expert_rle:
        return None
    intersection = 0
    tool_total = 0
    expert_total = 0
    keys = set(tool_masks) | {int(key) for key in expert_rle}
    for index in keys:
        tool = tool_masks.get(index)
        expert_runs = expert_rle.get(str(index), [])
        expert = rle_decode(expert_runs, rows, cols) if expert_runs else None
        if tool is None and expert is None:
            continue
        if tool is None:
            expert_total += int(expert.sum())
            continue
        if expert is None:
            tool_total += int(tool.sum())
            continue
        tool_total += int(tool.sum())
        expert_total += int(expert.sum())
        intersection += int(np.logical_and(tool, expert).sum())
    if tool_total + expert_total == 0:
        return None
    return 2.0 * intersection / (tool_total + expert_total)


def _convex_hull(points: np.ndarray) -> np.ndarray:
    ordered = points[np.lexsort((points[:, 1], points[:, 0]))]
    if len(ordered) <= 2:
        return ordered

    def cross(origin, a, b) -> float:
        return float((a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0]))

    lower: list[np.ndarray] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[np.ndarray] = []
    for point in ordered[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = np.vstack(lower[:-1] + upper[:-1])
    return hull


def _axis_pair(mask: np.ndarray, col_spacing: float, row_spacing: float) -> dict | None:
    rows, cols = np.nonzero(mask)
    if len(cols) < 2:
        return None
    points = np.stack([cols.astype(np.float64) * col_spacing, rows.astype(np.float64) * row_spacing], axis=1)
    if len(points) > 4000:
        points = points[:: max(1, len(points) // 4000)]
    hull = _convex_hull(points)
    best = 0.0
    pair = (0, 1 if len(hull) > 1 else 0)
    for i in range(len(hull)):
        delta = hull[i + 1 :] - hull[i]
        dist = np.hypot(delta[:, 0], delta[:, 1])
        if dist.size == 0:
            continue
        local = int(np.argmax(dist))
        if dist[local] > best:
            best = float(dist[local])
            pair = (i, i + 1 + local)
    if best <= 0:
        return None
    start = hull[pair[0]]
    end = hull[pair[1]]
    direction = (end - start) / best
    perpendicular = np.array([-direction[1], direction[0]])
    projected = (hull - start) @ perpendicular
    short = float(projected.max() - projected.min())
    center = points.mean(axis=0)
    half = perpendicular * (short / 2.0)
    short_start = center - half
    short_end = center + half
    return {
        "long_mm": best,
        "short_mm": short,
        "long": [_to_px(start, col_spacing, row_spacing), _to_px(end, col_spacing, row_spacing)],
        "short": [_to_px(short_start, col_spacing, row_spacing), _to_px(short_end, col_spacing, row_spacing)],
    }


def _to_px(point_mm: np.ndarray, col_spacing: float, row_spacing: float) -> list[float]:
    return [float(point_mm[0] / col_spacing), float(point_mm[1] / row_spacing)]


def _segment_mm(endpoints: list, col_spacing: float, row_spacing: float) -> float:
    (x1, y1), (x2, y2) = endpoints
    return hypot((x2 - x1) * col_spacing, (y2 - y1) * row_spacing)


def measurements(nodule: dict, case: dict, expert_rle: dict) -> dict:
    masks: dict[int, np.ndarray] = nodule["slices"]
    row_spacing = float(case["pixel_spacing"][0])
    col_spacing = float(case["pixel_spacing"][1])
    z_spacing = float(case["spacing_z"])
    volume = volume_mm3(masks, row_spacing, col_spacing, z_spacing)
    override = nodule.get("axis_override")
    if override:
        long_mm = _segment_mm(override["long"], col_spacing, row_spacing)
        short_mm = _segment_mm(override["short"], col_spacing, row_spacing)
        slice_index = int(override["slice_index"])
        axes = {"long": override["long"], "short": override["short"]}
    else:
        best = None
        best_index = None
        for index, mask in masks.items():
            measured = _axis_pair(mask, col_spacing, row_spacing)
            if measured and (best is None or measured["long_mm"] > best["long_mm"]):
                best = measured
                best_index = index
        if best is None:
            return {
                "long_mm": None,
                "short_mm": None,
                "mean_mm": None,
                "volume_mm3": volume,
                "slice_index": None,
                "instance_number": None,
                "dice": dice(masks, expert_rle, case["rows"], case["cols"]),
                "axes": None,
            }
        long_mm = best["long_mm"]
        short_mm = best["short_mm"]
        slice_index = best_index
        axes = {"long": best["long"], "short": best["short"]}
    instance = None
    if slice_index is not None:
        instance = int(case["slices"][slice_index]["instance"]) or (slice_index + 1)
    return {
        "long_mm": long_mm,
        "short_mm": short_mm,
        "mean_mm": (long_mm + short_mm) / 2.0,
        "volume_mm3": volume,
        "slice_index": slice_index,
        "instance_number": instance,
        "dice": dice(masks, expert_rle, case["rows"], case["cols"]),
        "axes": axes,
    }


def masks_to_rle(slice_masks: dict[int, np.ndarray]) -> dict[str, list]:
    encoded = {}
    for index, mask in sorted(slice_masks.items()):
        runs = rle_encode(mask)
        if runs:
            encoded[str(index)] = runs
    return encoded


def format_axes(long_mm: float | None, short_mm: float | None) -> tuple[str, str, str]:
    if long_mm is None or short_mm is None:
        return "", "", ""
    long_rounded = round(long_mm, 1)
    short_rounded = round(short_mm, 1)
    mean = (long_rounded + short_rounded) / 2.0
    return f"{long_rounded:.1f}", f"{short_rounded:.1f}", f"{mean:.1f}"


def format_volume(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(round(value)))


def format_thickness(value: float) -> str:
    return f"{value:g}"


def minutes_on_scan(elapsed_seconds: float) -> int:
    return max(1, int(round(elapsed_seconds / 60.0)))


def visit_rows(case: dict, nodules: list[dict], expert_rle: dict, elapsed_seconds: float) -> list[list]:
    """One row per accepted or edited nodule. Minutes sit on the first row of this visit only."""
    kept = [nodule for nodule in nodules if nodule["status"] in {"accepted", "edited"}]
    kept.sort(key=lambda nodule: nodule["id"])
    if not kept:
        return []
    minutes = minutes_on_scan(elapsed_seconds)
    rows = []
    for row_index, nodule in enumerate(kept):
        measured = measurements(nodule, case, expert_rle)
        match = "" if case["first_visit"] else nodule.get("match_to_prior", "")
        long_mm, short_mm, mean_mm = format_axes(measured["long_mm"], measured["short_mm"])
        rows.append(
            [
                case["patient_id"],
                case["scan_date"],
                case["timepoint"],
                case["series_number"],
                format_thickness(float(case["slice_thickness"])),
                nodule["id"],
                nodule.get("lobe", ""),
                "" if measured["instance_number"] is None else measured["instance_number"],
                nodule.get("type", ""),
                long_mm,
                short_mm,
                mean_mm,
                nodule.get("solid_component", ""),
                format_volume(measured["volume_mm3"]) if measured["long_mm"] is not None else "",
                match,
                minutes if row_index == 0 else "",
                nodule.get("measured_by", ""),
                nodule.get("notes", ""),
            ]
        )
    return rows


def export_csv(case: dict, nodules: list[dict], expert_rle: dict, elapsed_seconds: float) -> str:
    return _write_csv(visit_rows(case, nodules, expert_rle, elapsed_seconds))


def export_visits(visits: list[tuple[dict, list[dict], dict, float]]) -> str:
    """Several visits, test before retest, in one sheet. Each visit keeps its own minutes cell."""
    rows: list[list] = []
    for case, nodules, expert_rle, elapsed_seconds in visits:
        rows.extend(visit_rows(case, nodules, expert_rle, elapsed_seconds))
    return _write_csv(rows)


def _write_csv(rows: list[list]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue()
