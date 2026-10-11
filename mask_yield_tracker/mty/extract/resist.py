"""Thin resist (masking) features: the resist left between drawn features.

For a lift-off gate level the drawn shapes are openings (metal lands there)
and resist covers everything else. Narrow resist lines between metal strips
have little contact area and can skate. This module finds every part of the
resist that is thinner than a threshold and describes it by:

  min_width   narrowest point (width check)
  mean_width  2 * area / perimeter (the typical width of a strip)
  length      perimeter / 2 (strip length)
  area        contact area with the substrate
  anchoring   how it connects to the bulk resist:
              island (0 contacts), one_end (1), both_ends (2+)

The analysis runs once over the whole reticle field; features are then
attributed to every site whose own drawn geometry borders them.
"""
from __future__ import annotations

from dataclasses import dataclass

import klayout.db as db

from ..config import ResistConfig
from .geometry import Prim


@dataclass
class ResistFeature:
    poly: db.Polygon
    min_width: int          # dbu
    mean_width: float       # dbu
    length: float           # dbu
    area: float             # dbu^2
    contacts: int

    @property
    def anchoring(self) -> str:
        return "island" if self.contacts == 0 else "one_end" if self.contacts == 1 else "both_ends"


def _min_width(poly: db.Polygon, hi: int) -> int:
    r = db.Region(poly)
    lo = 1
    if r.width_check(hi).is_empty():
        return hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if r.width_check(mid).is_empty():
            lo = mid
        else:
            hi = mid
    return lo


def find_thin_resist(drawn: db.Region, field: db.Box, dbu_um: float, rc: ResistConfig) -> list[ResistFeature]:
    """drawn: merged drawn geometry of the reticle (field frame, dbu)."""
    margin = int(max(50.0, 10 * rc.thin_um) / dbu_um)
    window = db.Region(field.enlarged(margin, margin))
    resist = (window - drawn) if rc.drawn == "opening" else (drawn & window)
    r = max(1, int(round(rc.thin_um / dbu_um / 2)))
    bulk = resist.sized(-r).sized(r)          # morphological opening: parts wider than thin_um
    thin = (resist - bulk).merged()
    contacts = (thin.sized(1) & bulk).merged()
    min_area = rc.min_area_um2 / (dbu_um * dbu_um)
    hi = int(round(rc.thin_um / dbu_um))
    out = []
    for p in thin.each():
        a = p.area()
        if a < min_area:
            continue                          # corner slivers next to serifs etc.
        per = p.perimeter()
        n = contacts.interacting(db.Region(p).sized(2)).count()
        out.append(ResistFeature(p, _min_width(p, hi), 2 * a / per, per / 2, a, n))
    return out


def site_features(prims: list[Prim], features: list[ResistFeature], index: db.Region | None = None) -> list[int]:
    """Indices of features bordering this site's drawn geometry."""
    if not features:
        return []
    own = db.Region()
    for p in prims:
        if p.outline:
            own.insert(db.Polygon([db.Point(x, y) for x, y in p.outline]))
    own = own.sized(2)
    bb = own.bbox()
    return [i for i, f in enumerate(features)
            if f.poly.bbox().overlaps(bb) and not own.interacting(db.Region(f.poly)).is_empty()]


def summarize(idx: list[int], features: list[ResistFeature], dbu_um: float) -> dict:
    nm = dbu_um * 1000
    fs = [features[i] for i in idx]
    anch = {"island": 0, "one_end": 0, "both_ends": 0}
    for f in fs:
        anch[f.anchoring] += 1
    if not fs:
        return {"resist_min_width_nm": None, "resist_mean_width_nm": None, "resist_min_area_um2": None,
                "resist_max_length_um": None, "resist_max_aspect": None, "resist_n_thin": 0,
                "resist_anchoring": anch, "resist_features": []}
    narrow = min(fs, key=lambda f: (f.min_width, f.area))
    return {
        "resist_min_width_nm": round(narrow.min_width * nm, 1),
        "resist_mean_width_nm": round(narrow.mean_width * nm, 1),
        "resist_min_area_um2": round(min(f.area for f in fs) * dbu_um * dbu_um, 2),
        "resist_max_length_um": round(max(f.length for f in fs) * dbu_um, 2),
        "resist_max_aspect": round(max(f.length / max(f.mean_width, 1) for f in fs), 1),
        "resist_n_thin": len(fs),
        "resist_anchoring": anch,
        "resist_features": [{"min_width_nm": round(f.min_width * nm, 1), "mean_width_nm": round(f.mean_width * nm, 1),
                             "length_um": round(f.length * dbu_um, 2), "area_um2": round(f.area * dbu_um * dbu_um, 2),
                             "anchoring": f.anchoring} for f in sorted(fs, key=lambda f: f.min_width)],
    }
