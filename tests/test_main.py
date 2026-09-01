import sys
from pathlib import Path

import pytest

import autocomplete.main as main_module


def fail_if_called(*args: object, **kwargs: object) -> None:
    pytest.fail(f"unexpected call with arguments {args!r} and {kwargs!r}")


def prevent_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_load_snapshot", fail_if_called)
    monkeypatch.setattr(main_module, "SearchEngine", fail_if_called)
    monkeypatch.setattr(main_module, "configure_default_engine", fail_if_called)
    monkeypatch.setattr(main_module, "run_cli", fail_if_called)


def test_snapshot_argument_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    prevent_startup(monkeypatch)

    with pytest.raises(SystemExit) as error:
        main_module.main([])

    assert error.value.code == 2


def test_successful_startup_loads_once_and_configures_before_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {1: object()}
    index = object()
    engine = object()
    events: list[tuple[object, ...]] = []

    def load_snapshot(snapshot_path: Path) -> tuple[dict[int, object], object]:
        events.append(("load", snapshot_path))
        return records, index

    def create_engine(
        supplied_records: dict[int, object],
        supplied_index: object,
    ) -> object:
        events.append(("construct", supplied_records, supplied_index))
        return engine

    def configure(supplied_engine: object) -> None:
        events.append(("configure", supplied_engine))

    def run_cli() -> None:
        events.append(("cli",))
        assert [event[0] for event in events].count("load") == 1

    monkeypatch.setattr(main_module, "_load_snapshot", load_snapshot)
    monkeypatch.setattr(main_module, "SearchEngine", create_engine)
    monkeypatch.setattr(main_module, "configure_default_engine", configure)
    monkeypatch.setattr(main_module, "run_cli", run_cli)

    status = main_module.main(["--snapshot", "data/snapshots/current"])

    expected_path = Path("data/snapshots/current")
    assert status == 0
    assert events == [
        ("load", expected_path),
        ("construct", records, index),
        ("configure", engine),
        ("cli",),
    ]
    assert isinstance(events[0][1], Path)


def test_load_snapshot_resolves_current_pointer(monkeypatch, tmp_path) -> None:
    from autocomplete import snapshot_loader
    from autocomplete.snapshot_pointer import activate_snapshot

    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    snapshot_id = "a" * 64
    snapshot = snapshot_root / snapshot_id
    snapshot.mkdir()
    activate_snapshot(snapshot_root, snapshot_id)
    calls = []
    expected = ({}, object())
    monkeypatch.setattr(
        snapshot_loader,
        "load_snapshot",
        lambda path: calls.append(path) or expected,
    )

    assert main_module._load_snapshot(snapshot_root / "current") == expected
    assert calls == [snapshot]


def test_direct_argv_is_used_without_reading_real_sys_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supplied_paths: list[Path] = []
    engine = object()
    monkeypatch.setattr(sys, "argv", ["program-without-required-argument"])
    monkeypatch.setattr(
        main_module,
        "_load_snapshot",
        lambda path: supplied_paths.append(path) or ({}, object()),
    )
    monkeypatch.setattr(main_module, "SearchEngine", lambda records, index: engine)
    monkeypatch.setattr(main_module, "configure_default_engine", lambda value: None)
    monkeypatch.setattr(main_module, "run_cli", lambda: None)

    status = main_module.main(["--snapshot", "/explicit/snapshot"])

    assert status == 0
    assert supplied_paths == [Path("/explicit/snapshot")]


def test_show_timing_flag_is_forwarded_after_successful_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = object()
    cli_calls: list[bool] = []
    monkeypatch.setattr(main_module, "_load_snapshot", lambda path: ({}, object()))
    monkeypatch.setattr(main_module, "SearchEngine", lambda records, index: engine)
    monkeypatch.setattr(main_module, "configure_default_engine", lambda value: None)
    monkeypatch.setattr(
        main_module,
        "run_cli",
        lambda *, show_timing: cli_calls.append(show_timing),
    )

    status = main_module.main(
        ["--snapshot", "data/snapshots/current", "--show-timing"]
    )

    assert status == 0
    assert cli_calls == [True]


@pytest.mark.parametrize(
    "loader_error",
    [
        FileNotFoundError("manifest.pb is missing"),
        ValueError("snapshot manifest is corrupt"),
    ],
)
def test_load_failure_reports_error_and_prevents_remaining_startup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    loader_error: Exception,
) -> None:
    load_calls: list[Path] = []

    def load_snapshot(snapshot_path: Path) -> None:
        load_calls.append(snapshot_path)
        raise loader_error

    monkeypatch.setattr(main_module, "_load_snapshot", load_snapshot)
    monkeypatch.setattr(main_module, "SearchEngine", fail_if_called)
    monkeypatch.setattr(main_module, "configure_default_engine", fail_if_called)
    monkeypatch.setattr(main_module, "run_cli", fail_if_called)

    status = main_module.main(["--snapshot", "broken/snapshot"])

    captured = capsys.readouterr()
    assert status != 0
    assert load_calls == [Path("broken/snapshot")]
    assert "snapshot" in captured.err.lower()
    assert "failed" in captured.err.lower()
    assert "broken/snapshot" in captured.err
    assert str(loader_error) in captured.err


def test_cli_failure_propagates_after_successful_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = object()
    monkeypatch.setattr(main_module, "_load_snapshot", lambda path: ({}, object()))
    monkeypatch.setattr(main_module, "SearchEngine", lambda records, index: engine)
    monkeypatch.setattr(main_module, "configure_default_engine", lambda value: None)

    def run_cli() -> None:
        raise RuntimeError("query processing failed")

    monkeypatch.setattr(main_module, "run_cli", run_cli)

    with pytest.raises(RuntimeError, match="query processing failed"):
        main_module.main(["--snapshot", "valid/snapshot"])
