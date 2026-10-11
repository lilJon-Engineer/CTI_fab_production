"""Batch files (schema mty.batch v1): one wafer inspected at one process step.

{
  "schema": "mty.batch", "version": 1, "id": ..., "created": ..., "modified": ...,
  "reticle": {"id", "name", "gate_sha256", "device_table_created", "n_devices"},
  "meta": {"lot", "wafer", "process_step", "date", "operator", ...},   # see config/batch_meta.json
  "wafer": {"diameter_mm", "edge_exclusion_mm", "step_x_mm", "step_y_mm",
            "n_cols", "n_rows", "offset_x_mm", "offset_y_mm", "dies": [[col, row], ...]},
  "splits": {"dose_unit", "focus_unit",
             "columns": [{"dose", "focus"}] * n_cols,     # column = dose step
             "rows":    [{"focus"}] * n_rows},            # optional focus per row
  "results": {"col,row": {device_id: {"status", "ts"}}},
  "die_notes": {"col,row": "text"},
  "watchlist": [device_id, ...]                          # inspection list, in the user's order
}
Die (0, 0) is the top-left column/row of the grid. Focus for a die is the row
value if set, else the column value.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .workspace import new_id, now, slug

BATCH_SCHEMA = "mty.batch"
BATCH_VERSION = 1
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class BatchError(ValueError):
    pass


def load_failure_modes() -> list[dict]:
    return json.loads((CONFIG_DIR / "failure_modes.json").read_text(encoding="utf-8"))["modes"]


def load_meta_fields() -> list[dict]:
    return json.loads((CONFIG_DIR / "batch_meta.json").read_text(encoding="utf-8"))["fields"]


def die_key(col: int, row: int) -> str:
    return f"{int(col)},{int(row)}"


def die_center_mm(wafer: dict, col: int, row: int) -> tuple[float, float]:
    x = (col - (wafer["n_cols"] - 1) / 2) * wafer["step_x_mm"] + wafer.get("offset_x_mm", 0)
    y = ((wafer["n_rows"] - 1) / 2 - row) * wafer["step_y_mm"] + wafer.get("offset_y_mm", 0)
    return x, y


def dies_on_wafer(wafer: dict, field_w_mm: float | None = None, field_h_mm: float | None = None) -> list[list[int]]:
    """Grid positions whose die (step size, or field size if given) lies fully inside the usable wafer."""
    r = wafer["diameter_mm"] / 2 - wafer.get("edge_exclusion_mm", 0)
    hw = (field_w_mm or wafer["step_x_mm"]) / 2
    hh = (field_h_mm or wafer["step_y_mm"]) / 2
    out = []
    for row in range(wafer["n_rows"]):
        for col in range(wafer["n_cols"]):
            x, y = die_center_mm(wafer, col, row)
            if all(math.hypot(x + sx * hw, y + sy * hh) <= r for sx in (-1, 1) for sy in (-1, 1)):
                out.append([col, row])
    return out


def default_wafer() -> dict:
    w = {"diameter_mm": 100.0, "edge_exclusion_mm": 3.0, "step_x_mm": 10.0, "step_y_mm": 10.0,
         "n_cols": 9, "n_rows": 9, "offset_x_mm": 0.0, "offset_y_mm": 0.0}
    w["dies"] = dies_on_wafer(w)
    return w


def new_batch(reticle_meta: dict, device_table: dict, meta: dict | None = None, wafer: dict | None = None,
              splits: dict | None = None) -> dict:
    wafer = wafer or default_wafer()
    b = {
        "schema": BATCH_SCHEMA, "version": BATCH_VERSION,
        "id": new_id("-".join(slug(str((meta or {}).get(k, ""))) for k in ("lot", "wafer", "process_step")) or "batch"),
        "created": now(), "modified": now(),
        "reticle": {
            "id": reticle_meta["id"], "name": reticle_meta.get("name"),
            "gate_sha256": device_table["source"]["gate_sha256"],
            "device_table_created": device_table.get("created"),
            "n_devices": len(device_table["devices"]),
        },
        "meta": dict(meta or {}),
        "wafer": wafer,
        "splits": splits or {},
        "results": {},
        "die_notes": {},
    }
    normalize(b)
    return b


def normalize(b: dict) -> dict:
    """Fill defaults and size the split tables to the grid (keeps existing values)."""
    w = b["wafer"]
    for k in ("n_cols", "n_rows"):
        w[k] = int(w[k])
        if not 1 <= w[k] <= 200:
            raise BatchError(f"{k} must be 1..200")
    for k in ("diameter_mm", "step_x_mm", "step_y_mm"):
        w[k] = float(w[k])
        if w[k] <= 0:
            raise BatchError(f"{k} must be > 0")
    w["dies"] = sorted({(int(c), int(r)) for c, r in w.get("dies", []) if 0 <= int(c) < w["n_cols"] and 0 <= int(r) < w["n_rows"]})
    w["dies"] = [list(d) for d in w["dies"]]
    s = b.setdefault("splits", {})
    s.setdefault("dose_unit", "mJ/cm2")
    s.setdefault("focus_unit", "um")
    cols = list(s.get("columns", []))[: w["n_cols"]]
    cols += [{"dose": None, "focus": None} for _ in range(w["n_cols"] - len(cols))]
    rows = list(s.get("rows", []))[: w["n_rows"]]
    rows += [{"focus": None} for _ in range(w["n_rows"] - len(rows))]
    s["columns"], s["rows"] = cols, rows
    b.setdefault("results", {})
    b.setdefault("die_notes", {})
    b["die_notes"] = {k: str(v) for k, v in b["die_notes"].items() if str(v).strip()}
    seen = set()
    b["watchlist"] = [i for i in (str(x) for x in b.get("watchlist", [])) if not (i in seen or seen.add(i))]
    b.setdefault("meta", {})
    return b


def migrate(b: dict) -> dict:
    """Bring an older batch document up to the current version."""
    if b.get("schema") != BATCH_SCHEMA:
        raise BatchError("not a batch file (schema != mty.batch)")
    v = b.get("version")
    if v == BATCH_VERSION:
        return normalize(b)
    raise BatchError(f"unsupported batch version {v}")


def die_dose_focus(b: dict, col: int, row: int) -> tuple[float | None, float | None]:
    s = b["splits"]
    c = s["columns"][col] if col < len(s["columns"]) else {}
    r = s["rows"][row] if row < len(s["rows"]) else {}
    focus = r.get("focus") if r.get("focus") is not None else c.get("focus")
    return c.get("dose"), focus


def set_result(b: dict, col: int, row: int, device_id: str, status: str | None,
               statuses: set[str], device_ids: set[str]) -> None:
    if [col, row] not in b["wafer"]["dies"]:
        raise BatchError(f"die {col},{row} is not in this batch")
    if device_id not in device_ids:
        raise BatchError(f"unknown device {device_id!r}")
    key = die_key(col, row)
    if status is None:
        b["results"].get(key, {}).pop(device_id, None)
        if not b["results"].get(key):
            b["results"].pop(key, None)
        return
    if status not in statuses:
        raise BatchError(f"unknown status {status!r}")
    b["results"].setdefault(key, {})[device_id] = {"status": status, "ts": now()}
