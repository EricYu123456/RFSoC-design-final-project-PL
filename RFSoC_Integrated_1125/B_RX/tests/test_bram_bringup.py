"""Regression checks for accessing BRAM with stale PYNQ device metadata."""
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from webcam_benchmark import run_single_board
from webcam_board import HOST_SIGNATURE, PynqImageLinkIO, RESET, START


class BramBringupTests(unittest.TestCase):
    def test_clock_loss_allows_only_a_pending_reset(self):
        link = object.__new__(PynqImageLinkIO)
        link.health = Mock()
        link.health.read.return_value = HOST_SIGNATURE  # PS alive, RF clock stopped
        link.control = Mock()
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            link.apply_control(START)
        link.control.write.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            link.apply_control(RESET)
        link.control.write.assert_called_once_with(RESET, 0xffffffff)

    def test_cached_filename_cannot_bypass_download_or_rfdc_checks(self):
        board = Mock()
        board.check_rfdc.side_effect = TimeoutError("PLL unlocked")
        pynq = SimpleNamespace(PL=SimpleNamespace(bitfile_name="/tmp/ofdm_qam16.bit"))
        with tempfile.TemporaryDirectory() as out, patch.dict(sys.modules, pynq=pynq), \
                patch("webcam_benchmark.BoardRun", return_value=board):
            with self.assertRaisesRegex(TimeoutError, "PLL unlocked"):
                run_single_board(pynq.PL.bitfile_name, out)
        self.assertEqual(board.method_calls, [
            call.inspect(), call.program_clocks(), call.download_overlay(), call.check_ps_bus(),
            call.check_rfdc(), call.close(),
        ])

    def test_mismatched_named_bank_is_rejected_before_mapping(self):
        link = object.__new__(PynqImageLinkIO)
        link.read_status = Mock()
        # No mmio attribute: touching the mapping before this check would fail.
        wrong_bank = SimpleNamespace(base_address=0xA0000000, size=524288)
        link.overlay = SimpleNamespace(sink_ctrl=wrong_bank)
        with self.assertRaisesRegex(RuntimeError, "Unexpected PYNQ bank"):
            link.bram_words("sink_ctrl")
        link.read_status.assert_called_once()
