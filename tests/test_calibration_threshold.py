"""Verify calibration-driven edge threshold for polymarket/bot.

Tests the four components from issue #291:
1. effective_threshold — calibration only raises, never lowers
2. compute_calibration — MAE + spread + safety = calibrated threshold
3. load/save_calibration — local state file
4. Agent integration — effective threshold used instead of config threshold
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CAL_PATH = REPO_ROOT / "polymarket" / "bot" / "scripts" / "calibration.py"
AGENT_PATH = REPO_ROOT / "polymarket" / "bot" / "scripts" / "agent.py"


def _load_calibration_module():
    """Load calibration.py in isolation."""
    spec = importlib.util.spec_from_file_location("calibration", CAL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cal():
    return _load_calibration_module()


# --- effective_threshold: only raises, never lowers ---


class TestEffectiveThreshold:

    def test_no_calibration_uses_config(self, cal) -> None:
        threshold, reason = cal.effective_threshold(0.08, None)
        assert threshold == 0.08
        assert "config" in reason

    def test_insufficient_data_uses_config(self, cal) -> None:
        threshold, reason = cal.effective_threshold(0.08, {"resolved_count": 30})
        assert threshold == 0.08
        assert "30/50" in reason

    def test_calibration_raises_threshold(self, cal) -> None:
        calibration_data = {
            "resolved_count": 100,
            "median_absolute_error": 0.14,
            "calibrated_threshold": 0.19,
        }
        threshold, reason = cal.effective_threshold(0.08, calibration_data)
        assert threshold == 0.19
        assert "calibrated" in reason

    def test_calibration_never_lowers_threshold(self, cal) -> None:
        """Critical safety rule: calibration can only raise, never lower."""
        calibration_data = {
            "resolved_count": 100,
            "median_absolute_error": 0.02,
            "calibrated_threshold": 0.05,  # Lower than config 0.08
        }
        threshold, reason = cal.effective_threshold(0.08, calibration_data)
        assert threshold == 0.08, "Calibration must never lower the threshold below config"
        assert "config" in reason


# --- compute_calibration ---


class TestComputeCalibration:

    def test_returns_none_below_minimum(self, cal) -> None:
        storage = MagicMock()
        storage.get_resolved_predictions.return_value = [
            {"predicted_fair_value": 0.7, "actual_probability": 1.0, "confidence": "medium"}
            for _ in range(30)
        ]
        result = cal.compute_calibration(storage)
        assert result is None

    def test_computes_mae_above_minimum(self, cal, tmp_path) -> None:
        # Temporarily redirect calibration file
        original = cal.CALIBRATION_FILE
        cal.CALIBRATION_FILE = tmp_path / "calibration.json"
        cal.STATE_DIR = tmp_path

        try:
            storage = MagicMock()
            # 60 predictions: predicted 0.6, actual was 1.0 → error = 0.4 each
            storage.get_resolved_predictions.return_value = [
                {"predicted_fair_value": 0.6, "actual_probability": 1.0, "confidence": "high"}
                for _ in range(60)
            ]
            result = cal.compute_calibration(storage)
            assert result is not None
            assert result["resolved_count"] == 60
            assert result["median_absolute_error"] == 0.4
            # calibrated = MAE(0.4) + spread(0.03) + safety(0.02) = 0.45
            assert result["calibrated_threshold"] == 0.45
            assert result["high_confidence_mae"] == 0.4
        finally:
            cal.CALIBRATION_FILE = original
            cal.STATE_DIR = original.parent

    def test_none_storage_returns_none(self, cal) -> None:
        assert cal.compute_calibration(None) is None


# --- load/save calibration ---


class TestCalibrationPersistence:

    def test_save_and_load(self, cal, tmp_path) -> None:
        original_file = cal.CALIBRATION_FILE
        original_dir = cal.STATE_DIR
        cal.CALIBRATION_FILE = tmp_path / "calibration.json"
        cal.STATE_DIR = tmp_path

        try:
            data = {
                "resolved_count": 100,
                "median_absolute_error": 0.14,
                "calibrated_threshold": 0.19,
            }
            cal.save_calibration(data)
            loaded = cal.load_calibration()
            assert loaded["resolved_count"] == 100
            assert loaded["calibrated_threshold"] == 0.19
        finally:
            cal.CALIBRATION_FILE = original_file
            cal.STATE_DIR = original_dir

    def test_load_missing_file_returns_none(self, cal, tmp_path) -> None:
        original = cal.CALIBRATION_FILE
        cal.CALIBRATION_FILE = tmp_path / "nonexistent.json"
        try:
            assert cal.load_calibration() is None
        finally:
            cal.CALIBRATION_FILE = original


# --- Agent integration ---


class TestAgentIntegration:

    def test_agent_uses_effective_threshold(self) -> None:
        """agent.py must use effective_mispricing_threshold, not mispricing_threshold,
        for the edge comparison in evaluate_opportunity."""
        source = AGENT_PATH.read_text(encoding="utf-8")
        assert "effective_mispricing_threshold" in source
        assert "import calibration" in source

    def test_agent_saves_predictions(self) -> None:
        """agent.py must call save_prediction in the LLM analysis loop."""
        source = AGENT_PATH.read_text(encoding="utf-8")
        assert "save_prediction" in source

    def test_agent_runs_post_scan_calibration(self) -> None:
        """agent.py must call run_post_scan_calibration after scan completes."""
        source = AGENT_PATH.read_text(encoding="utf-8")
        assert "run_post_scan_calibration" in source

    def test_effective_threshold_before_kelly(self) -> None:
        """Effective threshold check must appear before Kelly sizing."""
        source = AGENT_PATH.read_text(encoding="utf-8")
        eff_pos = source.find("effective_mispricing_threshold")
        kelly_pos = source.find("calculate_position_size")
        assert eff_pos > 0 and kelly_pos > 0
        assert eff_pos < kelly_pos
