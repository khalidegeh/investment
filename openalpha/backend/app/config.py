"""Settings. Environment only: nothing here is a secret and no key is required."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    cache_path: Path = Path("data/openalpha_cache.sqlite")
    cache_ttl_seconds: float = 900.0          # 15 minutes
    contact_email: str | None = None           # required for SEC EDGAR
    rotate_user_agent: bool = False            # opt-in, never applies to EDGAR
    portfolio_path: Path = Path("config/portfolio_config.json")   # local only, git-ignored
    export_dir: Path = Path("exports")
    alerts_state_path: Path = Path("config/alerts_state.json")   # local only, git-ignored

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            cache_path=Path(os.environ.get("OPENALPHA_CACHE_PATH", "data/openalpha_cache.sqlite")),
            cache_ttl_seconds=float(os.environ.get("OPENALPHA_CACHE_TTL", "900")),
            contact_email=os.environ.get("OPENALPHA_CONTACT") or None,
            rotate_user_agent=os.environ.get("OPENALPHA_ROTATE_UA", "") == "1",
            portfolio_path=Path(
                os.environ.get("OPENALPHA_PORTFOLIO", "config/portfolio_config.json")
            ),
            export_dir=Path(os.environ.get("OPENALPHA_EXPORT_DIR", "exports")),
            alerts_state_path=Path(
                os.environ.get("OPENALPHA_ALERTS_STATE", "config/alerts_state.json")
            ),
        )
