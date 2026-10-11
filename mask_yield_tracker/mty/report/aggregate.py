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

    def row(self, mode_ids: list[str], pass_ids: set[str]) -> dict:
        inspected = sum(self.modes.values())
        passed = sum(v for k, v in self.modes.items() if k in pass_ids)
        lo, hi = wilson(passed, inspected)
        return {
            "sites": self.sites, "inspected": inspected, "pass": passed,
            "yield": passed / inspected if inspected else None,
            "yield_lo": lo, "yield_hi": hi,
            "coverage": inspected / self.sites if self.sites else None,
            "modes": {m: self.modes.get(m, 0) for m in mode_ids},
        }


def aggregate(batch: dict, table: dict, modes: list[dict]) -> dict:
    mode_ids = [m["id"] for m in modes]
    pass_ids = {m["id"] for m in modes if m.get("pass")}
    devices = table["devices"]
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
            for t in (by_cd_cond[(k, dose, focus)], by_cdpitch_cond[(k, p, dose, focus)], by_cd[k],
                      by_cond[cond], total, t_die):
                t.sites += 1
                if st in mode_ids:
                    t.modes[st] += 1
        die_rows.append({"die": die_key(col, row), "col": col, "row": row, "dose": dose, "focus": focus,
                         **t_die.row(mode_ids, pass_ids)})

    def cond_sort(c):
        return (c[0] is None, c[0] if c[0] is not None else 0, c[1] is None, c[1] if c[1] is not None else 0)

    conds = sorted(by_cond, key=cond_sort)
    cds = sorted(by_cd, key=_cd_sort)
    focus_values = sorted({f for _, f in conds if f is not None})
    return {
        "mode_ids": mode_ids,
        "conditions": [{"dose": c[0], "focus": c[1], **by_cond[c].row(mode_ids, pass_ids)} for c in conds],
        "focus_varies": len(focus_values) > 1,
        "cds": [{"key": k, "label": cd_label(k), "short": cd_short(k), "n_devices": sum(1 for v in dev_key.values() if v == k),
                 **by_cd[k].row(mode_ids, pass_ids)} for k in cds],
        "cd_cond": [{"key": k, "dose": dz, "focus": fc, **t.row(mode_ids, pass_ids)}
                    for (k, dz, fc), t in sorted(by_cd_cond.items(), key=lambda kv: (_cd_sort(kv[0][0]), cond_sort(kv[0][1:])))],
        "cdpitch_cond": [{"key": k, "pitch_nm": p, "dose": dz, "focus": fc, **t.row(mode_ids, pass_ids)}
                         for (k, p, dz, fc), t in sorted(by_cdpitch_cond.items(),
                                                         key=lambda kv: (_cd_sort(kv[0][0]), kv[0][1] or 0, cond_sort(kv[0][2:])))],
        "dies": die_rows,
        "total": total.row(mode_ids, pass_ids),
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
                        "status": r.get("status"), "ts": r.get("ts")})
    return out
