import csv
import json

import pytest

from mty.config import ConfigError, config_from_dict
from mty.extract import summary, write_outputs
from mty.extract.listing import describe
from mty.synthetic import CUTOFF_DEVICES, EXPECTED_SITES, PCM_SUBDEVICES


def test_schema_header(table):
    assert table["schema"] == "mty.device_table"
    assert table["version"] == 1
    assert table["source"]["dbu_um"] == pytest.approx(0.001)
    assert len(table["source"]["gate_sha256"]) == 64


def test_exact_site_set(by_id):
    # text cells, pad frames and empty cells never become sites
    assert set(by_id) == EXPECTED_SITES


@pytest.mark.parametrize("name", ["XA01", "XA02", "XA04"])
def test_finger_arrays(by_id, name):
    cd, n, pitch = CUTOFF_DEVICES[name]
    d = by_id[f"{name}#1"]
    assert d["kind"] == "finger_array"
    assert d["cd_nm"] == cd
    assert d["n_fingers"] == n
    assert d["pitch_nm"] == pitch
    assert d["orientation"] == "v"


def test_single_finger(by_id):
    d = by_id["XA03#1"]
    assert d["kind"] == "single_line"
    assert d["cd_nm"] == 150 and d["n_fingers"] == 1 and d["density"] == "isolated"


def test_text_and_serifs_ignored(by_id):
    # text glyph strokes are 120 nm and serif triangles are tiny: neither may show up
    for d in by_id.values():
        assert "120.0" not in d["widths_nm"]
        assert d["thinnest_nm"] >= 150


def test_repeated_and_arrayed_placements(by_id):
    a, b = by_id["XA03#1"], by_id["XA03#2"]
    assert a["placements_total"] == b["placements_total"] == 2
    assert a["center_um"][1] > b["center_um"][1]          # #1 is the upper one
    xs = [by_id[f"XA04#{i}"]["center_um"][0] for i in (1, 2, 3)]
    assert xs == sorted(xs) and xs[1] - xs[0] == pytest.approx(400)


def test_pcm_split_and_rotation(by_id):
    for sub, (cd, n, pitch) in PCM_SUBDEVICES.items():
        d = by_id[f"PCMA_01/{sub}#1"]
        assert d["group"] == "PCM_top" and d["parent_cell"] == "PCMA_01_Gate"
        assert d["cd_nm"] == cd and d["n_fingers"] == n and d["pitch_nm"] == pitch
        assert d["orientation"] == "h"                     # PCM cell placed at 270 deg


def test_long_lines(by_id):
    for sid in ("PCMA_01/meander#1", "PCMA_01/_wires#1"):
        d = by_id[sid]
        assert d["kind"] == "long_line"
        assert d["cd_nm"] is None and d["thinnest_nm"] == 350


def test_sd_pairing(by_id):
    for sid, d in by_id.items():
        if sid == "BARS#1":
            assert d["sd_name"] is None and "no_sd_match" in d["flags"]
        else:
            assert d["sd_match"] == "name" and d["sd_name"] == d["name"]
            assert d["sd_coverage"] == 1.0, sid
            assert d["sd_cell"].endswith("_SD")


def test_positions_in_field(table, by_id):
    fx0, fy0, fx1, fy1 = table["field"]["bbox_um"]
    for d in by_id.values():
        x0, y0, x1, y1 = d["bbox_um"]
        assert fx0 <= x0 < x1 <= fx1 and fy0 <= y0 < y1 <= fy1


def test_outputs_and_summary(table, tmp_path):
    jp, cp = write_outputs(table, tmp_path)
    assert json.loads(jp.read_text())["devices"] == table["devices"]
    rows = list(csv.DictReader(cp.open()))
    assert {r["id"] for r in rows} == EXPECTED_SITES
    text = summary(table)
    assert "12 sites" in text and "no_sd_match" in text


def test_listing(synthetic_gds):
    text = describe(str(synthetic_gds))
    assert "1/0" in text and "2/0" in text
    assert "SYN_top_Gate" in text and "SYN_top_SD" in text


def test_config_validation():
    with pytest.raises(ConfigError):
        config_from_dict({"gate": {"top_cell": "T", "layer": 1}})          # no groups
    with pytest.raises(ConfigError):
        config_from_dict({"config_version": 99, "gate": {}, "groups": []})


def test_rows_match_schema(table):
    from pathlib import Path
    schema = json.loads((Path(__file__).parent.parent / "schemas" / "device_table.v1.schema.json").read_text())
    dev = schema["$defs"]["device"]
    for d in table["devices"]:
        assert set(d) == set(dev["properties"]), set(d) ^ set(dev["properties"])
        if d["kind"] is not None:
            assert d["kind"] in dev["properties"]["kind"]["enum"]


def test_geometry_outlines(synthetic_gds, config, table, tmp_path):
    from mty.extract import extract
    geo: dict = {}
    t2 = extract(synthetic_gds, config, geo)
    assert set(geo) == {d["id"] for d in t2["devices"]}
    d = next(d for d in t2["devices"] if d["id"] == "XA01#1")
    polys = geo["XA01#1"]
    assert len(polys) == 8 + 2          # 8 fingers, bus, pad stub (serifs and text excluded)
    xs = [v for p in polys for v in p[0::2]]
    ys = [v for p in polys for v in p[1::2]]
    x0, y0, x1, y1 = d["bbox_um"]
    assert min(xs) >= x0 - 1e-6 and max(xs) <= x1 + 1e-6 and min(ys) >= y0 - 1e-6 and max(ys) <= y1 + 1e-6
    write_outputs(t2, tmp_path, geometry=geo)
    assert json.loads((tmp_path / "geometry.json").read_text())["outlines_um"]["XA01#1"] == polys
