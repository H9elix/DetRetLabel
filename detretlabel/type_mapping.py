"""Optional display-only category names; detection identities always keep raw values."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def category_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


@dataclass(frozen=True)
class TypeMapping:
    entries: dict[str, str] = field(default_factory=dict)
    path: Path | None = None

    def display(self, category: Any) -> str | None:
        if category is None:
            return None
        key = category_text(category)
        return self.entries.get(key, key)

    @classmethod
    def load(cls, path: Path) -> TypeMapping:
        entries = {}
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"[:：]", line, maxsplit=1)
            if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                raise ValueError(f"第 {number} 行无效，请使用 代号:显示名称，每行一条。")
            key, value = (part.strip() for part in parts)
            if key in entries:
                raise ValueError(f"第 {number} 行的种类代号重复：{key}")
            entries[key] = value
        if not entries:
            raise ValueError("type-mapping 文件没有有效映射；如需恢复原始种类，请点击清除。")
        return cls(entries, path.resolve())

    @classmethod
    def from_preferences(cls, entries: str, path: str = "") -> TypeMapping:
        values = json.loads(entries)
        if not isinstance(values, dict) or any(not isinstance(k, str) or not k.strip() or
                not isinstance(v, str) or not v.strip() for k, v in values.items()):
            raise ValueError("保存的种类映射格式无效。")
        return cls(values, Path(path) if path else None)
