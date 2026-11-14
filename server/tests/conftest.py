from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mailstudio.app import create_app
from mailstudio.auth import create_key, parse_scopes
from mailstudio.config import Settings
from mailstudio.db import connect, transaction


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:  # runs startup (migrations)
        yield test_client


@pytest.fixture
def make_key(settings: Settings, client: TestClient) -> Callable[[str], dict[str, str]]:
    def make(scopes: str = "read write") -> dict[str, str]:
        conn = connect(settings.database_path)
        with transaction(conn):
            key = create_key(conn, "test", parse_scopes(scopes))
        conn.close()
        return {"Authorization": f"Bearer {key.token}"}

    return make


@pytest.fixture
def auth(make_key: Callable[[str], dict[str, str]]) -> dict[str, str]:
    return make_key("read write")


CONSENT = {"source": "Signed up on the website"}
