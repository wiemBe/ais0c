"""Command-line behavior of the deployment migration command."""

import pytest

import ais0c_activities.deploy as deploy
from ais0c_worker import main as worker_main


def test_migrate_check_returns_3_when_behind(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(deploy, "database_revision", lambda _url: ("0011", "0012"))

    status = worker_main.main(["migrate", "--check"], {"AIS0C_DATABASE_URL": "test-url"})

    assert status == 3
    assert capsys.readouterr().out == "behind: 0011 -> 0012\n"


def test_migrate_check_returns_0_at_head(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(deploy, "database_revision", lambda _url: ("0012", "0012"))

    status = worker_main.main(["migrate", "--check"], {"AIS0C_DATABASE_URL": "test-url"})

    assert status == 0
    assert capsys.readouterr().out == "at head 0012\n"


def test_migrate_exits_1_on_a_database_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(_url: str) -> str:
        raise RuntimeError("synthetic database failure")

    monkeypatch.setattr(deploy, "migrate_to_head", fail)

    status = worker_main.main(["migrate"], {"AIS0C_DATABASE_URL": "test-url"})

    assert status == 1
    assert capsys.readouterr().err == "error: database migration failed (RuntimeError)\n"


def test_migrate_without_database_url_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = worker_main.main(["migrate"], {})

    assert status == 2
    assert capsys.readouterr().err == "error: AIS0C_DATABASE_URL is not set\n"
