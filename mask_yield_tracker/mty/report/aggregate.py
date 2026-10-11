"""Yield aggregation for one batch.

Yield = pass / inspected. Sites without a result are "not inspected" and are
left out of yield (coverage is reported next to it). Devices are grouped by
"CD key": the finger CD, or for sites without fingers (long lines, other) the
thinnest drawn width.
"""
from __future__ import annotations

import math
from collections import defaultdict

from ..batch import die_dose_focus, die_key


def cd_key(d: dict) -> tuple[str, float | None]:
    if d["cd_nm"] is not None:
        return f"cd:{d['cd_nm']:g}", d["cd_nm"]
    if d["thinnest_nm"] is not None:
        return f"th:{d['thinnest_nm']:g}", d["thinnest_nm"]
    return "none", None


def cd_label(key: str) -> str:
    if key.startswith("cd:"):
        return f"{key[3:]} nm"
    if key.startswith("th:"):
        return f"{key[3:]} nm (thinnest, no fingers)"
    return "no geometry"


def cd_short(key: str) -> str:
    if key.startswith("cd:"):
        return f"{key[3:]} nm"
    if key.startswith("th:"):
        return f"thinnest {key[3:]} nm"
    return "no geometry"


def _cd_sort(key: str):
    kind = {"cd": 0, "th": 1}.get(key[:2], 2)
    try:
        v = float(key.split(":", 1)[1])
    except (IndexError, ValueError):
        v = 0.0
    return (kind, v)


AREA_BINS = [(10, "< 10 µm²"), (100, "10–100 µm²"), (1000, "100–1000 µm²"), (float("inf"), "≥ 1000 µm²")]
ANCHOR_ORDER = ["island", "one_end", "both_ends", "none"]
ANCHOR_LABEL = {"island": "has an island", "one_end": "held at one end", "both_ends": "held at both ends",
                "none": "no thin resist"}


def resist_width_key(d: dict) -> str:
    w = d.get("resist_min_width_nm")
    return "none" if w is None else f"rw:{round(w, -1):g}"


def resist_width_label(key: str) -> str:
    return "no thin resist" if key == "none" else f"{key[3:]} nm"


def resist_area_key(d: dict) -> str:
    a = d.get("resist_min_area_um2")
    if a is None:
        return "none"
    for i, (hi, _) in enumerate(AREA_BINS):
        if a < hi:
            return f"ra:{i}"
    return f"ra:{len(AREA_BINS) - 1}"


def resist_area_label(key: str) -> str:
    return "no thin resist" if key == "none" else AREA_BINS[int(key[3:])][1]


def resist_anchor_key(d: dict) -> str:
    a = d.get("resist_anchoring") or {}
    for k in ("island", "one_end", "both_ends"):
        if a.get(k):
            return k
    return "none"


def wilson(passed: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """95 % Wilson score interval for a pass fraction."""
    if n == 0:
        return None, None
    p = passed / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - half), min(1.0, c + half)


class Tally:
    __slots__ = ("sites", "modes")

    def __init__(self) -> None:
        self.sites = 0
        self.modes: dict[str, int] = defaultdict(int)

    def row(self, mode_ids: list[str], pass_ids: set[str], resist_ids: set[str] = frozenset()) -> dict:
        inspected = sum(self.modes.values())
        passed = sum(v for k, v in self.modes.items() if k in pass_ids)
        rfail = sum(v for k, v in self.modes.items() if k in resist_ids)
        lo, hi = wilson(passed, inspected)
        return {
            "resist_fail": rfail, "resist_fail_rate": rfail / inspected if inspected else None,
            "sites": self.sites, "inspected": inspected, "pass": passed,
            "yield": passed / inspected if inspected else None,
            "yield_lo": lo, "yield_hi": hi,
            "coverage": inspected / self.sites if self.sites else None,
            "modes": {m: self.modes.get(m, 0) for m in mode_ids},
        }


def aggregate(batch: dict, table: dict, modes: list[dict]) -> dict:
    mode_ids = [m["id"] for m in modes]
    pass_ids = {m["id"] for m in modes if m.get("pass")}
    resist_ids = {m["id"] for m in modes if m.get("resist")}
    devices = table["devices"]
    rkeys = {d["id"]: (resist_width_key(d), resist_area_key(d), resist_anchor_key(d)) for d in devices}
    by_resist: dict[tuple, Tally] = defaultdict(Tally)       # (dimension, bin, dose, focus)
    by_resist_all: dict[tuple, Tally] = defaultdict(Tally)   # (dimension, bin)
    by_cd_rw: dict[tuple, Tally] = defaultdict(Tally)        # (cd, resist width bin)
    dev_key = {d["id"]: cd_key(d)[0] for d in devices}
    dev_pitch = {d["id"]: d.get("pitch_nm") for d in devices}
    known = set(dev_key)

    dies = [tuple(d) for d in batch["wafer"]["dies"]]
    die_rows = []
    by_cd_cond: dict[tuple, Tally] = defaultdict(Tally)       # (cd, dose, focus)
    by_cdpitch_cond: dict[tuple, Tally] = defaultdict(Tally)  # (cd, pitch, dose, focus)
    by_cd: dict[str, Tally] = defaultdict(Tally)
    by_cond: dict[tuple, Tally] = defaultdict(Tally)          # (dose, focus), all devices
    total = Tally()
    unknown_results = 0

    for col, row in dies:
        dose, focus = die_dose_focus(batch, col, row)
        cond = (dose, focus)
        res = batch["results"].get(die_key(col, row), {})
        unknown_results += sum(1 for i in res if i not in known)
        t_die = Tally()
        for d in devices:
            k, p = dev_key[d["id"]], dev_pitch[d["id"]]
            st = res.get(d["id"], {}).get("status")
            rw, ra, rn = rkeys[d["id"]]
            for t in (by_cd_cond[(k, dose, focus)], by_cdpitch_cond[(k, p, dose, focus)], by_cd[k],
                      by_cond[cond], total, t_die,
                      by_resist[("width", rw, dose, focus)], by_resist[("area", ra, dose, focus)],
                      by_resist[("anchor", rn, dose, focus)], by_resist_all[("width", rw)],
                      by_resist_all[("area", ra)], by_resist_all[("anchor", rn)], by_cd_rw[(k, rw)]):
                t.sites += 1
                if st in mode_ids:
                    t.modes[st] += 1
        die_rows.append({"die": die_key(col, row), "col": col, "row": row, "dose": dose, "focus": focus,
                         **t_die.row(mode_ids, pass_ids, resist_ids)})

    def cond_sort(c):
        return (c[0] is None, c[0] if c[0] is not None else 0, c[1] is None, c[1] if c[1] is not None else 0)

    conds = sorted(by_cond, key=cond_sort)
    cds = sorted(by_cd, key=_cd_sort)
    focus_values = sorted({f for _, f in conds if f is not None})
    def rw_sort(k):
        return (k == "none", float(k[3:]) if k != "none" else 0.0)

    def ra_sort(k):
        return (k == "none", int(k[3:]) if k != "none" else 0)

    order = {"width": rw_sort, "area": ra_sort, "anchor": lambda k: ANCHOR_ORDER.index(k)}
    label = {"width": resist_width_label, "area": resist_area_label, "anchor": ANCHOR_LABEL.get}
    resist = {}
    for dim in ("width", "area", "anchor"):
        bins = sorted({b for (dm, b) in by_resist_all if dm == dim}, key=order[dim])
        resist[dim] = {
            "bins": [{"key": b, "label": label[dim](b), "n_devices": sum(1 for v in rkeys.values()
                                                                         if v[("width", "area", "anchor").index(dim)] == b),
                      **by_resist_all[(dim, b)].row(mode_ids, pass_ids, resist_ids)} for b in bins],
            "cond": [{"key": b, "dose": dz, "focus": fc, **t.row(mode_ids, pass_ids, resist_ids)}
                     for (_, b, dz, fc), t in sorted(((k, t) for k, t in by_resist.items() if k[0] == dim),
                                                     key=lambda kv: (order[dim](kv[0][1]), cond_sort(kv[0][2:])))],
        }
    resist["cd_width"] = [{"cd": k, "rw": rw, **t.row(mode_ids, pass_ids, resist_ids)}
                          for (k, rw), t in sorted(by_cd_rw.items(), key=lambda kv: (_cd_sort(kv[0][0]), rw_sort(kv[0][1])))]

    return {
        "mode_ids": mode_ids,
        "resist_mode_ids": sorted(resist_ids),
        "resist": resist,
        "conditions": [{"dose": c[0], "focus": c[1], **by_cond[c].row(mode_ids, pass_ids, resist_ids)} for c in conds],
        "focus_varies": len(focus_values) > 1,
        "cds": [{"key": k, "label": cd_label(k), "short": cd_short(k), "n_devices": sum(1 for v in dev_key.values() if v == k),
                 **by_cd[k].row(mode_ids, pass_ids, resist_ids)} for k in cds],
        "cd_cond": [{"key": k, "dose": dz, "focus": fc, **t.row(mode_ids, pass_ids, resist_ids)}
                    for (k, dz, fc), t in sorted(by_cd_cond.items(), key=lambda kv: (_cd_sort(kv[0][0]), cond_sort(kv[0][1:])))],
        "cdpitch_cond": [{"key": k, "pitch_nm": p, "dose": dz, "focus": fc, **t.row(mode_ids, pass_ids)}
                         for (k, p, dz, fc), t in sorted(by_cdpitch_cond.items(),
                                                         key=lambda kv: (_cd_sort(kv[0][0]), kv[0][1] or 0, cond_sort(kv[0][2:])))],
        "dies": die_rows,
        "total": total.row(mode_ids, pass_ids, resist_ids),
        "unknown_results": unknown_results,
    }


def result_rows(batch: dict, table: dict) -> list[dict]:
    """One row per recorded result, joined with device data (for CSV export)."""
    dev = {d["id"]: d for d in table["devices"]}
    out = []
    for key, res in sorted(batch["results"].items()):
        col, row = (int(v) for v in key.split(","))
        dose, focus = die_dose_focus(batch, col, row)
        for did, r in sorted(res.items()):
            d = dev.get(did, {})
            out.append({"die": key, "col": col, "row": row, "dose": dose, "focus": focus, "device_id": did,
                        "name": d.get("name"), "group": d.get("group"), "kind": d.get("kind"),
                        "cd_nm": d.get("cd_nm"), "pitch_nm": d.get("pitch_nm"), "thinnest_nm": d.get("thinnest_nm"),
                        "resist_min_width_nm": d.get("resist_min_width_nm"), "resist_min_area_um2": d.get("resist_min_area_um2"),
                        "resist_max_length_um": d.get("resist_max_length_um"),
                        "status": r.get("status"), "ts": r.get("ts")})
    return out
