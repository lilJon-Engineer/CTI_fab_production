"""Local web server for the inspection app (stdlib only).

Binds to 127.0.0.1 and rejects requests whose Host / Origin is not this
server, so other web pages open in the browser cannot read reticle data
through it (DNS-rebinding / cross-site requests).
"""
from __future__ import annotations

import json
import mimetypes
import shutil
import threading
import tomllib
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .batch import (BATCH_VERSION, BatchError, default_wafer, dies_on_wafer, load_failure_modes,
                    load_meta_fields, migrate, new_batch, normalize, set_result)
from .config import ConfigError, config_from_dict
from .extract import extract, summary, write_outputs
from .extract.listing import describe
from .extract.suggest import suggest_config, to_toml
from .workspace import Workspace, WorkspaceError, new_id, now, read_json

APP_DIR = Path(__file__).resolve().parent.parent / "app"
PKG_ROOT = Path(__file__).resolve().parent.parent
MAX_JSON = 50 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


class App:
    """Request-independent logic, so it can be tested without HTTP."""

    def __init__(self, workspace: Workspace, defaults_path: Path | None = None):
        self.ws = workspace
        self.defaults_path = defaults_path
        self._tables: dict[str, tuple[float, dict]] = {}
        self._extract_lock = threading.Lock()

    # ---- helpers ----
    def defaults(self) -> dict:
        if self.defaults_path and self.defaults_path.exists():
            return tomllib.loads(self.defaults_path.read_text(encoding="utf-8"))
        return {}

    def table(self, rid: str) -> dict:
        p = self.ws.device_table_path(rid)
        if not p.exists():
            raise ApiError(404, "reticle has not been extracted yet")
        mt = p.stat().st_mtime
        cached = self._tables.get(rid)
        if not cached or cached[0] != mt:
            self._tables[rid] = (mt, read_json(p))
        return self._tables[rid][1]

    def settings(self) -> dict:
        return {"version": __version__, "batch_version": BATCH_VERSION,
                "failure_modes": load_failure_modes(), "meta_fields": load_meta_fields(),
                "default_wafer": default_wafer()}

    # ---- reticles ----
    def reticle_detail(self, rid: str) -> dict:
        d = self.ws.reticle_dir(rid)
        meta = self.ws.reticle_meta(rid)
        cfg = self.ws.config_path(rid)
        listing = d / "listing.txt"
        summ = d / "summary.txt"
        return {"meta": meta,
                "config": cfg.read_text(encoding="utf-8") if cfg.exists() else "",
                "listing": listing.read_text(encoding="utf-8") if listing.exists() else "",
                "summary": summ.read_text(encoding="utf-8") if summ.exists() else ""}

    def upload_reticle(self, filename: str, name: str, stream, length: int) -> dict:
        meta = self.ws.add_reticle(filename, stream, length, name)
        rid = meta["id"]
        d = self.ws.reticle_dir(rid)
        path = self.ws.reticle_file(rid)
        try:
            (d / "listing.txt").write_text(describe(str(path), depth=4, max_children=40), encoding="utf-8")
            self.ws.config_path(rid).write_text(to_toml(suggest_config(str(path), self.defaults())), encoding="utf-8")
        except Exception as e:  # unreadable layout: remove the upload again
            shutil.rmtree(d, ignore_errors=True)
            raise ApiError(400, f"could not read layout: {e}") from e
        return self.reticle_detail(rid)

    def save_config(self, rid: str, text: str) -> dict:
        try:
            config_from_dict(tomllib.loads(text))
        except (tomllib.TOMLDecodeError, ConfigError, TypeError, KeyError) as e:
            raise ApiError(400, f"config error: {e}") from e
        self.ws.config_path(rid).write_text(text, encoding="utf-8")
        return self.reticle_detail(rid)

    def run_extract(self, rid: str) -> dict:
        from .config import load_config
        d = self.ws.reticle_dir(rid)
        with self._extract_lock:
            try:
                cfg = load_config(self.ws.config_path(rid))
                geometry: dict = {}
                table = extract(self.ws.reticle_file(rid), cfg, geometry)
            except (ConfigError, ValueError, tomllib.TOMLDecodeError) as e:
                raise ApiError(400, f"extraction failed: {e}") from e
            write_outputs(table, d, geometry=geometry)
            text = summary(table)
            (d / "summary.txt").write_text(text, encoding="utf-8")
            meta = self.ws.reticle_meta(rid)
            meta["extracted"] = {"created": table["created"], "n_devices": len(table["devices"]),
                                 "gate_sha256": table["source"]["gate_sha256"]}
            self.ws.save_reticle_meta(rid, meta)
        return self.reticle_detail(rid)

    def delete_reticle(self, rid: str) -> None:
        users = [b["id"] for b in self.ws.list_batches() if b["reticle"].get("id") == rid]
        if users:
            raise ApiError(409, f"reticle is used by {len(users)} batch(es): {', '.join(users[:5])}")
        shutil.rmtree(self.ws.reticle_dir(rid))
        self._tables.pop(rid, None)

    # ---- batches ----
    def create_batch(self, body: dict) -> dict:
        rid = body.get("reticle_id")
        table = self.table(rid)
        b = new_batch(self.ws.reticle_meta(rid), table, body.get("meta"), body.get("wafer"), body.get("splits"))
        return self.ws.save_batch(b)

    def update_batch(self, bid: str, body: dict) -> dict:
        b = self.ws.load_batch(bid)
        for k in ("meta", "wafer", "splits", "die_notes"):
            if k in body:
                b[k] = body[k]
        normalize(b)
        dies = {f"{c},{r}" for c, r in b["wafer"]["dies"]}
        orphan = [k for k in b["results"] if k not in dies]
        if orphan and not body.get("drop_orphan_results"):
            raise ApiError(409, f"dies {', '.join(orphan)} have results but are no longer in the batch; "
                                "re-add them or confirm dropping their results")
        for k in orphan:
            b["results"].pop(k)
        return self.ws.save_batch(b)

    def record(self, bid: str, body: dict) -> dict:
        b = self.ws.load_batch(bid)
        table = self.table(b["reticle"]["id"])
        ids = {d["id"] for d in table["devices"]}
        statuses = {m["id"] for m in load_failure_modes()}
        col, row = body["die"]
        for u in body.get("updates", []):
            set_result(b, int(col), int(row), u["device_id"], u.get("status"), statuses, ids)
        self.ws.save_batch(b)
        return {"modified": b["modified"], "die": f"{col},{row}", "results": b["results"].get(f"{col},{row}", {})}

    def import_batch(self, doc: dict) -> dict:
        b = migrate(doc)
        rid = b.get("reticle", {}).get("id")
        try:
            self.ws.reticle_dir(rid)
        except WorkspaceError:
            raise ApiError(400, f"batch refers to reticle {rid!r}, which is not in this workspace") from None
        if self.ws.batch_path(b["id"]).exists():
            b["id"] = new_id(b["id"].rsplit("-", 3)[0] + "-import")
        return self.ws.save_batch(b)

    def delete_batch(self, bid: str) -> None:
        p = self.ws.batch_path(bid)
        if not p.exists():
            raise WorkspaceError(f"no batch {bid!r}")
        trash = self.ws.batches_dir / "trash"
        trash.mkdir(exist_ok=True)
        p.replace(trash / f"{bid}.{now().replace(':', '')}.json")


class Handler(BaseHTTPRequestHandler):
    server_version = f"mty/{__version__}"
    app: App  # set on the subclass by make_server

    def log_message(self, fmt, *args):  # quieter console
        if not self.path.startswith("/app/"):
            super().log_message(fmt, *args)

    # ---- plumbing ----
    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _check_origin(self) -> None:
        if self.headers.get("Host", "") not in self._allowed_hosts():
            raise ApiError(403, "bad Host header")
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc not in self._allowed_hosts():
            raise ApiError(403, "cross-origin request refused")

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200, extra: dict | None = None) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8", extra)

    def _length(self) -> int:
        return int(self.headers.get("Content-Length") or 0)

    def _body_json(self):
        n = self._length()
        if n > MAX_JSON:
            raise ApiError(413, "request too large")
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError as e:
            raise ApiError(400, f"invalid JSON: {e}") from e

    def _body_text(self) -> str:
        n = self._length()
        if n > MAX_JSON:
            raise ApiError(413, "request too large")
        return self.rfile.read(n).decode("utf-8")

    def _dispatch(self, method: str) -> None:
        try:
            self._check_origin()
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            self._route(method, parts, q)
        except ApiError as e:
            self._json({"error": str(e)}, e.status)
        except (WorkspaceError, BatchError, ConfigError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # pragma: no cover - surfaced to the UI
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # ---- routes ----
    def _static(self, rel: str) -> None:
        p = (APP_DIR / rel).resolve()
        if APP_DIR not in p.parents or not p.is_file():
            raise ApiError(404, "not found")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, p.read_bytes(), ctype)

    def _route(self, m: str, p: list[str], q: dict) -> None:
        app = self.app
        if m == "GET" and not p:
            return self._static("index.html")
        if m == "GET" and p[0] == "app":
            return self._static("/".join(p[1:]))
        if not p or p[0] != "api":
            raise ApiError(404, "not found")
        p = p[1:]
        ws = app.ws

        if p == ["settings"] and m == "GET":
            return self._json(app.settings())
        if p == ["wafer", "dies"] and m == "POST":
            body = self._body_json()
            return self._json(dies_on_wafer(body["wafer"], body.get("field_w_mm"), body.get("field_h_mm")))

        if p[:1] == ["reticles"]:
            if len(p) == 1 and m == "GET":
                return self._json(ws.list_reticles())
            if len(p) == 1 and m == "POST":
                return self._json(app.upload_reticle(q.get("filename", ""), q.get("name", ""), self.rfile, self._length()), 201)
            rid = p[1]
            rest = p[2:]
            if not rest and m == "GET":
                return self._json(app.reticle_detail(rid))
            if not rest and m == "DELETE":
                app.delete_reticle(rid)
                return self._json({"deleted": rid})
            if rest == ["config"] and m == "PUT":
                return self._json(app.save_config(rid, self._body_text()))
            if rest == ["extract"] and m == "POST":
                return self._json(app.run_extract(rid))
            if rest == ["devices"] and m == "GET":
                return self._json(app.table(rid))
            if rest == ["geometry"] and m == "GET":
                p_geo = ws.reticle_dir(rid) / "geometry.json"
                if not p_geo.exists():
                    raise ApiError(404, "no geometry; re-extract the reticle")
                return self._send(200, p_geo.read_bytes(), "application/json; charset=utf-8")
            if rest == ["devices.csv"] and m == "GET":
                app.table(rid)
                data = (ws.reticle_dir(rid) / "device_table.csv").read_bytes()
                return self._send(200, data, "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{rid}_devices.csv"'})

        if p[:1] == ["batches"]:
            if len(p) == 1 and m == "GET":
                return self._json(ws.list_batches())
            if len(p) == 1 and m == "POST":
                return self._json(app.create_batch(self._body_json()), 201)
            if p[1:] == ["import"] and m == "POST":
                return self._json(app.import_batch(self._body_json()), 201)
            bid = p[1]
            rest = p[2:]
            if not rest and m == "GET":
                return self._json(ws.load_batch(bid))
            if not rest and m == "PUT":
                return self._json(app.update_batch(bid, self._body_json()))
            if not rest and m == "DELETE":
                app.delete_batch(bid)
                return self._json({"deleted": bid})
            if rest == ["results"] and m == "POST":
                return self._json(app.record(bid, self._body_json()))
            if rest == ["export"] and m == "GET":
                return self._json(ws.load_batch(bid), extra={
                    "Content-Disposition": f'attachment; filename="{bid}.batch.json"'})
        raise ApiError(404, "not found")


def make_server(workspace: str | Path, port: int = 8765, defaults: str | Path | None = None) -> ThreadingHTTPServer:
    app = App(Workspace(workspace), Path(defaults) if defaults else None)
    handler = type("BoundHandler", (Handler,), {"app": app})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve(workspace: str | Path, port: int = 8765, open_browser: bool = True,
          defaults: str | Path | None = None) -> None:
    srv = make_server(workspace, port, defaults)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"mty {__version__} serving {url}  (workspace: {Path(workspace).resolve()})")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


DEFAULT_WORKSPACE = PKG_ROOT / "local" / "workspace"
DEFAULT_DEFAULTS = PKG_ROOT / "local" / "defaults.toml"
