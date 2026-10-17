"""MedSAM2 proposal. One axial click becomes a box prompt on a local CT slab.

The checkpoint is bowang-lab MedSAM2 (sam2.1 Hiera-T, 512). It treats the slab
as a short video and propagates a box drawn on the clicked slice. The returned
mask uses the full volume's (z, y, x) index space. If the weights or the model
cannot be loaded, callers get MedSAM2Unavailable and should keep the region-grow
outline instead of pretending this model ran.
"""

from __future__ import annotations

import os
import shutil
import urllib.request
from pathlib import Path

import numpy as np

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("TQDM_DISABLE", "1")

ROOT = Path(__file__).resolve().parents[1]
VENDOR = Path(__file__).resolve().parent / "vendor"
CONFIG_NAME = "configs/sam2.1_hiera_t512.yaml"
CHECKPOINT = ROOT / "checkpoints" / "MedSAM2_latest.pt"
CHECKPOINT_URL = "https://huggingface.co/wanglab/MedSAM2/resolve/main/MedSAM2_latest.pt"
CHECKPOINT_BYTES = 150_000_000

# Lung window used by the viewer (center -600 HU, width 1500).
WINDOW_LOW = -1350.0
WINDOW_HIGH = 150.0
IMAGE_SIZE = 512

_predictor = None
_device: str | None = None
_force_cpu = False
_load_error: str | None = None
_hydra_ready = False
last_info: dict = {}


class MedSAM2Unavailable(RuntimeError):
    """Weights or the model did not produce an outline."""


def propose_mask(
    volume: np.ndarray,
    z: int,
    y: int,
    x: int,
    spacing_mm: tuple[float, float, float],
) -> np.ndarray:
    """Return a boolean volume the same shape as `volume`."""
    _validate_click(volume, z, y, x)
    try:
        predictor, device = _get_predictor()
    except MedSAM2Unavailable:
        raise
    except Exception as exc:
        raise _unavailable(f"could not load the model ({exc})") from exc
    try:
        return _run(predictor, volume, z, y, x, spacing_mm)
    except MedSAM2Unavailable:
        raise
    except Exception as exc:
        if device == "mps":
            _switch_to_cpu()
            try:
                predictor, _ = _get_predictor()
                return _run(predictor, volume, z, y, x, spacing_mm)
            except MedSAM2Unavailable:
                raise
            except Exception as exc2:
                raise _unavailable(f"inference failed on cpu after mps failed ({exc2})") from exc2
        raise _unavailable(f"inference failed ({exc})") from exc


def runtime_info() -> dict:
    return {
        "device": _device,
        "checkpoint": str(CHECKPOINT),
        "loaded": _predictor is not None,
        "last": dict(last_info),
    }


def _unavailable(reason: str) -> MedSAM2Unavailable:
    return MedSAM2Unavailable(
        f"MedSAM2 did not run: {reason}. This outline is the region-grow fallback."
    )


def _validate_click(volume: np.ndarray, z: int, y: int, x: int) -> None:
    depth, rows, cols = volume.shape
    if not (0 <= z < depth and 0 <= y < rows and 0 <= x < cols):
        raise ValueError("That click is outside the scan.")
    if float(volume[z, y, x]) < -700:
        raise ValueError("Click the nodule, not the air around it.")


def _switch_to_cpu() -> None:
    global _predictor, _device, _force_cpu
    _predictor = None
    _device = None
    _force_cpu = True


def _choose_device() -> str:
    if _force_cpu:
        return "cpu"
    import torch

    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _get_predictor():
    global _predictor, _device, _load_error
    if _predictor is not None:
        return _predictor, _device
    if _load_error:
        raise _unavailable(_load_error)
    device = _choose_device()
    try:
        _predictor = _build(device)
        _device = device
    except MedSAM2Unavailable:
        raise
    except Exception as exc:
        if device != "mps":
            _load_error = f"could not load the model ({exc})"
            raise _unavailable(_load_error) from exc
        _switch_to_cpu()
        try:
            _predictor = _build("cpu")
            _device = "cpu"
        except MedSAM2Unavailable:
            raise
        except Exception as exc2:
            _load_error = f"could not load the model ({exc2})"
            raise _unavailable(_load_error) from exc2
    print(f"MedSAM2 loaded on {_device} from {CHECKPOINT}", flush=True)
    return _predictor, _device


def _prepare_hydra() -> None:
    global _hydra_ready
    if _hydra_ready:
        return
    import sam2  # noqa: F401  registers the package and initialises Hydra

    from hydra import initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra

    config_path = VENDOR / CONFIG_NAME
    if not config_path.is_file():
        raise _unavailable(f"the model config is missing ({config_path}).")
    if GlobalHydra.instance().is_initialized():
        GlobalHydra.instance().clear()
    initialize_config_dir(config_dir=str(VENDOR.resolve()), version_base="1.2")
    _hydra_ready = True


def _ensure_checkpoint() -> Path:
    if CHECKPOINT.is_file() and CHECKPOINT.stat().st_size >= CHECKPOINT_BYTES:
        return CHECKPOINT
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT.with_suffix(".partial")
    request = urllib.request.Request(CHECKPOINT_URL, headers={"User-Agent": "base44-medsam2"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        if temporary.stat().st_size < CHECKPOINT_BYTES:
            raise OSError(f"download stopped at {temporary.stat().st_size} bytes")
        temporary.replace(CHECKPOINT)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise _unavailable(f"could not download the checkpoint ({exc})") from exc
    return CHECKPOINT


def _silence_progress() -> None:
    """The video predictor prints a bar on every slice. Keep the server log quiet."""
    import sam2.sam2_video_predictor_npz as predictor_module

    predictor_module.tqdm = lambda iterable, *args, **kwargs: iterable


def _build(device: str):
    _prepare_hydra()
    _silence_progress()
    checkpoint = _ensure_checkpoint()
    from sam2.build_sam import build_sam2_video_predictor_npz

    return build_sam2_video_predictor_npz(
        CONFIG_NAME,
        str(checkpoint),
        device=device,
        apply_postprocessing=False,
        hydra_overrides_extra=[
            "++model.sam_mask_decoder_extra_args.dynamic_multimask_via_stability=true",
            "++model.sam_mask_decoder_extra_args.dynamic_multimask_stability_delta=0.05",
            "++model.sam_mask_decoder_extra_args.dynamic_multimask_stability_thresh=0.98",
            "++model.binarize_mask_from_pts_for_mem_enc=true",
            "++model.fill_hole_area=0",
        ],
    )


def _run(
    predictor,
    volume: np.ndarray,
    z: int,
    y: int,
    x: int,
    spacing_mm: tuple[float, float, float],
) -> np.ndarray:
    import torch

    z_spacing, row_spacing, col_spacing = spacing_mm
    box = _tight_box(volume[z], y, x, row_spacing, col_spacing)
    z0, z1 = _slab_bounds(volume.shape[0], z, box, row_spacing, col_spacing, z_spacing)
    slab = np.ascontiguousarray(volume[z0:z1])
    print(f"MedSAM2 proposing on slices {z0}:{z1} ({_device})", flush=True)
    images = torch.from_numpy(_preprocess(slab))
    local_z = z - z0
    rows, cols = volume.shape[1], volume.shape[2]
    mask = np.zeros(slab.shape, dtype=bool)
    with torch.inference_mode():
        state = predictor.init_state(images, rows, cols)
        _prompt(predictor, state, local_z, box)
        _write_propagation(predictor, state, mask, reverse=False)
        predictor.reset_state(state)
        _prompt(predictor, state, local_z, box)
        _write_propagation(predictor, state, mask, reverse=True)
        predictor.reset_state(state)
    if _device == "mps" and hasattr(torch, "mps"):
        torch.mps.empty_cache()
    chosen = _keep_prompted(mask, local_z, y, x, box)
    if not chosen.any():
        raise _unavailable("the model returned an empty outline")
    full = np.zeros(volume.shape, dtype=bool)
    full[z0:z1] = chosen
    last_info.clear()
    last_info.update(
        {
            "device": _device,
            "checkpoint": str(CHECKPOINT),
            "box": [int(value) for value in box],
            "slab": [int(z0), int(z1)],
            "click": [int(z), int(y), int(x)],
            "voxels": int(chosen.sum()),
        }
    )
    return full


def _prompt(predictor, state, frame_idx: int, box: list[int]) -> None:
    predictor.add_new_points_or_box(
        inference_state=state,
        frame_idx=frame_idx,
        obj_id=1,
        box=np.asarray(box, dtype=np.float32),
    )


def _write_propagation(predictor, state, mask: np.ndarray, reverse: bool) -> None:
    rows, cols = mask.shape[1], mask.shape[2]
    for frame_idx, _obj_ids, logits in predictor.propagate_in_video(state, reverse=reverse):
        binary = _binary_mask(logits)
        if binary.shape != (rows, cols):
            raise RuntimeError(
                f"MedSAM2 mask shape {binary.shape} does not match the slice {(rows, cols)}."
            )
        mask[int(frame_idx)] = binary


def _binary_mask(logits) -> np.ndarray:
    scores = logits.detach().float().cpu().numpy()
    while scores.ndim > 2:
        scores = scores[0]
    return scores > 0.0


def _tight_box(slice_hu: np.ndarray, y: int, x: int, row_spacing: float, col_spacing: float) -> list[int]:
    """Box in (x_min, y_min, x_max, y_max) pixel coordinates around the click.

    MedSAM2's CT checkpoint takes a box. The box is the bounding box of soft
    tissue connected to the click, padded a few millimetres, not the expert contour.
    """
    rows, cols = slice_hu.shape
    seed = float(slice_hu[y, x])
    search_mm = 80.0
    y_radius = max(4, int(round(search_mm / row_spacing)))
    x_radius = max(4, int(round(search_mm / col_spacing)))
    y0, y1 = max(0, y - y_radius), min(rows, y + y_radius + 1)
    x0, x1 = max(0, x - x_radius), min(cols, x + x_radius + 1)
    crop = slice_hu[y0:y1, x0:x1]
    local = (y - y0, x - x0)
    component = _component(crop, local, max(seed - 220.0, -450.0), min(seed + 220.0, 250.0))
    if int(component.sum()) < 30:
        component = _component(crop, local, max(seed - 360.0, -550.0), min(seed + 360.0, 400.0))
    yy, xx = np.nonzero(component)
    if len(yy) < 8:
        return _fixed_box(rows, cols, y, x, row_spacing, col_spacing, 18.0)
    pad_y = max(2, int(round(6.0 / row_spacing)))
    pad_x = max(2, int(round(6.0 / col_spacing)))
    min_half_y = max(4, int(round(8.0 / row_spacing)))
    min_half_x = max(4, int(round(8.0 / col_spacing)))
    top = max(0, int(yy.min()) + y0 - pad_y)
    bottom = min(rows - 1, int(yy.max()) + y0 + pad_y)
    left = max(0, int(xx.min()) + x0 - pad_x)
    right = min(cols - 1, int(xx.max()) + x0 + pad_x)
    top = min(top, max(0, y - min_half_y))
    bottom = max(bottom, min(rows - 1, y + min_half_y))
    left = min(left, max(0, x - min_half_x))
    right = max(right, min(cols - 1, x + min_half_x))
    return [int(left), int(top), int(right), int(bottom)]


def _fixed_box(rows: int, cols: int, y: int, x: int, row_spacing: float, col_spacing: float, half_mm: float) -> list[int]:
    half_y = max(4, int(round(half_mm / row_spacing)))
    half_x = max(4, int(round(half_mm / col_spacing)))
    return [
        max(0, x - half_x),
        max(0, y - half_y),
        min(cols - 1, x + half_x),
        min(rows - 1, y + half_y),
    ]


def _component(crop: np.ndarray, seed: tuple[int, int], low: float, high: float) -> np.ndarray:
    allowed = (crop >= low) & (crop <= high)
    sy, sx = seed
    if not allowed[sy, sx]:
        allowed = allowed.copy()
        allowed[sy, sx] = True
    region = np.zeros(crop.shape, dtype=bool)
    region[sy, sx] = True
    for _ in range(max(crop.shape)):
        grown = region.copy()
        grown[1:] |= region[:-1]
        grown[:-1] |= region[1:]
        grown[:, 1:] |= region[:, :-1]
        grown[:, :-1] |= region[:, 1:]
        grown &= allowed
        if np.array_equal(grown, region):
            break
        region = grown
    return region


def _slab_bounds(
    depth: int,
    z: int,
    box: list[int],
    row_spacing: float,
    col_spacing: float,
    z_spacing: float,
) -> tuple[int, int]:
    """Limit the video to a slab around the click. The tumour is local."""
    x0, y0, x1, y1 = box
    box_mm = max((x1 - x0) * col_spacing, (y1 - y0) * row_spacing)
    half_mm = min(90.0, max(30.0, box_mm * 0.75 + 15.0))
    half = max(4, int(round(half_mm / z_spacing)))
    return max(0, z - half), min(depth, z + half + 1)


def _preprocess(slab: np.ndarray) -> np.ndarray:
    """Window, scale to 8-bit, and ImageNet-normalise RGB frames at 512."""
    from PIL import Image

    scaled = np.clip((slab - WINDOW_LOW) / (WINDOW_HIGH - WINDOW_LOW), 0.0, 1.0)
    frames_u8 = np.round(scaled * 255.0).astype(np.uint8)
    depth = frames_u8.shape[0]
    frames = np.empty((depth, 3, IMAGE_SIZE, IMAGE_SIZE), dtype=np.float32)
    for index in range(depth):
        image = Image.fromarray(frames_u8[index]).convert("RGB").resize((IMAGE_SIZE, IMAGE_SIZE))
        frames[index] = np.asarray(image, dtype=np.float32).transpose(2, 0, 1)
    frames /= 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)[:, None, None]
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)[:, None, None]
    frames -= mean
    frames /= std
    return frames


def _keep_prompted(mask: np.ndarray, z: int, y: int, x: int, box: list[int]) -> np.ndarray:
    """Keep the 3D component that the click or its box actually prompted."""
    if 0 <= y < mask.shape[1] and 0 <= x < mask.shape[2] and mask[z, y, x]:
        seeds = np.zeros(mask.shape, dtype=bool)
        seeds[z, y, x] = True
        return _flood(mask, seeds)
    x0, y0, x1, y1 = box
    overlap = np.zeros(mask.shape, dtype=bool)
    overlap[z, y0 : y1 + 1, x0 : x1 + 1] = mask[z, y0 : y1 + 1, x0 : x1 + 1]
    if overlap.any():
        return _flood(mask, overlap)
    return mask


def _flood(mask: np.ndarray, seeds: np.ndarray) -> np.ndarray:
    region = seeds & mask
    if not region.any():
        return region
    for _ in range(max(mask.shape)):
        grown = region.copy()
        grown[1:] |= region[:-1]
        grown[:-1] |= region[1:]
        grown[:, 1:] |= region[:, :-1]
        grown[:, :-1] |= region[:, 1:]
        grown[:, :, 1:] |= region[:, :, :-1]
        grown[:, :, :-1] |= region[:, :, 1:]
        grown &= mask
        if np.array_equal(grown, region):
            break
        region = grown
    return region
