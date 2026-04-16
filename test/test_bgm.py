from __future__ import annotations

from pathlib import Path

from videocut.bgm import resolve_bgm_dir


def test_resolve_bgm_dir_defaults_to_repo_input_bgm(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BGM_DIR", raising=False)
    result = resolve_bgm_dir(tmp_path)
    assert result == tmp_path / "input" / "bgm"


def test_resolve_bgm_dir_prefers_env_and_resolves_relative_to_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BGM_DIR", "runtime/bgm")
    result = resolve_bgm_dir(tmp_path, "custom/bgm")
    assert result == tmp_path / "runtime" / "bgm"


def test_resolve_bgm_dir_uses_configured_relative_path_without_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BGM_DIR", raising=False)
    result = resolve_bgm_dir(tmp_path, "custom/bgm")
    assert result == tmp_path / "custom" / "bgm"
