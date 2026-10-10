"""Load and validate the reticle extraction config (TOML)."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_VERSION = 1


@dataclass
class ReticleSide:
    top_cell: str
    layer: int
    datatype: int = 0
    cell_suffix: str = ""
    file: str = ""
    enabled: bool = True

    def stem(self, cell_name: str) -> str:
        """Cell name without the reticle suffix."""
        if self.cell_suffix and cell_name.endswith(self.cell_suffix):
            return cell_name[: -len(self.cell_suffix)]
        return cell_name


@dataclass
class Group:
    name: str
    split: bool = False


@dataclass
class Geometry:
    ignore_triangles: bool = True
    min_finger_aspect: float = 5.0
    long_line_um: float = 100.0
    dense_space_factor: float = 3.0


@dataclass
class Config:
    gate: ReticleSide
    sd: ReticleSide | None
    groups: list[Group]
    text_patterns: list[re.Pattern]
    split_exclude: list[re.Pattern]
    own_shapes_site: bool = True
    geometry: Geometry = field(default_factory=Geometry)
    min_coverage: float = 0.5
    raw: dict = field(default_factory=dict)

    def is_text(self, cell_name: str) -> bool:
        return any(p.search(cell_name) for p in self.text_patterns)

    def is_split_excluded(self, cell_name: str) -> bool:
        return any(p.search(cell_name) for p in self.split_exclude)


class ConfigError(ValueError):
    pass


def _side(d: dict, key: str) -> ReticleSide:
    try:
        return ReticleSide(
            top_cell=d["top_cell"],
            layer=int(d["layer"]),
            datatype=int(d.get("datatype", 0)),
            cell_suffix=d.get("cell_suffix", ""),
            file=d.get("file", ""),
            enabled=bool(d.get("enabled", True)),
        )
    except KeyError as e:
        raise ConfigError(f"[{key}] is missing {e}") from None


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return config_from_dict(raw)


def config_from_dict(raw: dict) -> Config:
    version = raw.get("config_version", CONFIG_VERSION)
    if version != CONFIG_VERSION:
        raise ConfigError(f"unsupported config_version {version} (expected {CONFIG_VERSION})")
    if "gate" not in raw:
        raise ConfigError("config needs a [gate] section")
    gate = _side(raw["gate"], "gate")
    sd = _side(raw["sd"], "sd") if "sd" in raw else None
    if sd is not None and not sd.enabled:
        sd = None
    groups = [Group(name=g["name"], split=bool(g.get("split", False))) for g in raw.get("groups", [])]
    if not groups:
        raise ConfigError("config needs at least one [[groups]] entry")
    geo = Geometry(**raw.get("geometry", {}))
    split = raw.get("split", {})
    return Config(
        gate=gate,
        sd=sd,
        groups=groups,
        text_patterns=[re.compile(p) for p in raw.get("text", {}).get("cell_patterns", [])],
        split_exclude=[re.compile(p) for p in split.get("exclude_patterns", [])],
        own_shapes_site=bool(split.get("own_shapes_site", True)),
        geometry=geo,
        min_coverage=float(raw.get("pairing", {}).get("min_coverage", 0.5)),
        raw=raw,
    )
