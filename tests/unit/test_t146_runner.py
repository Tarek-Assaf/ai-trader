"""Focused tests for the resumable T-153 group runner."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "t146_runner", ROOT / "scripts/lab_t146_runner.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeProcess:
    def __init__(self, *, complete: bool) -> None:
        self.complete = complete
        self.returncode = 0 if complete else None
        self.pid = -1
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 143

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_next_group_skips_completed_and_partial(tmp_path: Path) -> None:
    module = load_runner()
    progress = module._empty_progress()
    progress["groups"]["NSXUSD/60"]["status"] = "complete"
    progress["groups"]["XAUUSD/60"]["status"] = "partial"
    assert module.next_group(progress) == ("NSXUSD", 5)


def test_partial_marking_on_time_limit(tmp_path: Path) -> None:
    module = load_runner()
    clock = FakeClock()
    output = tmp_path / "output"
    child_progress = output / "groups" / "nsxusd_60m" / "progress.json"
    child_progress.parent.mkdir(parents=True)
    child_progress.write_text(
        json.dumps(
            {
                "queries_done": 2,
                "elapsed_seconds": 2.0,
                "library_windows": 123,
                "oos_queries": 7,
                "library_windows_by_length": {"30": 50, "60": 40, "120": 33},
                "oos_windows_by_length": {"30": 3, "60": 2, "120": 2},
            }
        )
    )
    process = FakeProcess(complete=False)

    def popen(*args, **kwargs):
        return process

    record = module.run_group_with_timeout(
        instrument="NSXUSD",
        timeframe=60,
        lake=tmp_path / "lake",
        output=output,
        holdout_end="2024-02-01T00:00:00+00:00",
        time_limit_seconds=2,
        clock=clock,
        sleep=clock.sleep,
        popen_factory=popen,
    )
    assert record["status"] == "partial"
    assert record["queries_done"] == 2
    assert record["library_windows"] == 123
    assert record["oos_queries"] == 7
    assert record["library_windows_by_length"] == {"30": 50, "60": 40, "120": 33}
    assert process.terminated
    assert clock.now >= 2


def test_time_limit_stops_child_and_preserves_exit_code(tmp_path: Path) -> None:
    module = load_runner()
    clock = FakeClock()
    process = FakeProcess(complete=False)

    record = module.run_group_with_timeout(
        instrument="XAUUSD",
        timeframe=1,
        lake=tmp_path / "lake",
        output=tmp_path / "output",
        holdout_end="2024-02-01T00:00:00+00:00",
        time_limit_seconds=1,
        clock=clock,
        sleep=clock.sleep,
        popen_factory=lambda *args, **kwargs: process,
    )
    assert record["status"] == "partial"
    assert record["exit_code"] == 143
    assert process.terminated
