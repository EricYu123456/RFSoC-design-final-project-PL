"""Single-board latency benchmark. PS fills both BRAMs while the modem is held in reset."""
import json
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from webcam_board import BoardRun, FRAME_BYTES, RESET
from webcam_latency import StageTimes, perf_counter_ns
from webcam_live import BATCH_SIZE, Radio, receive_video
from webcam_native import read_words
from webcam_stream import Capture, DIGEST_BYTES, ENTRY, HEADER, MAX_FRAMES, block_extent, pack_block


def _jpeg_samples(camera, fps, timing):
    if camera is None:
        pattern = np.random.default_rng(7).integers(0, 256, (480, 640), dtype=np.uint8)
        ok, buf = cv2.imencode(".jpg", pattern, [cv2.IMWRITE_JPEG_QUALITY, 25])
        if not ok:
            raise RuntimeError("Could not encode the test pattern")
        return [buf.tobytes()]
    stop = threading.Event()
    capture = Capture(camera, fps, stop, timing)
    capture.start()
    pool = []
    try:
        deadline = time.monotonic() + max(15, 70 / fps)
        while len(pool) < 60 and time.monotonic() < deadline:
            entries = capture.next_batch()
            if entries is None:
                break
            pool.extend(jpeg for _, _, jpeg in entries)
        if capture.error:
            raise capture.error
        if not pool:
            raise RuntimeError("Camera produced no frames")
        return pool[:60]
    finally:
        stop.set()
        capture.join(timeout=5)
        if capture.is_alive():
            raise RuntimeError("Camera read is blocked")


def _snapshot(view):
    return np.fromiter((view[i] for i in range(view.size)), dtype="<u4", count=view.size)


def _word_write(view, values):
    for i, word in enumerate(values):
        view[i] = word


class _InjectedSlots:
    def __init__(self, tx, rx, pool, blocks, frames, fps):
        self.tx, self.rx, self.timing = tx, rx, rx.timing
        self.pool, self.blocks, self.frames, self.fps = pool, blocks, frames, fps
        self.done_ns = None
        self.used_bytes = 0

    def receive(self, number, stream, stop):
        stream_id = 0x52585031
        first = number * self.frames
        entries = [(i, i / self.fps, self.pool[i % len(self.pool)])
                   for i in range(first, first + self.frames)] if number < self.blocks else []
        start = perf_counter_ns()
        block = pack_block(stream_id, number, self.fps, entries)
        self.timing.end("pack", start)
        self.tx.upload(stream_id, number, block)
        if entries:
            self.used_bytes += HEADER.unpack_from(block)[-1] + DIGEST_BYTES
        # Test fixture. Exclude setup of the simulated incoming block from RX times.
        transmitted = read_words(self.tx.payload, self.tx.native)
        if transmitted != block:
            raise RuntimeError("TX BRAM differs from the expected zero-padded RF slot")
        _word_write(self.rx.payload, np.frombuffer(transmitted, dtype="<u4"))
        _word_write(self.rx.descriptor, _snapshot(self.tx.descriptor))
        _word_write(self.rx.quality, [0x02020202] * self.rx.quality.size)
        start = perf_counter_ns()
        reference = _snapshot(self.rx.payload).tobytes()
        self.timing.end("full_trial", start)
        if reference != block:
            raise AssertionError("Full BRAM snapshot differs from the injected slot")
        self.rx.done_ns = self.done_ns = perf_counter_ns()
        result = self.rx.read_slot(number, stream, {"unverified_packets": 0}, release=False)
        pause = perf_counter_ns()
        if result[1] != block[:block_extent(block)]:
            raise RuntimeError("Single-board BRAM round trip failed")
        self.done_ns += perf_counter_ns() - pause
        return result


def run_single_board(bitfile, output_root, *, camera=None, fps=30, blocks=40, frames=6,
                     recording_codec="auto", on_decoded=None):
    """Profile the normal JPEG, pack/upload, RX read, decode and MP4 paths without an RF peer."""
    if not (1 <= frames <= MAX_FRAMES and 1 <= blocks < BATCH_SIZE and 0 < fps <= 120):
        raise ValueError("Invalid block count, frames per block, or frame rate")
    output = Path(output_root) / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_single_board")
    board = BoardRun("receive", bitfile, output)
    timing = StageTimes()
    stats = dict(blocks=0, frames_received=0, blocks_rejected=0, end_of_stream=False,
                 mode="single_board", rf_peer=False, camera=str(camera), frames_per_block=frames)
    with closing(board):
        board.inspect()
        # Overlay(download=False) can already report the requested filename on
        # PYNQ 3.1 even when a different design is programmed in the fabric.
        board.program_clocks()
        board.download_overlay()
        board.check_ps_bus()
        board.check_rfdc()
        board.read_state()
        previous = int(board.link.applied_control.read())
        if not previous & RESET:
            raise RuntimeError("Modem is active. Stop the existing session before benchmarking.")
        board.prepare_memory()
        board.role = "send"
        tx = Radio(board, map_only=True)
        board.role = "receive"
        rx = Radio(board, map_only=True)
        tx.timing = rx.timing = timing
        pool = _jpeg_samples(camera, fps, timing)
        # Complex images can exceed the nominal JPEG budget at minimum quality.
        # Mirror live packing's capacity check before any timed iteration.
        max_size = max(map(len, pool))

        frames = min(frames, (FRAME_BYTES - HEADER.size - DIGEST_BYTES) // (ENTRY.size + max_size))
        if frames < 1:
            raise ValueError("A JPEG exceeds the available slot")
        source = _InjectedSlots(tx, rx, pool, blocks, frames, fps)
        receive_video(source, threading.Event(), stats, lambda _: None, output / "benchmark.mp4",
                      recording_codec, on_decoded=on_decoded)
        stats["bram_reader"] = "native aligned uint32" if rx.native else "Python aligned uint32"
        if (stats["frames_received"] != blocks * frames or stats["recorded_input_frames"] != blocks * frames
                or not stats["end_of_stream"]):
            raise RuntimeError("Benchmark frame count mismatch")
        stats.update(frames_per_block=frames, jpeg_samples=len(pool), mean_slot_used_pct=100*source.used_bytes/(blocks*FRAME_BYTES))
    stats["latency_report"] = timing.save(output, "single board / PS-injected BRAM / no RF or browser preview")
    (output / "summary.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats
