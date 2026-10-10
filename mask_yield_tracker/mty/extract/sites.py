"""Find inspection sites in one reticle (gate or S/D) of a layout.

A *site* is one physical placement that gets one pass/fail result:
  - each child placement of a group container (arrays are expanded), or
  - for split groups, each geometry-bearing sub-cell of such a device, plus
    an optional "<device>/_wires" site for shapes drawn in the device itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterator

import klayout.db as db

from ..config import Config, ReticleSide
from .geometry import Prim, shape_to_prim

_HASH_SUFFIX = re.compile(r"\$\$\d+")


def clean_name(stem: str) -> str:
    """Drop klayout's '$$<number>' uniquifier from a cell name."""
    return _HASH_SUFFIX.sub("", stem)


@dataclass
class RawSite:
    group: str
    name: str                    # device name, "/"-joined for sub-sites
    cell: str                    # cell that defines the site (the device cell for _wires)
    parent_cell: str | None      # device cell for sub-sites
    trans: db.ICplxTrans         # cell -> field
    prims: list[Prim]


def placements(cell: db.Cell) -> Iterator[tuple[db.Cell, db.ICplxTrans, int]]:
    """Child cells with each array member's transform, plus the array size."""
    for inst in cell.each_inst():
        ca = inst.cell_inst
        n = ca.size()
        for t in ca.each_cplx_trans():
            yield inst.cell, t, n


def find_cell_placements(layout: db.Layout, top: db.Cell, target: str) -> list[db.ICplxTrans]:
    """All absolute placements of the cell named `target` below (or at) `top`."""
    found: list[db.ICplxTrans] = []

    def walk(c: db.Cell, t: db.ICplxTrans) -> None:
        if c.name == target:
            found.append(t)
            return
        for child, ct, _ in placements(c):
            walk(child, t * ct)

    walk(top, db.ICplxTrans())
    return found


def collect_prims(cell: db.Cell, layer: int, trans: db.ICplxTrans, skip: Callable[[str], bool],
                  ignore_triangles: bool, own_only: bool = False) -> list[Prim]:
    out: list[Prim] = []
    for s in cell.shapes(layer).each():
        p = shape_to_prim(s, trans, ignore_triangles)
        if p is not None:
            out.append(p)
    if own_only:
        return out
    for child, ct, _ in placements(cell):
        if skip(child.name):
            continue
        out.extend(collect_prims(child, layer, trans * ct, skip, ignore_triangles))
    return out


def find_sites(layout: db.Layout, side: ReticleSide, cfg: Config) -> list[RawSite]:
    top = layout.cell(side.top_cell)
    if top is None:
        raise ValueError(f"top cell {side.top_cell!r} not found")
    layer = layout.find_layer(side.layer, side.datatype)
    if layer is None:
        raise ValueError(f"layer {side.layer}/{side.datatype} not found")
    ign = cfg.geometry.ignore_triangles
    skip = cfg.is_text

    sites: list[RawSite] = []
    for group in cfg.groups:
        container = group.name + side.cell_suffix
        if layout.cell(container) is None:
            raise ValueError(f"group container {container!r} not found")
        for ct in find_cell_placements(layout, top, container):
            for dev, dt, _ in placements(layout.cell(container)):
                if skip(dev.name):
                    continue
                t = ct * dt
                dname = clean_name(side.stem(dev.name))
                if not group.split:
                    prims = collect_prims(dev, layer, t, skip, ign)
                    if prims:
                        sites.append(RawSite(group.name, dname, dev.name, None, t, prims))
                    continue
                # split device: one site per sub-cell instance (arrays stay one site)
                for inst in dev.each_inst():
                    sub = inst.cell
                    if skip(sub.name) or cfg.is_split_excluded(sub.name):
                        continue
                    prims = []
                    for st in inst.cell_inst.each_cplx_trans():
                        prims.extend(collect_prims(sub, layer, t * st, skip, ign))
                    if prims:
                        sname = f"{dname}/{clean_name(side.stem(sub.name))}"
                        sites.append(RawSite(group.name, sname, sub.name, dev.name, t, prims))
                if cfg.own_shapes_site:
                    prims = collect_prims(dev, layer, t, skip, ign, own_only=True)
                    if prims:
                        sites.append(RawSite(group.name, f"{dname}/_wires", dev.name, dev.name, t, prims))
    return sites
