from __future__ import annotations

from pathlib import Path

import pytest

from lineguard.database import initialize_lineguard


@pytest.fixture
def lineguard_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "lineguard"
    monkeypatch.setenv("LINEGUARD_DATA_DIR", str(data_dir))
    monkeypatch.setenv(
        "LINEGUARD_DATABASE_URL",
        f"sqlite:///{(data_dir / 'lineguard.db').as_posix()}",
    )
    monkeypatch.setenv("LINEGUARD_USE_LLM", "false")
    initialize_lineguard()
    return data_dir
