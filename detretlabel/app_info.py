"""Application identity and build-time stamp, shared by the help UI."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import __author__, __version__

RESOURCE_ROOT = Path(__file__).resolve().parents[1]
AUTHOR = __author__
VERSION = __version__


def build_label() -> str:
    try:
        value = json.loads(Path(__file__).with_name("build_info.json").read_text(encoding="utf-8-sig"))["build"]
        if isinstance(value, str) and re.fullmatch(r"Build\d{8}", value):
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return "构建信息不可用"


BUILD = build_label()
