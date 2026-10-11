"""Local workspace on disk (everything here is user data and stays local).

layout:
    <root>/reticles/<rid>/reticle.json      metadata (name, file, created, extracted)
    <root>/reticles/<rid>/<file>.gds|oas    uploaded layout
    <root>/reticles/<rid>/config.toml       extraction config
    <root>/reticles/<rid>/device_table.json/.csv
    <root>/batches/<bid>.json               one batch (wafer inspection) per file
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

LAYOUT_SUFFIXES = (".gds", ".gds2", ".gdsii", ".oas", ".oasis")
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_lock = threading.RLock()


class WorkspaceError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_id(i: str) -> str:
    if not _ID.match(i or "") or i in (".", ".."):
        raise WorkspaceError(f"invalid id {i!r}")
    return i


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-.")[:40]
    return s or "item"


def new_id(prefix: str) -> str:
    return f"{slug(prefix)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def write_json(path: Path, data) -> None:
    """Atomic write: never leaves a half-written file behind."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.reticles_dir = self.root / "reticles"
        self.batches_dir = self.root / "batches"
        self.reticles_dir.mkdir(parents=True, exist_ok=True)
        self.batches_dir.mkdir(parents=True, exist_ok=True)

    # ---- reticles ----
    def reticle_dir(self, rid: str) -> Path:
        d = self.reticles_dir / check_id(rid)
        if not (d / "reticle.json").exists():
            raise WorkspaceError(f"no reticle {rid!r}")
        return d

    def reticle_meta(self, rid: str) -> dict:
        return read_json(self.reticle_dir(rid) / "reticle.json")

    def save_reticle_meta(self, rid: str, meta: dict) -> None:
        with _lock:
            write_json(self.reticle_dir(rid) / "reticle.json", meta)

    def list_reticles(self) -> list[dict]:
        out = []
        for d in sorted(self.reticles_dir.iterdir()):
            if (d / "reticle.json").exists():
                out.append(read_json(d / "reticle.json"))
        return sorted(out, key=lambda m: m.get("created", ""), reverse=True)

    def add_reticle(self, filename: str, stream, length: int, name: str = "") -> dict:
        fname = Path(filename).name
        if not fname.lower().endswith(LAYOUT_SUFFIXES):
            raise WorkspaceError(f"expected a layout file ({', '.join(LAYOUT_SUFFIXES)}), got {fname!r}")
        rid = new_id(name or Path(fname).stem)
        d = self.reticles_dir / rid
        d.mkdir(parents=True)
        target = d / slug(fname)
        remaining = length
        with open(target, "wb") as f:
            while remaining > 0:
                chunk = stream.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        if remaining:
            raise WorkspaceError("upload incomplete")
        meta = {"id": rid, "name": name or Path(fname).stem, "file": target.name,
                "size": length, "created": now(), "extracted": None}
        write_json(d / "reticle.json", meta)
        return meta

    def reticle_file(self, rid: str) -> Path:
        return self.reticle_dir(rid) / self.reticle_meta(rid)["file"]

    def config_path(self, rid: str) -> Path:
        return self.reticle_dir(rid) / "config.toml"

    def device_table_path(self, rid: str) -> Path:
        return self.reticle_dir(rid) / "device_table.json"

    # ---- batches ----
    def batch_path(self, bid: str) -> Path:
        return self.batches_dir / f"{check_id(bid)}.json"

    def list_batches(self) -> list[dict]:
        out = []
        for p in self.batches_dir.glob("*.json"):
            try:
                b = read_json(p)
            except (OSError, json.JSONDecodeError):
                continue
            n = sum(len(v) for v in b.get("results", {}).values())
            out.append({"id": b["id"], "meta": b.get("meta", {}), "reticle": b.get("reticle", {}),
                        "modified": b.get("modified"), "n_results": n,
                        "n_dies": len(b.get("wafer", {}).get("dies", []))})
        return sorted(out, key=lambda b: b.get("modified") or "", reverse=True)

    def load_batch(self, bid: str) -> dict:
        p = self.batch_path(bid)
        if not p.exists():
            raise WorkspaceError(f"no batch {bid!r}")
        return read_json(p)

    def save_batch(self, batch: dict) -> dict:
        with _lock:
            batch["modified"] = now()
            write_json(self.batch_path(batch["id"]), batch)
        return batch
