"""Build the device table (schema mty.device_table v1) from a reticle file."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import klayout.db as db

from .. import __version__
from ..config import Config
from .geometry import analyze
from .sites import RawSite, find_sites

DEVICE_TABLE_SCHEMA = "mty.device_table"
DEVICE_TABLE_VERSION = 1


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(path: Path, cache: dict) -> db.Layout:
    key = str(path.resolve())
    if key not in cache:
        ly = db.Layout()
        ly.read(str(path))
        cache[key] = ly
    return cache[key]


def _bbox_of(site: RawSite) -> db.Box:
    b = db.Box()
    for p in site.prims:
        b += p.bbox
    return b


def _pair(name: str, gate_box: db.Box, sd_sites: list[tuple[RawSite, db.Box]], min_cov: float):
    """Find the S/D site for a gate site.

    1. S/D sites with the same device name: the one covering the gate site
       best, then the nearest (handles repeated placements).
    2. Otherwise any S/D site covering >= min_cov of the gate site, smallest
       first (the most specific cell on top of the gate geometry).
    Returns (site, coverage, how) or (None, 0.0, None).
    """
    ga = gate_box.area() or 1
    gc = gate_box.center()

    def cov(sb: db.Box) -> float:
        inter = gate_box & sb
        return 0.0 if inter.empty() else inter.area() / ga

    same = [(cov(sb), -gc.distance(sb.center()), s) for s, sb in sd_sites if s.name == name]
    if same:
        c, _, s = max(same, key=lambda t: (t[0], t[1]))
        return s, c, "name" if c > 0 else "name_only"
    best = None
    for s, sb in sd_sites:
        if s.name.endswith("/_wires"):
            continue
        c = cov(sb)
        if c >= min_cov and (best is None or (c, -sb.area()) > best[0]):
            best = ((c, -sb.area()), s, c)
    return (best[1], best[2], "overlap") if best else (None, 0.0, None)


def extract(reticle: str | Path, cfg: Config) -> dict:
    reticle = Path(reticle)
    cache: dict = {}
    gate_path = Path(cfg.gate.file) if cfg.gate.file else reticle
    gate_ly = _load(gate_path, cache)
    um = gate_ly.dbu            # um per dbu
    nm = um * 1000.0
    geo = cfg.geometry

    gate_sites = find_sites(gate_ly, cfg.gate, cfg)

    sd_sites: list[tuple[RawSite, db.Box]] = []
    sd_path = None
    if cfg.sd is not None:
        sd_path = Path(cfg.sd.file) if cfg.sd.file else gate_path
        sd_ly = _load(sd_path, cache)
        if abs(sd_ly.dbu - gate_ly.dbu) > 1e-12:
            raise ValueError("gate and S/D files use different database units")
        sd_sites = [(s, _bbox_of(s)) for s in find_sites(sd_ly, cfg.sd, cfg)]

    devices = []
    for s in gate_sites:
        g = analyze(s.prims, um, geo)
        flags = list(g.flags)
        name, sd_name, sd_cell, cov, how = s.name, None, None, None, None
        if cfg.sd is not None:
            match, cov, how = _pair(s.name, g.bbox, sd_sites, cfg.min_coverage)
            if match is None:
                flags.append("no_sd_match")
            else:
                sd_cell, sd_name = match.cell, match.name
                if how == "name_only":
                    flags.append("sd_matched_by_name_without_overlap")
                elif how == "overlap":
                    flags.append("sd_name_differs_from_gate")
        b = g.bbox
        devices.append({
            "name": name,
            "group": s.group,
            "gate_cell": s.cell,
            "parent_cell": s.parent_cell,
            "sd_name": sd_name,
            "sd_cell": sd_cell,
            "sd_match": how,
            "sd_coverage": None if cov is None else round(cov, 3),
            "kind": g.kind,
            "bbox_um": [round(b.left * um, 4), round(b.bottom * um, 4), round(b.right * um, 4), round(b.top * um, 4)],
            "center_um": [round(b.center().x * um, 4), round(b.center().y * um, 4)],
            "cd_nm": None if g.cd_dbu is None else round(g.cd_dbu * nm, 2),
            "n_fingers": g.n_fingers,
            "pitch_nm": None if g.pitch_dbu is None else round(g.pitch_dbu * nm, 2),
            "orientation": g.orientation,
            "density": g.density,
            "thinnest_nm": None if g.thinnest_dbu is None else round(g.thinnest_dbu * nm, 2),
            "widths_nm": {str(round(w * nm, 2)): n for w, n in sorted(g.widths.items())},
            "flags": flags,
        })

    # Stable ids: name + placement index ordered top-to-bottom, left-to-right.
    by_name = defaultdict(list)
    for d in devices:
        by_name[d["name"]].append(d)
    for name, ds in by_name.items():
        ds.sort(key=lambda d: (-d["center_um"][1], d["center_um"][0]))
        for i, d in enumerate(ds, 1):
            d["placement"] = i
            d["placements_total"] = len(ds)
            d["id"] = f"{name}#{i}"
    devices.sort(key=lambda d: (d["group"], d["name"], d["placement"]))
    devices = [{"id": d.pop("id"), **d} for d in devices]

    top = gate_ly.cell(cfg.gate.top_cell)
    fb = top.bbox()
    return {
        "schema": DEVICE_TABLE_SCHEMA,
        "version": DEVICE_TABLE_VERSION,
        "generator": f"mty {__version__}",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "gate_file": gate_path.name,
            "gate_sha256": sha256(gate_path),
            "sd_file": sd_path.name if sd_path else None,
            "sd_sha256": sha256(sd_path) if sd_path else None,
            "dbu_um": um,
            "config": cfg.raw,
        },
        "field": {"bbox_um": [fb.left * um, fb.bottom * um, fb.right * um, fb.top * um]},
        "groups": [g.name for g in cfg.groups],
        "devices": devices,
    }


CSV_COLUMNS = ["id", "name", "group", "kind", "cd_nm", "n_fingers", "pitch_nm", "density", "orientation",
               "thinnest_nm", "center_x_um", "center_y_um", "x0_um", "y0_um", "x1_um", "y1_um",
               "gate_cell", "parent_cell", "sd_name", "sd_cell", "sd_match", "sd_coverage", "widths_nm", "flags"]


def write_outputs(table: dict, out_dir: str | Path, stem: str = "device_table") -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp, cp = out / f"{stem}.json", out / f"{stem}.csv"
    jp.write_text(json.dumps(table, indent=1), encoding="utf-8")
    with open(cp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for d in table["devices"]:
            x0, y0, x1, y1 = d["bbox_um"]
            w.writerow([d["id"], d["name"], d["group"], d["kind"], d["cd_nm"], d["n_fingers"], d["pitch_nm"],
                        d["density"], d["orientation"], d["thinnest_nm"], *d["center_um"], x0, y0, x1, y1,
                        d["gate_cell"], d["parent_cell"], d["sd_name"], d["sd_cell"], d["sd_match"], d["sd_coverage"],
                        ";".join(f"{k}x{v}" for k, v in d["widths_nm"].items()), ";".join(d["flags"])])
    return jp, cp


def summary(table: dict) -> str:
    ds = table["devices"]
    lines = [f"{len(ds)} sites   field {table['field']['bbox_um']} um   dbu {table['source']['dbu_um']} um", ""]
    lines.append("By group / kind:")
    gk = Counter((d["group"], d["kind"]) for d in ds)
    for (g, k), n in sorted(gk.items()):
        lines.append(f"  {g:20s} {k:14s} {n:5d}")
    lines.append("")
    lines.append("Sites per CD (finger arrays and single lines):")
    cds = Counter(d["cd_nm"] for d in ds if d["cd_nm"] is not None)
    for cd, n in sorted(cds.items()):
        dens = Counter(d["density"] for d in ds if d["cd_nm"] == cd)
        lines.append(f"  {cd:9.1f} nm  {n:5d}   " + "  ".join(f"{k}:{v}" for k, v in sorted(dens.items(), key=str)))
    other = [d for d in ds if d["cd_nm"] is None]
    if other:
        lines.append("")
        lines.append("Sites without a finger CD (thinnest dimension):")
        th = Counter((d["kind"], d["thinnest_nm"]) for d in other)
        for (k, t), n in sorted(th.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
            lines.append(f"  {k:14s} {t!s:>9} nm  {n:5d}")
    flags = Counter(f for d in ds for f in d["flags"])
    if flags:
        lines.append("")
        lines.append("Flags (check these):")
        for f, n in flags.most_common():
            lines.append(f"  {f:32s} {n:5d}")
    return "\n".join(lines)
