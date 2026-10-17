"""Index the practice CTs and rasterize the manual gross-tumour contours."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pydicom

from app.geometry import fill_polygons, patient_xy_to_pixel, rle_encode, slice_spacing_mm

ROOT = Path(__file__).resolve().parents[1]
CT_ROOT = ROOT / "data" / "ct"
SEG_ROOT = ROOT / "data" / "seg"
INBOX = ROOT / "data" / "inbox"
CACHE = ROOT / "data" / "cache"
CASES_PATH = CACHE / "cases.json"

SLICE_TAGS = [
    "SOPInstanceUID",
    "ImagePositionPatient",
    "InstanceNumber",
    "PixelSpacing",
    "ImageOrientationPatient",
    "Rows",
    "Columns",
    "SliceThickness",
    "SeriesNumber",
    "StudyDate",
    "PatientID",
    "RescaleSlope",
    "RescaleIntercept",
]


def ensure_index() -> dict:
    if CASES_PATH.exists():
        cached = json.loads(CASES_PATH.read_text())
        if cached.get("cases"):
            _mark_practice(cached["cases"])
            return cached
    if not CT_ROOT.is_dir() or not SEG_ROOT.is_dir():
        return {"cases": [], "skipped_struct_patients": []}
    payload = build_index()
    _mark_practice(payload["cases"])
    return payload


def load_catalog() -> dict:
    """Practice visits plus any DICOM series currently in data/inbox."""
    catalog = ensure_index()
    catalog["cases"] = list(catalog["cases"]) + build_inbox()
    return catalog


def _mark_practice(cases: list[dict]) -> None:
    for case in cases:
        case["source"] = "practice"
        case["has_expert"] = bool(case.get("expert_file"))
        case["timepoint_locked"] = True


def build_index() -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    series = [_index_series(path) for path in sorted(p for p in CT_ROOT.iterdir() if p.is_dir())]
    by_uid = {item["series_uid"]: item for item in series}
    cases = []
    skipped = []
    for path in sorted(p for p in SEG_ROOT.iterdir() if p.is_dir()):
        sample = next(path.glob("*.dcm"), None)
        if sample is None:
            continue
        dataset = pydicom.dcmread(sample, stop_before_pixels=True, specific_tags=["Modality"])
        if str(getattr(dataset, "Modality", "")) != "RTSTRUCT":
            continue
        dataset = pydicom.dcmread(sample, stop_before_pixels=True)
        found = _manual_cases(dataset, by_uid)
        if found:
            cases.extend(found)
        elif not any(name.endswith("_man") for _, name in _roi_names(dataset)):
            skipped.append(str(getattr(dataset, "PatientID", path.name)))

    grouped: dict[str, set[str]] = {}
    for case in cases:
        grouped.setdefault(case["patient_id"], set()).add(case["timepoint"])
    for case in cases:
        timepoints = grouped[case["patient_id"]]
        case["first_visit"] = case["timepoint"] == "test" if "test" in timepoints else True

    cases.sort(key=lambda item: (item["patient_id"], 0 if item["timepoint"] == "test" else 1))
    payload = {"cases": cases, "skipped_struct_patients": skipped}
    CASES_PATH.write_text(json.dumps(payload))
    print(f"indexed {len(cases)} visits from {len(series)} CT series")
    return payload


def _index_series(series_dir: Path) -> dict:
    slices = []
    for file_path in series_dir.glob("*.dcm"):
        dataset = pydicom.dcmread(file_path, stop_before_pixels=True, specific_tags=SLICE_TAGS)
        origin = [float(value) for value in dataset.ImagePositionPatient]
        slices.append(
            {
                "file": str(file_path.relative_to(ROOT)),
                "sop": str(dataset.SOPInstanceUID),
                "z": origin[2],
                "instance": int(getattr(dataset, "InstanceNumber", 0) or 0),
                "origin": origin,
            }
        )
    slices.sort(key=lambda item: item["z"])
    print(f"ct {series_dir.name[-12:]} {len(slices)} slices", flush=True)
    first = pydicom.dcmread(ROOT / slices[0]["file"], stop_before_pixels=True, specific_tags=SLICE_TAGS)
    spacing = [float(value) for value in first.PixelSpacing]
    orientation = [float(value) for value in first.ImageOrientationPatient]
    date = str(getattr(first, "StudyDate", "") or "")
    if len(date) == 8:
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    series_number = getattr(first, "SeriesNumber", "")
    series_number = "" if series_number in (None, "") else str(int(series_number))
    return {
        "series_uid": series_dir.name,
        "patient_id": str(getattr(first, "PatientID", "")),
        "scan_date": date,
        "series_number": series_number,
        "slice_thickness": float(first.SliceThickness),
        "rows": int(first.Rows),
        "cols": int(first.Columns),
        "pixel_spacing": spacing,
        "orientation": orientation,
        "spacing_z": slice_spacing_mm([item["z"] for item in slices]),
        "rescale_slope": float(getattr(first, "RescaleSlope", 1) or 1),
        "rescale_intercept": float(getattr(first, "RescaleIntercept", 0) or 0),
        "slices": slices,
    }


def _roi_names(dataset) -> list[tuple[int, str]]:
    return [(int(roi.ROINumber), str(roi.ROIName)) for roi in dataset.StructureSetROISequence]


def _manual_cases(dataset, by_uid: dict[str, dict]) -> list[dict]:
    names = dict(_roi_names(dataset))
    referenced = _referenced_series_uid(dataset)
    series = by_uid.get(referenced)
    if series is None:
        print(f"no CT for referenced series {referenced}")
        return []
    sop_to_index = {item["sop"]: index for index, item in enumerate(series["slices"])}
    cases = []
    for contour in dataset.ROIContourSequence:
        name = names.get(int(contour.ReferencedROINumber), "")
        if name == "GTVp_test_man":
            timepoint = "test"
        elif name == "GTVp_retest_man":
            timepoint = "retest"
        else:
            continue
        polygons, rle = _rasterize(contour, series, sop_to_index)
        case_id = f"{series['patient_id']}__{timepoint}"
        expert_path = CACHE / "expert" / f"{case_id}.json"
        expert_path.parent.mkdir(parents=True, exist_ok=True)
        expert_path.write_text(json.dumps({"polygons": polygons, "rle": rle}))
        cases.append(
            {
                "id": case_id,
                "patient_id": series["patient_id"],
                "timepoint": timepoint,
                "roi_name": name,
                "series_uid": series["series_uid"],
                "scan_date": series["scan_date"],
                "series_number": series["series_number"],
                "slice_thickness": series["slice_thickness"],
                "rows": series["rows"],
                "cols": series["cols"],
                "pixel_spacing": series["pixel_spacing"],
                "orientation": series["orientation"],
                "spacing_z": series["spacing_z"],
                "rescale_slope": series["rescale_slope"],
                "rescale_intercept": series["rescale_intercept"],
                "n_slices": len(series["slices"]),
                "slices": series["slices"],
                "expert_file": str(expert_path.relative_to(ROOT)),
                "source": "practice",
                "has_expert": True,
                "timepoint_locked": True,
            }
        )
    return cases


def _referenced_series_uid(dataset) -> str:
    frame = dataset.ReferencedFrameOfReferenceSequence[0]
    study = frame.RTReferencedStudySequence[0]
    series = study.RTReferencedSeriesSequence[0]
    return str(series.SeriesInstanceUID)


def _rasterize(contour_sequence, series: dict, sop_to_index: dict[str, int]) -> tuple[dict, dict]:
    grouped: dict[int, list[list[tuple[float, float]]]] = {}
    for contour in contour_sequence.ContourSequence:
        data = [float(value) for value in contour.ContourData]
        points = list(zip(data[0::3], data[1::3], data[2::3]))
        if len(points) < 3:
            continue
        slice_index = _slice_index(contour, points, series, sop_to_index)
        if slice_index is None:
            continue
        origin = series["slices"][slice_index]["origin"]
        pixel_points = []
        for x, y, z in points:
            column, row = patient_xy_to_pixel(x, y, z, origin, series["pixel_spacing"], series["orientation"])
            pixel_points.append((column, row))
        grouped.setdefault(slice_index, []).append(pixel_points)

    polygons = {
        str(index): [[[x, y] for x, y in polygon] for polygon in items]
        for index, items in grouped.items()
    }
    rle = {}
    for index, items in grouped.items():
        mask = fill_polygons(items, series["rows"], series["cols"])
        encoded = rle_encode(mask)
        if encoded:
            rle[str(index)] = encoded
    return polygons, rle


def _slice_index(contour, points, series: dict, sop_to_index: dict[str, int]) -> int | None:
    sequence = getattr(contour, "ContourImageSequence", None)
    if sequence:
        sop = str(sequence[0].ReferencedSOPInstanceUID)
        if sop in sop_to_index:
            return sop_to_index[sop]
    z = float(np.median([point[2] for point in points]))
    zs = [item["z"] for item in series["slices"]]
    nearest = int(np.argmin(np.abs(np.asarray(zs) - z)))
    if abs(zs[nearest] - z) > max(series["spacing_z"] * 0.6, 0.8):
        return None
    return nearest


def load_expert(case: dict) -> dict:
    relative = case.get("expert_file") or ""
    if not relative:
        return {"polygons": {}, "rle": {}}
    path = ROOT / relative
    if not path.is_file():
        return {"polygons": {}, "rle": {}}
    return json.loads(path.read_text())


def build_inbox(overrides: dict[str, str] | None = None) -> list[dict]:
    """Index DICOM series dropped in data/inbox. These visits have no expert contour."""
    overrides = overrides or {}
    INBOX.mkdir(parents=True, exist_ok=True)
    cases = []
    for path in _inbox_series_dirs():
        try:
            series = _index_series(path)
        except Exception as exc:
            print(f"skip inbox {path.name}: {exc}", flush=True)
            continue
        if not series["slices"]:
            continue
        cases.append(
            {
                "id": f"research__{path.name}",
                "source": "research",
                "has_expert": False,
                "timepoint_locked": False,
                "patient_id": series["patient_id"],
                "timepoint": "",
                "roi_name": "",
                "series_uid": series["series_uid"],
                "scan_date": series["scan_date"],
                "series_number": series["series_number"],
                "slice_thickness": series["slice_thickness"],
                "rows": series["rows"],
                "cols": series["cols"],
                "pixel_spacing": series["pixel_spacing"],
                "orientation": series["orientation"],
                "spacing_z": series["spacing_z"],
                "rescale_slope": series["rescale_slope"],
                "rescale_intercept": series["rescale_intercept"],
                "n_slices": len(series["slices"]),
                "slices": series["slices"],
                "expert_file": "",
                "first_visit": True,
            }
        )
    _assign_research_timepoints(cases, overrides)
    cases.sort(key=lambda case: (case["patient_id"], case["scan_date"], case["series_number"], case["id"]))
    print(f"inbox {len(cases)} series", flush=True)
    return cases


def _inbox_series_dirs() -> list[Path]:
    if not INBOX.is_dir():
        return []
    found = []
    if any(INBOX.glob("*.dcm")):
        found.append(INBOX)
    for path in sorted(p for p in INBOX.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if any(path.glob("*.dcm")):
            found.append(path)
            continue
        for child in sorted(p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if any(child.glob("*.dcm")):
                found.append(child)
    return found


def _assign_research_timepoints(cases: list[dict], overrides: dict[str, str]) -> None:
    grouped: dict[str, list[dict]] = {}
    for case in cases:
        grouped.setdefault(case["patient_id"], []).append(case)
    for items in grouped.values():
        items.sort(key=lambda case: (case["scan_date"], case["series_number"], case["id"]))
        for index, case in enumerate(items):
            case["first_visit"] = index == 0
            if index == 0:
                default = "baseline"
            elif index == 1:
                default = "follow-up"
            else:
                default = f"follow-up {index}"
            case["timepoint"] = overrides.get(case["id"]) or default


def case_by_id(index: dict, case_id: str) -> dict:
    for case in index["cases"]:
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)
