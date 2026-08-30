import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from dronevis.config import load_config
from dronevis.parse.pipeline import Parser

KYIV = (50.4501, 30.5234)


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def parser(cfg):
    return Parser(cfg)


@pytest.fixture
def parse(parser):
    def _p(text, channel=None):
        return parser.parse(text, channel=channel, area_center=KYIV)

    return _p
