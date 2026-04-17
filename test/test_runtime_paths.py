from __future__ import annotations

from pathlib import Path

from videocut import runtime_paths


def test_resolve_runtime_path_uses_default_when_env_missing(tmp_path) -> None:
    resolved = runtime_paths.resolve_runtime_path(None, tmp_path / "data" / "tasks.db", root_dir=tmp_path)
    assert resolved == (tmp_path / "data" / "tasks.db").resolve()


def test_resolve_runtime_path_maps_container_paths_back_to_project_root_on_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_paths, "_is_windows", lambda: True)

    db_path = runtime_paths.resolve_runtime_path("/srv/videocut/data/tasks.db", tmp_path / "data" / "tasks.db", root_dir=tmp_path)
    pipelines_path = runtime_paths.resolve_runtime_path("/app/pipelines", tmp_path / "pipelines", root_dir=tmp_path)

    assert db_path == (tmp_path / "data" / "tasks.db").resolve()
    assert pipelines_path == (tmp_path / "pipelines").resolve()


def test_resolve_runtime_path_keeps_non_windows_resolution_rules(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_paths, "_is_windows", lambda: False)

    raw = "/srv/videocut/data/tasks.db"
    resolved = runtime_paths.resolve_runtime_path(raw, tmp_path / "data" / "tasks.db", root_dir=tmp_path)

    assert resolved == Path(raw).resolve()
    assert resolved != (tmp_path / "data" / "tasks.db").resolve()
