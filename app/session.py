"""Review session for one open visit at a time. Marks persist in data/cache/session.json."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pydicom

from app.geometry import rle_decode
from app.index_cases import CACHE, ROOT, build_inbox, case_by_id, load_expert
from app.measure import export_csv, export_visits, measurements, masks_to_rle
from app.medsam2_proposer import MedSAM2Unavailable
from app.medsam2_proposer import propose_mask as medsam2_propose
from app.segment import paint, propose_mask

COLORS = ["#0f6e6e", "#9a3b24", "#2f5d9f", "#8a6a12", "#5c4a7a", "#3d6b4f"]
MATCH_VALUES = {"", "Confident", "Uncertain", "Not found"}
SESSION_PATH = CACHE / "session.json"


class ReviewSession:
    def __init__(self, index: dict, session_path: Path | None = None):
        self.session_path = session_path or SESSION_PATH
        self.index = index
        self.case: dict | None = None
        self.expert: dict = {"polygons": {}, "rle": {}}
        self.nodules: list[dict] = []
        self.started = 0.0
        self._volume: np.ndarray | None = None
        self._volume_case = ""
        self._next_id = 1
        self.saved: dict[str, dict] = {}
        self.timepoint_overrides: dict[str, str] = {}
        self._load()

    def public_cases(self) -> list[dict]:
        self._stash()
        listed = []
        for case in self.index["cases"]:
            saved = self.saved.get(case["id"])
            nodules = saved["nodules"] if saved else []
            listed.append(
                {
                    "id": case["id"],
                    "patient_id": case["patient_id"],
                    "timepoint": case["timepoint"],
                    "scan_date": case["scan_date"],
                    "series_number": case["series_number"],
                    "slice_thickness": case["slice_thickness"],
                    "n_slices": case["n_slices"],
                    "first_visit": case["first_visit"],
                    "source": case.get("source", "practice"),
                    "has_expert": bool(case.get("has_expert")),
                    "timepoint_locked": bool(case.get("timepoint_locked", True)),
                    "exportable": any(nodule["status"] in {"accepted", "edited"} for nodule in nodules),
                }
            )
        return listed

    def open_case(self, case_id: str) -> dict:
        case = case_by_id(self.index, case_id)
        if self.case is not None and self.case["id"] == case_id:
            return self.state()
        self._stash()
        self.case = case
        saved = self.saved.get(case_id)
        self.expert = load_expert(case)
        if saved:
            self.nodules = saved["nodules"]
            self.started = saved["started"]
            self._next_id = saved["next_id"]
        else:
            self.nodules = []
            self.started = time.time()
            self._next_id = 1
        self._volume = None
        self._volume_case = ""
        return self._done()

    def propose(self, case_id: str, slice_index: int, x: float, y: float) -> dict:
        self._require(case_id)
        volume = self._hu()
        spacing = (
            float(self.case["spacing_z"]),
            float(self.case["pixel_spacing"][0]),
            float(self.case["pixel_spacing"][1]),
        )
        z = int(slice_index)
        yy = int(round(y))
        xx = int(round(x))
        warning = None
        try:
            mask = medsam2_propose(volume, z, yy, xx, spacing)
        except MedSAM2Unavailable as exc:
            warning = str(exc)
            mask = propose_mask(volume, z, yy, xx, spacing)
        nodule = self._new_nodule()
        nodule["slices"] = _sparse(mask)
        self.nodules.append(nodule)
        payload = self._done()
        if warning:
            payload["warning"] = warning
        return payload

    def paint_points(self, case_id: str, nodule_id: int, slice_index: int, points: list[dict], radius: float, mode: str) -> dict:
        nodule = self._nodule(case_id, nodule_id)
        rows, cols = self.case["rows"], self.case["cols"]
        mask = nodule["slices"].get(slice_index)
        if mask is None:
            mask = np.zeros((rows, cols), dtype=bool)
        else:
            mask = mask.copy()
        for point in points:
            paint(mask, float(point["x"]), float(point["y"]), float(radius), mode != "erase")
        if mask.any():
            nodule["slices"][slice_index] = mask
        else:
            nodule["slices"].pop(slice_index, None)
        nodule["status"] = "edited"
        nodule["axis_override"] = None
        return self._done()

    def set_status(self, case_id: str, nodule_id: int, status: str) -> dict:
        nodule = self._nodule(case_id, nodule_id)
        if status not in {"accepted", "edited", "failed", "unverified"}:
            raise ValueError("Unknown mark status.")
        if status == "accepted" and nodule["status"] == "edited":
            status = "edited"
        nodule["status"] = status
        return self._done()

    def set_axes(self, case_id: str, nodule_id: int, slice_index: int, long_axis: list, short_axis: list) -> dict:
        nodule = self._nodule(case_id, nodule_id)
        nodule["axis_override"] = {
            "slice_index": int(slice_index),
            "long": long_axis,
            "short": short_axis,
        }
        nodule["status"] = "edited"
        return self._done()

    def set_timepoint(self, case_id: str, timepoint: str) -> dict:
        case = case_by_id(self.index, case_id)
        if case.get("timepoint_locked"):
            raise ValueError("This visit's timepoint comes from the practice contour.")
        label = " ".join(str(timepoint).split())
        if not label:
            raise ValueError("Timepoint cannot be empty.")
        if len(label) > 40:
            raise ValueError("Timepoint is too long.")
        case["timepoint"] = label
        self.timepoint_overrides[case_id] = label
        if self.case is not None and self.case["id"] == case_id:
            return self._done()
        self._persist()
        return {"ok": True}

    def rescan_inbox(self) -> list[dict]:
        self._stash()
        research = build_inbox(self.timepoint_overrides)
        practice = [case for case in self.index["cases"] if case.get("source") != "research"]
        self.index["cases"] = practice + research
        if self.case is not None and self.case.get("source") == "research":
            try:
                self.case = case_by_id(self.index, self.case["id"])
            except KeyError:
                pass
        self._persist()
        return self.public_cases()

    def update_fields(self, case_id: str, nodule_id: int, fields: dict) -> dict:
        nodule = self._nodule(case_id, nodule_id)
        for key in ("lobe", "type", "solid_component", "measured_by", "notes", "match_to_prior"):
            if key not in fields:
                continue
            value = "" if fields[key] is None else str(fields[key])
            if key == "match_to_prior":
                if self.case["first_visit"]:
                    value = ""
                elif value not in MATCH_VALUES:
                    raise ValueError("Match to prior must be Confident, Uncertain, or Not found.")
            nodule[key] = value
        return self._done()

    def delete(self, case_id: str, nodule_id: int) -> dict:
        self._require(case_id)
        self.nodules = [nodule for nodule in self.nodules if nodule["id"] != nodule_id]
        return self._done()

    def reset_visit(self, case_id: str) -> dict:
        """Clear nodules, calipers, statuses, and the timer on the open visit."""
        self._require(case_id)
        self.nodules = []
        self._next_id = 1
        self.started = time.time()
        return self._done()

    def state(self) -> dict:
        case = self.case
        assert case is not None
        nodules = []
        for nodule in self.nodules:
            measured = measurements(nodule, case, self.expert.get("rle", {}))
            nodules.append(
                {
                    "id": nodule["id"],
                    "status": nodule["status"],
                    "color": nodule["color"],
                    "rle": masks_to_rle(nodule["slices"]),
                    "measurements": {
                        "long_mm": measured["long_mm"],
                        "short_mm": measured["short_mm"],
                        "mean_mm": measured["mean_mm"],
                        "volume_mm3": measured["volume_mm3"],
                        "slice_index": measured["slice_index"],
                        "instance_number": measured["instance_number"],
                        "dice": measured["dice"],
                    },
                    "axes": None
                    if measured["axes"] is None
                    else {
                        "slice_index": measured["slice_index"],
                        "long": measured["axes"]["long"],
                        "short": measured["axes"]["short"],
                    },
                    "lobe": nodule["lobe"],
                    "type": nodule["type"],
                    "solid_component": nodule["solid_component"],
                    "measured_by": nodule["measured_by"],
                    "notes": nodule["notes"],
                    "match_to_prior": nodule["match_to_prior"],
                }
            )
        return {
            "case": {
                "id": case["id"],
                "patient_id": case["patient_id"],
                "timepoint": case["timepoint"],
                "scan_date": case["scan_date"],
                "series_number": case["series_number"],
                "slice_thickness": case["slice_thickness"],
                "n_slices": case["n_slices"],
                "first_visit": case["first_visit"],
                "source": case.get("source", "practice"),
                "has_expert": bool(case.get("has_expert")),
                "timepoint_locked": bool(case.get("timepoint_locked", True)),
                "rows": case["rows"],
                "cols": case["cols"],
            },
            "elapsed_seconds": max(0.0, time.time() - self.started),
            "nodules": nodules,
            "expert_polygons": self.expert.get("polygons", {}),
        }

    def csv_text(self, case_id: str) -> str:
        self._require(case_id)
        return export_csv(self.case, self.nodules, self.expert.get("rle", {}), time.time() - self.started)

    def csv_all(self) -> str:
        """Every reviewed visit, in patient order with test before retest."""
        return self.csv_selected([case["id"] for case in self.index["cases"]])

    def csv_selected(self, case_ids: list[str]) -> str:
        """Checked visits only, in patient order with test before retest.

        A checked visit with no accepted or edited nodule contributes no rows.
        Minutes stay on the first exported row of each visit.
        """
        self._stash()
        wanted = {str(case_id) for case_id in case_ids if str(case_id).strip()}
        visits = []
        for case in self.index["cases"]:
            if case["id"] not in wanted:
                continue
            saved = self.saved.get(case["id"])
            nodules = saved["nodules"] if saved else []
            expert_rle = load_expert(case).get("rle", {}) if saved else {}
            elapsed = time.time() - saved["started"] if saved else 0.0
            visits.append((case, nodules, expert_rle, elapsed))
        return export_visits(visits)

    def slice_hu(self, case_id: str, slice_index: int) -> np.ndarray:
        self._require(case_id)
        volume = self._hu()
        if not 0 <= slice_index < volume.shape[0]:
            raise IndexError("Slice is outside this scan.")
        return volume[slice_index]

    def _hu(self) -> np.ndarray:
        assert self.case is not None
        if self._volume is not None and self._volume_case == self.case["id"]:
            return self._volume
        case = self.case
        volume = np.empty((case["n_slices"], case["rows"], case["cols"]), dtype=np.float32)
        slope = float(case["rescale_slope"])
        intercept = float(case["rescale_intercept"])
        for index, item in enumerate(case["slices"]):
            dataset = pydicom.dcmread(ROOT / item["file"])
            volume[index] = dataset.pixel_array.astype(np.float32) * slope + intercept
        self._volume = volume
        self._volume_case = case["id"]
        return volume

    def _new_nodule(self) -> dict:
        nodule_id = self._next_id
        self._next_id += 1
        return {
            "id": nodule_id,
            "status": "unverified",
            "color": COLORS[(nodule_id - 1) % len(COLORS)],
            "slices": {},
            "axis_override": None,
            "lobe": "",
            "type": "",
            "solid_component": "",
            "measured_by": "",
            "notes": "",
            "match_to_prior": "",
        }

    def _done(self) -> dict:
        self._persist()
        return self.state()

    def _stash(self) -> None:
        if self.case is None:
            return
        self.saved[self.case["id"]] = {
            "nodules": self.nodules,
            "started": self.started,
            "next_id": self._next_id,
        }

    def _persist(self) -> None:
        self._stash()
        visits = {}
        for case_id, saved in self.saved.items():
            try:
                case = case_by_id(self.index, case_id)
            except KeyError:
                continue
            nodules = []
            for nodule in saved["nodules"]:
                item = {key: value for key, value in nodule.items() if key != "slices"}
                item["slices"] = masks_to_rle(nodule["slices"])
                nodules.append(item)
            visits[case_id] = {
                "nodules": nodules,
                "started": saved["started"],
                "next_id": saved["next_id"],
            }
        CACHE.mkdir(parents=True, exist_ok=True)
        payload = {"visits": visits, "timepoints": self.timepoint_overrides}
        temporary = self.session_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload))
        temporary.replace(self.session_path)

    def _load(self) -> None:
        if not self.session_path.is_file():
            return
        try:
            payload = json.loads(self.session_path.read_text())
        except json.JSONDecodeError:
            print(f"ignored unreadable session file {self.session_path}", flush=True)
            return
        self.timepoint_overrides = {
            str(case_id): str(label)
            for case_id, label in (payload.get("timepoints") or {}).items()
            if str(label).strip()
        }
        for case in self.index["cases"]:
            label = self.timepoint_overrides.get(case["id"])
            if label and not case.get("timepoint_locked"):
                case["timepoint"] = label
        for case_id, saved in (payload.get("visits") or {}).items():
            try:
                case = case_by_id(self.index, case_id)
            except KeyError:
                continue
            nodules = [_nodule_from_json(item, case["rows"], case["cols"]) for item in saved.get("nodules", [])]
            self.saved[case_id] = {
                "nodules": nodules,
                "started": float(saved.get("started", time.time())),
                "next_id": int(saved.get("next_id", 1)),
            }

    def _require(self, case_id: str) -> None:
        if self.case is None or self.case["id"] != case_id:
            raise KeyError("Open the visit before editing it.")

    def _nodule(self, case_id: str, nodule_id: int) -> dict:
        self._require(case_id)
        for nodule in self.nodules:
            if nodule["id"] == nodule_id:
                return nodule
        raise KeyError("That nodule is not on this visit.")


def _nodule_from_json(item: dict, rows: int, cols: int) -> dict:
    nodule = {key: value for key, value in item.items() if key != "slices"}
    slices = {}
    for key, runs in (item.get("slices") or {}).items():
        if runs:
            slices[int(key)] = rle_decode(runs, rows, cols)
    nodule["slices"] = slices
    nodule.setdefault("axis_override", None)
    return nodule


def _sparse(mask: np.ndarray) -> dict[int, np.ndarray]:
    slices = {}
    for index in range(mask.shape[0]):
        if mask[index].any():
            slices[index] = mask[index]
    return slices
