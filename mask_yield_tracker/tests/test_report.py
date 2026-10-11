import json

import pytest

from mty.batch import load_failure_modes, new_batch, set_result
from mty.report import aggregate, build_report, extract_report_data, write_report
from mty.report.aggregate import wilson


@pytest.fixture()
def batch(table):
    w = {"diameter_mm": 100, "edge_exclusion_mm": 3, "step_x_mm": 10, "step_y_mm": 10, "n_cols": 3, "n_rows": 1,
         "dies": [[0, 0], [1, 0], [2, 0]]}
    b = new_batch({"id": "r1", "name": "syn"}, table, {"lot": "L1", "wafer": "W1", "process_step": "gate"}, w,
                  {"columns": [{"dose": 10}, {"dose": 12}, {"dose": 14}]})
    ids = {d["id"] for d in table["devices"]}
    st = {m["id"] for m in load_failure_modes()}
    # die 0 (dose 10): XA04 x3 -> 1 pass, 1 missing, 1 lifting ; die 1: all three pass ; die 2: nothing
    for dev, s in (("XA04#1", "pass"), ("XA04#2", "missing"), ("XA04#3", "lifting"), ("XA03#1", "skating")):
        set_result(b, 0, 0, dev, s, st, ids)
    for dev in ("XA04#1", "XA04#2", "XA04#3"):
        set_result(b, 1, 0, dev, "pass", st, ids)
    return b


def test_aggregate_counts(batch, table):
    agg = aggregate(batch, table, load_failure_modes())
    cell = {(r["key"], r["dose"]): r for r in agg["cd_cond"]}
    a = cell[("cd:250", 10)]
    # 250 nm: XA04 x3 + PCMA SubB = 4 sites per die
    assert (a["sites"], a["inspected"], a["pass"]) == (4, 3, 1)
    assert a["yield"] == pytest.approx(1 / 3)
    assert a["modes"] == {"pass": 1, "lifting": 1, "missing": 1, "skating": 0}
    assert cell[("cd:250", 12)]["yield"] == 1.0
    assert cell[("cd:250", 14)]["yield"] is None and cell[("cd:250", 14)]["inspected"] == 0
    assert cell[("cd:150", 10)]["modes"]["skating"] == 1
    t = agg["total"]
    assert t["sites"] == 12 * 3 and t["inspected"] == 7 and t["pass"] == 4
    assert [d["inspected"] for d in agg["dies"]] == [4, 3, 0]
    assert not agg["focus_varies"]
    assert [c["dose"] for c in agg["conditions"]] == [10, 12, 14]


def test_wilson():
    lo, hi = wilson(5, 10)
    assert lo < 0.5 < hi and 0 <= lo and hi <= 1
    assert wilson(0, 0) == (None, None)
    assert wilson(10, 10)[1] == pytest.approx(1.0)


def test_focus_split(batch, table):
    batch["splits"]["rows"][0]["focus"] = 0.2
    batch["splits"]["columns"][1]["focus"] = -0.2      # row value wins
    agg = aggregate(batch, table, load_failure_modes())
    assert {c["focus"] for c in agg["conditions"]} == {0.2}


def test_report_html_roundtrip(batch, table, tmp_path):
    modes = load_failure_modes()
    html = build_report(batch, table, modes)
    assert html.startswith("<!doctype html>") and "Yield vs dose per CD" in html
    assert "http://" not in html.replace("http://www.w3.org", "") and "https://" not in html  # fully offline
    data = extract_report_data(html)
    assert data["batch"] == batch
    assert data["aggregates"]["total"]["inspected"] == 7
    assert sum(len(v) for v in data["batch"]["results"].values()) == 7

    p = write_report(batch, table, modes, tmp_path)
    names = sorted(f.name for f in tmp_path.iterdir())
    assert p.name in names and any(n.endswith("_yield_by_cd_dose.csv") for n in names)
    rows = (tmp_path / f"{batch['id']}_report_results.csv").read_text().splitlines()
    assert len(rows) == 8 and rows[0].startswith("die,col,row,dose")


def test_report_escapes_script_end(batch, table):
    batch["meta"]["notes"] = "</script><script>alert(1)</script>"
    html = build_report(batch, table, load_failure_modes())
    assert html.count("<script>alert(1)") == 0
    assert extract_report_data(html)["batch"]["meta"]["notes"] == batch["meta"]["notes"]


def test_not_a_report():
    with pytest.raises(ValueError):
        extract_report_data("<html></html>")
