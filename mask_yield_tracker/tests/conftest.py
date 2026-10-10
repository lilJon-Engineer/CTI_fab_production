import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mty.config import load_config  # noqa: E402
from mty.extract import extract  # noqa: E402
from mty.synthetic import SYNTHETIC_CONFIG, build  # noqa: E402


@pytest.fixture(scope="session")
def synthetic_gds(tmp_path_factory):
    return build(tmp_path_factory.mktemp("syn") / "synthetic.gds")


@pytest.fixture(scope="session")
def config():
    return load_config(SYNTHETIC_CONFIG)


@pytest.fixture(scope="session")
def table(synthetic_gds, config):
    return extract(synthetic_gds, config)


@pytest.fixture(scope="session")
def by_id(table):
    return {d["id"]: d for d in table["devices"]}
