"""Runtime configuration.

Every setting has a working default so `docker run` needs no environment
variables. Override with MAILSTUDIO_* variables where needed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# In a source checkout the shared section templates sit at the repository root;
# the Docker image copies them to /app/sections.
_REPO_SECTIONS = Path(__file__).resolve().parents[2] / "sections"
_REPO_WEB = Path(__file__).resolve().parents[2] / "web" / "dist"


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    """Holds the database, backups and uploaded assets. Mounted as a volume in Docker."""

    base_url: str = "http://localhost:8080"
    """Public address of this install, used in unsubscribe links. Set by the installer."""

    sections_dir: Path = field(default=_REPO_SECTIONS)

    web_dir: Path = field(default=_REPO_WEB)
    """The built web app (`web/dist`); the Docker image copies it to /app/web."""

    gallery_url: str = "https://mailstudio.email/gallery/index.json"
    """The online template gallery's index; empty turns the gallery off."""

    run_worker: bool = False
    """Run the sending worker inside the web process (on by default for `mailstudio serve`)."""

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "mailstudio.db"

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            data_dir=Path(os.environ.get("MAILSTUDIO_DATA_DIR", "./data")).resolve(),
            base_url=os.environ.get("MAILSTUDIO_BASE_URL", "http://localhost:8080").rstrip("/"),
            sections_dir=Path(os.environ.get("MAILSTUDIO_SECTIONS_DIR", str(_REPO_SECTIONS))).resolve(),
            web_dir=Path(os.environ.get("MAILSTUDIO_WEB_DIR", str(_REPO_WEB))).resolve(),
            gallery_url=os.environ.get("MAILSTUDIO_GALLERY_URL", "https://mailstudio.email/gallery/index.json").strip(),
            run_worker=os.environ.get("MAILSTUDIO_WORKER", "1") == "1",
        )
