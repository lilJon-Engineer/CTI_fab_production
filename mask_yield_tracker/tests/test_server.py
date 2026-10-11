import http.client
import json
import threading

import pytest

from mty.batch import default_wafer, dies_on_wafer
from mty.server import make_server


@pytest.fixture()
def server(tmp_path):
    srv = make_server(tmp_path / "ws", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def req(srv, method, path, body=None, headers=None, raw=False):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    hdrs = dict(headers or {})
    if body is not None and not raw:
        body = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    c.request(method, path, body=body, headers=hdrs)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        return r.status, json.loads(data)
    except json.JSONDecodeError:
        return r.status, data


def upload_and_extract(srv, gds):
    s, d = req(srv, "POST", "/api/reticles?filename=synthetic.gds&name=syn", gds.read_bytes(), raw=True)
    assert s == 201, d
    rid = d["meta"]["id"]
    assert "SYN_top_Gate" in d["listing"]
    assert "top_cell = 'SYN_top_Gate'" in d["config"]           # suggested config
    s, d = req(srv, "POST", f"/api/reticles/{rid}/extract")
    assert s == 200, d
    assert d["meta"]["extracted"]["n_devices"] == 12
    return rid


def test_full_flow(server, synthetic_gds):
    s, settings = req(server, "GET", "/api/settings")
    assert s == 200 and {m["id"] for m in settings["failure_modes"]} >= {"pass", "lifting", "missing", "skating"}

    rid = upload_and_extract(server, synthetic_gds)
    s, table = req(server, "GET", f"/api/reticles/{rid}/devices")
    assert s == 200 and len(table["devices"]) == 12
    s, geo = req(server, "GET", f"/api/reticles/{rid}/geometry")
    assert s == 200 and set(geo["outlines_um"]) == {d["id"] for d in table["devices"]}

    wafer = default_wafer()
    wafer["dies"] = [[3, 3], [4, 3], [5, 3]]
    splits = {"columns": [{"dose": 10 + i, "focus": None} for i in range(9)]}
    s, b = req(server, "POST", "/api/batches", {"reticle_id": rid, "meta": {"lot": "L1", "wafer": "W2", "process_step": "gate litho"},
                                                 "wafer": wafer, "splits": splits})
    assert s == 201, b
    bid = b["id"]
    assert b["splits"]["columns"][4]["dose"] == 14 and len(b["splits"]["rows"]) == 9

    s, r = req(server, "POST", f"/api/batches/{bid}/results",
               {"die": [4, 3], "updates": [{"device_id": "XA01#1", "status": "pass"},
                                           {"device_id": "XA03#2", "status": "skating"}]})
    assert s == 200 and r["results"]["XA03#2"]["status"] == "skating"
    # clear one
    s, r = req(server, "POST", f"/api/batches/{bid}/results", {"die": [4, 3], "updates": [{"device_id": "XA01#1", "status": None}]})
    assert "XA01#1" not in r["results"]
    # validation
    assert req(server, "POST", f"/api/batches/{bid}/results", {"die": [0, 0], "updates": [{"device_id": "XA01#1", "status": "pass"}]})[0] == 400
    assert req(server, "POST", f"/api/batches/{bid}/results", {"die": [4, 3], "updates": [{"device_id": "nope", "status": "pass"}]})[0] == 400
    assert req(server, "POST", f"/api/batches/{bid}/results", {"die": [4, 3], "updates": [{"device_id": "XA01#1", "status": "weird"}]})[0] == 400

    # removing a die that has results needs confirmation
    wafer["dies"] = [[3, 3]]
    s, d = req(server, "PUT", f"/api/batches/{bid}", {"wafer": wafer})
    assert s == 409
    s, d = req(server, "PUT", f"/api/batches/{bid}", {"die_notes": {"4,3": "scratch near PCM"}})
    assert s == 200 and d["die_notes"]["4,3"] == "scratch near PCM"

    # export -> import gives a new id when the original still exists
    s, doc = req(server, "GET", f"/api/batches/{bid}/export")
    assert s == 200 and doc["schema"] == "mty.batch" and doc["version"] == 1
    s, imp = req(server, "POST", "/api/batches/import", doc)
    assert s == 201 and imp["id"] != bid and imp["results"] == doc["results"]
    s, lst = req(server, "GET", "/api/batches")
    assert {b["id"] for b in lst} == {bid, imp["id"]}

    # reticle in use cannot be deleted
    assert req(server, "DELETE", f"/api/reticles/{rid}")[0] == 409
    assert req(server, "DELETE", f"/api/batches/{bid}")[0] == 200
    assert req(server, "DELETE", f"/api/batches/{imp['id']}")[0] == 200
    assert req(server, "DELETE", f"/api/reticles/{rid}")[0] == 200


def test_static_and_bad_requests(server):
    s, body = req(server, "GET", "/")
    assert s == 200 and b"Mask Yield Tracker" in body
    assert req(server, "GET", "/app/../mty/server.py")[0] == 404
    assert req(server, "GET", "/api/batches/..%2F..%2Fsecret")[0] == 400
    s, d = req(server, "POST", "/api/reticles?filename=notes.txt", b"hello", raw=True)
    assert s == 400 and "layout file" in d["error"]
    s, d = req(server, "POST", "/api/reticles?filename=broken.gds", b"not a gds", raw=True)
    assert s == 400


def test_config_validation_on_save(server, synthetic_gds):
    s, d = req(server, "POST", "/api/reticles?filename=s.gds", synthetic_gds.read_bytes(), raw=True)
    rid = d["meta"]["id"]
    s, d = req(server, "PUT", f"/api/reticles/{rid}/config", b"this is = = not toml", raw=True)
    assert s == 400
    s, d = req(server, "PUT", f"/api/reticles/{rid}/config", b"[gate]\ntop_cell='X'\nlayer=1\n", raw=True)
    assert s == 400 and "groups" in d["error"]
    assert req(server, "GET", f"/api/reticles/{rid}/devices")[0] == 404     # not extracted yet


def test_rejects_foreign_host_and_origin(server):
    assert req(server, "GET", "/api/batches", headers={"Host": "evil.example:80"})[0] == 403
    assert req(server, "GET", "/api/batches", headers={"Origin": "http://evil.example"})[0] == 403
    port = server.server_address[1]
    assert req(server, "GET", "/api/batches", headers={"Origin": f"http://localhost:{port}", "Host": f"localhost:{port}"})[0] == 200


def test_dies_on_wafer():
    w = default_wafer()                       # 100 mm, 3 mm exclusion, 10 mm step, 9x9
    dies = dies_on_wafer(w)
    assert [4, 4] in dies and [0, 0] not in dies
    assert dies == sorted(dies, key=lambda d: (d[1], d[0]))
    # smaller field fits more dies than the full step
    assert len(dies_on_wafer(w, 8.76, 8.95)) >= len(dies)


def test_new_batch_matches_schema(table):
    from pathlib import Path
    from mty.batch import new_batch
    schema = json.loads((Path(__file__).parent.parent / "schemas" / "batch.v1.schema.json").read_text())
    b = new_batch({"id": "r1", "name": "syn"}, table, {"lot": "L", "wafer": "W", "process_step": "S"})
    assert set(schema["required"]) <= set(b)
    assert set(b) <= set(schema["properties"])
    assert set(schema["properties"]["wafer"]["required"]) <= set(b["wafer"])
