"""Config loading. All behavior flows from config.yaml — no magic constants."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Config:
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        with open(path) as f:
            raw = yaml.safe_load(f)
        return cls(raw=raw or {})

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def content_hash(self) -> str:
        """Stable hash of config for stamping into backtest results."""
        blob = json.dumps(self.raw, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]
