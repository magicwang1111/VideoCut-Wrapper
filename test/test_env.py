from __future__ import annotations

import os

from videocut.env import load_env_file, load_project_env


def test_load_env_file_reads_explicit_file_without_overriding_existing_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("VIDEOCUT_FILE_VALUE", raising=False)
    monkeypatch.setenv("VIDEOCUT_FILE_EXISTING_VALUE", "from-env")

    env_path = tmp_path / "videocut.env"
    env_path.write_text(
        "\n".join(
            [
                "VIDEOCUT_FILE_VALUE=from-file",
                "export VIDEOCUT_FILE_EXISTING_VALUE=from-file",
            ]
        ),
        encoding="utf-8",
    )

    assert load_env_file(env_path) is True

    assert os.environ["VIDEOCUT_FILE_VALUE"] == "from-file"
    assert os.environ["VIDEOCUT_FILE_EXISTING_VALUE"] == "from-env"


def test_load_project_env_reads_dotenv_without_overriding_existing_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("VIDEOCUT_TEST_VALUE", raising=False)
    monkeypatch.delenv("VIDEOCUT_EXPORTED_VALUE", raising=False)
    monkeypatch.setenv("VIDEOCUT_EXISTING_VALUE", "from-env")

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "# comments are ignored",
                "VIDEOCUT_TEST_VALUE=from-file",
                "export VIDEOCUT_EXPORTED_VALUE=from-export",
                "VIDEOCUT_EXISTING_VALUE=from-file",
            ]
        ),
        encoding="utf-8",
    )

    load_project_env(tmp_path)

    assert os.environ["VIDEOCUT_TEST_VALUE"] == "from-file"
    assert os.environ["VIDEOCUT_EXPORTED_VALUE"] == "from-export"
    assert os.environ["VIDEOCUT_EXISTING_VALUE"] == "from-env"
