"""Synthetic two-reticle layout used for tests and demos.

Mimics the structure of a real gate / source-drain reticle pair without any
real data: two top cells sharing one origin, device sub-cells with finger
arrays (paths + serif triangles + bus), text sub-cells, repeated and arrayed
placements, a rotated PCM block with sub-devices and long bent lines, and a
gate-only litho structure.
"""
from __future__ import annotations

from pathlib import Path

import klayout.db as db

GATE = (1, 0)
SD = (2, 0)
UM = 1000  # dbu per um (dbu = 1 nm)

# name: (cd_nm, n_fingers, pitch_nm)
CUTOFF_DEVICES = {
    "XA01": (200, 8, 6400),
    "XA02": (300, 4, 6500),
    "XA03": (150, 1, 0),
    "XA04": (250, 8, 6400),
}
PCM_SUBDEVICES = {"SubA": (150, 8, 6400), "SubB": (250, 8, 6400)}


class _Builder:
    def __init__(self) -> None:
        self.ly = db.Layout()
        self.ly.dbu = 0.001
        self.g = self.ly.layer(*GATE)
        self.s = self.ly.layer(*SD)
        self._text: dict[tuple[str, str], db.Cell] = {}

    def cell(self, name: str) -> db.Cell:
        return self.ly.cell(name) or self.ly.create_cell(name)

    def place(self, parent: db.Cell, child: db.Cell, x: int, y: int, rot: int = 0) -> None:
        parent.insert(db.CellInstArray(child.cell_index(), db.Trans(rot, False, x, y)))

    def text(self, label: str, sfx: str, layer: int) -> db.Cell:
        """Glyph-like text cell named like klayout TEXT output (e.g. Letter_A$$123)."""
        key = (label, sfx)
        if key not in self._text:
            if label.isdigit():
                name = f"Digit_{label}s$$9{len(self._text)}{sfx}"
            elif len(label) == 1:
                name = f"{label}$$8{len(self._text)}{sfx}"
            else:
                name = f"Letter_{label[0]}$$7{len(self._text)}{sfx}"
            c = self.cell(name)
            # an L-shaped glyph: narrow strokes that must NOT be read as a CD
            c.shapes(layer).insert(db.Polygon([db.Point(0, 0), db.Point(3000, 0), db.Point(3000, 120),
                                               db.Point(120, 120), db.Point(120, 5000), db.Point(0, 5000)]))
            self._text[key] = c
        return self._text[key]

    def finger_device(self, name: str, cd: int, n: int, pitch: int) -> db.Cell:
        """Gate finger array: n vertical fingers (58 um) on a 2 um bus, serif triangles at the joint."""
        fg = self.cell(f"Fg_{name}_Gate")
        L = 58 * UM
        fg.shapes(self.g).insert(db.Path([db.Point(0, 0), db.Point(0, L)], cd))
        h = cd // 2
        fg.shapes(self.g).insert(db.Polygon([db.Point(h, L - 300), db.Point(h, L), db.Point(h + 300, L)]))
        fg.shapes(self.g).insert(db.Polygon([db.Point(-h, L - 300), db.Point(-h - 300, L), db.Point(-h, L)]))
        core = self.cell(f"Core_{name}_Gate")
        if n > 1:
            core.insert(db.CellInstArray(fg.cell_index(), db.Trans(0, 0), db.Vector(pitch, 0), db.Vector(0, 0), n, 1))
        else:
            core.insert(db.CellInstArray(fg.cell_index(), db.Trans(0, 0)))
        span = (n - 1) * pitch
        core.shapes(self.g).insert(db.Path([db.Point(-600, L + 1000), db.Point(span + 600, L + 1000)], 2 * UM))
        return core

    def device(self, name: str, cd: int, n: int, pitch: int, with_sd: bool = True) -> tuple[db.Cell, db.Cell]:
        gate = self.cell(f"{name}_Gate")
        self.place(gate, self.finger_device(name, cd, n, pitch), 80 * UM, 100 * UM)
        # gate pad stub (wide box, not a finger)
        gate.shapes(self.g).insert(db.Box(80 * UM, 170 * UM, 85 * UM, 178 * UM))
        for i, ch in enumerate([name[0], name[1:3]]):
            self.place(gate, self.text(ch, "_Gate", self.g), 10 * UM + i * 6 * UM, 20 * UM)
        sd = self.cell(f"{name}_SD")
        if with_sd:
            sd.shapes(self.s).insert(db.Box(0, 0, 300 * UM, 330 * UM))
            for i, ch in enumerate([name[0], name[1:3]]):
                self.place(sd, self.text(ch, "_SD", self.s), 10 * UM + i * 6 * UM, 20 * UM)
        return gate, sd

    def build(self) -> db.Layout:
        tops = {sfx: self.cell(f"SYN_top{sfx}") for sfx in ("_Gate", "_SD")}
        devs = {sfx: self.cell(f"Devices_top{sfx}") for sfx in tops}
        cut = {sfx: self.cell(f"Logic_top{sfx}") for sfx in tops}
        pcm = {sfx: self.cell(f"PCM_top{sfx}") for sfx in tops}
        lit = {sfx: self.cell(f"Litho_top{sfx}") for sfx in tops}
        for sfx in tops:
            self.place(tops[sfx], devs[sfx], 19 * UM, -24 * UM)
            self.place(tops[sfx], lit[sfx], 20 * UM, 0)
            self.place(devs[sfx], cut[sfx], 0, 0)
            self.place(devs[sfx], pcm[sfx], 0, 0)
            # field label at top level (text, must be ignored)
            self.place(tops[sfx], self.text("A", sfx, self.g if sfx == "_Gate" else self.s), 6000 * UM, 100 * UM)

        # --- Logic block: XA01, XA02 once; XA03 placed twice; XA04 as a 1x3 array
        placements = {"XA01": [(0, 4000)], "XA02": [(400, 4000)], "XA03": [(800, 4000), (800, 3500)]}
        for name, (cd, n, p) in CUTOFF_DEVICES.items():
            g, s = self.device(name, cd, n, p)
            for sfx, c in (("_Gate", g), ("_SD", s)):
                if name == "XA04":
                    cut[sfx].insert(db.CellInstArray(c.cell_index(), db.Trans(0, 3000 * UM),
                                                     db.Vector(400 * UM, 0), db.Vector(0, 0), 3, 1))
                    continue
                for x, y in placements[name]:
                    self.place(cut[sfx], c, x * UM, y * UM)
            # a text cell directly in the container must not become a site
        self.place(cut["_Gate"], self.text("Q", "_Gate", self.g), 2000 * UM, 4500 * UM)

        # --- PCM block: one PCM cell, rotated 270, with sub-devices, pads, long lines
        pg, ps = self.cell("PCMA_01_Gate"), self.cell("PCMA_01_SD")
        frame_g, frame_s = self.cell("PCM_frame_A_Gate"), self.cell("PCM_frame_A_SD")
        for k in range(4):
            frame_g.shapes(self.g).insert(db.Box(k * 300 * UM, 0, k * 300 * UM + 94 * UM, 94 * UM))
            frame_s.shapes(self.s).insert(db.Box(k * 300 * UM, 0, k * 300 * UM + 94 * UM, 94 * UM))
        self.place(pg, frame_g, 0, 0)
        self.place(ps, frame_s, 0, 0)
        for i, (sub, (cd, n, p)) in enumerate(PCM_SUBDEVICES.items()):
            core = self.finger_device(f"PCMA_{sub}", cd, n, p)
            sg = self.cell(f"{sub}_Gate")
            self.place(sg, core, 0, 0)
            ss = self.cell(f"{sub}_SD")
            ss.shapes(self.s).insert(db.Box(-5 * UM, -5 * UM, (n - 1) * p + 5 * UM, 70 * UM))
            self.place(pg, sg, (150 + 400 * i) * UM, 120 * UM)
            self.place(ps, ss, (150 + 400 * i) * UM, 120 * UM)
        # meander sub-cell: long 350 nm line with bends
        mg = self.cell("meander$$555_Gate")
        pts = [db.Point(0, 0)]
        for k in range(6):
            x = (k % 2) * 60 * UM
            pts += [db.Point(x, k * 5 * UM), db.Point(60 * UM - x, k * 5 * UM)]
        mg.shapes(self.g).insert(db.Path(pts[1:], 350))
        ms = self.cell("meander$$555_SD")
        ms.shapes(self.s).insert(db.Box(-5 * UM, -5 * UM, 65 * UM, 30 * UM))
        self.place(pg, mg, 900 * UM, 150 * UM)
        self.place(ps, ms, 900 * UM, 150 * UM)
        # own shapes: bent 350 nm routing line (long) drawn directly in the PCM cell
        pg.shapes(self.g).insert(db.Path([db.Point(100 * UM, 200 * UM), db.Point(700 * UM, 200 * UM),
                                          db.Point(700 * UM, 260 * UM)], 350))
        ps.shapes(self.s).insert(db.Box(90 * UM, 190 * UM, 710 * UM, 270 * UM))
        self.place(pg, self.text("P", "_Gate", self.g), 50 * UM, 300 * UM)
        self.place(ps, self.text("P", "_SD", self.s), 50 * UM, 300 * UM)
        self.place(pcm["_Gate"], pg, 2000 * UM, 3000 * UM, rot=3)
        self.place(pcm["_SD"], ps, 2000 * UM, 3000 * UM, rot=3)

        # --- Litho block: gate-only CD bars (no S/D geometry)
        bars_g = self.cell("BARS_Gate")
        for k in range(5):
            x = k * 5500
            bars_g.shapes(self.g).insert(db.Path([db.Point(x, 0), db.Point(x, 20 * UM)], 5 * UM))
        bars_s = self.cell("BARS_SD")  # exists but empty, like the real litho cells
        self.place(lit["_Gate"], bars_g, 100 * UM, 100 * UM)
        self.place(lit["_SD"], bars_s, 100 * UM, 100 * UM)
        return self.ly


# Expected extraction results, used by the tests.
EXPECTED_SITES = {
    "XA01#1", "XA02#1", "XA03#1", "XA03#2", "XA04#1", "XA04#2", "XA04#3",
    "PCMA_01/SubA#1", "PCMA_01/SubB#1", "PCMA_01/meander#1", "PCMA_01/_wires#1",
    "BARS#1",
}


def build(out: str | Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _Builder().build().write(str(out))
    return out


SYNTHETIC_CONFIG = Path(__file__).resolve().parent.parent / "config" / "reticle.example.toml"
