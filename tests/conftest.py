import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from royaltyledger.service import RoyaltyService, build_data  # noqa: E402


@pytest.fixture(scope="session")
def small_data(tmp_path_factory) -> Path:
    """A small generated catalog (8 artists) with every report format, shared by the slower tests."""
    d = tmp_path_factory.mktemp("rl") / "data"
    build_data(d, seed=3, n_artists=8)
    return d


@pytest.fixture(scope="session")
def svc(small_data) -> RoyaltyService:
    return RoyaltyService(small_data)
