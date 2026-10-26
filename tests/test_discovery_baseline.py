"""Recovery and isolation invariants needed before continuous research."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from test_driver import _GOOD_SPEC, _fake_res, _row

from kilimanjaro.data.root import data_root
from kilimanjaro.driver import ledger_io
from kilimanjaro.driver.loop import process_spec


def test_failed_attempt_retries_and_negative_completion_deduplicates(tmp_path):
    ledger = tmp_path / "events.jsonl"
    args = (_GOOD_SPEC, "2025-07-01", "2026-06-30")
    kwargs = {"results_root": tmp_path, "ledger_path": ledger}
    with patch(
        "kilimanjaro.driver.loop.run_spec", side_effect=RuntimeError("interrupted")
    ):
        assert process_spec(*args, **kwargs)["status"] == "error"
    fp = ledger_io.scan_fingerprint(*args)
    assert not ledger_io.already_processed(fp, ledger)
    with patch(
        "kilimanjaro.driver.loop.run_spec",
        return_value=(_fake_res([_row(False)]), tmp_path, "s"),
    ) as run:
        assert process_spec(*args, **kwargs)["status"] == "scan-failed"
        assert process_spec(*args, **kwargs)["status"] == "skipped-duplicate"
        assert run.call_count == 1


def test_explicit_root_never_resolves_shared_root(tmp_path):
    with patch("hase.bronze.data_root", side_effect=AssertionError("shared root")):
        assert data_root(tmp_path) == tmp_path
        from kilimanjaro.data.feature_frame import available_features

        assert available_features("GMO", "BTC_JPY", tmp_path) == {}


def test_cli_help_is_read_only_and_available():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "src/cli.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "factory" in result.stdout
