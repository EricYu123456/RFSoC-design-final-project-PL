"""Run one RF endpoint. The capture thread owns the camera and B owns the MP4."""
import json
import queue
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from webcam_board import BoardRun, FRAME_BYTES, PACKET_COUNT, RECEIVER, RESET
from webcam_native import native_library, read_words, write_words
from webcam_stream import HEADER, Capture, Recording, block_extent, pack_block, unpack_block
from webcam_latency import StageTimes, perf_counter_ns

# The PL descriptor is uint16 count/index. Start a new RF batch at each boundary.
BATCH_SIZE = 65535
DESCRIPTOR = 0x7fff0


class Radio:
    def __init__(self, board, *, map_only=False):
        self.board = board
        self.timing = StageTimes()
        self.done_ns = None
        name = "source_ctrl" if board.role == "send" else "sink_ctrl"
        self.words = board.link.bram_words(name)
        self.payload = self.words[:FRAME_BYTES // 4]
        self.quality = self.words[FRAME_BYTES // 4:(FRAME_BYTES + PACKET_COUNT) // 4]
        self.descriptor = self.words[DESCRIPTOR // 4:DESCRIPTOR // 4 + 2]
        self.checked = False
        self.previous_words = self.payload.size
        self.native = native_library()
        if map_only:
            return
        if board.role == "receive":
            board.base_control |= RECEIVER
            self.arm()
        else:
            board.link.apply_control(board.base_control)

    def arm(self):
        self.board.link.apply_control(self.board.base_control | RESET)
        self.board.link.apply_control(self.board.base_control)

    def upload(self, stream, number, block):
        self.board.link.require_ready()
        started = perf_counter_ns()
        batch, index = divmod(number, BATCH_SIZE)
        used_words = (block_extent(block) + 3) // 4
        # Only the previous useful prefix can contain stale bytes. On the first
        # upload write the complete slot, including zeros, for the fixed-length PL.
        count = max(self.previous_words, used_words)
        values = np.frombuffer(block, dtype="<u4", count=count)
        write_words(self.payload[:count], values, self.native)
        self.previous_words = used_words
        self.descriptor[0] = (stream + batch) & 0xffffffff
        self.descriptor[1] = (BATCH_SIZE << 16) | index
        self.timing.end("upload", started)
        if not self.checked:
            started = perf_counter_ns()
            if read_words(self.payload, self.native) != block:
                raise RuntimeError("TX BRAM readback differs from the video block")
            self.checked = True
            self.timing.end("readback", started)

    def send(self, stream, number, block):
        self.upload(stream, number, block)
        started = perf_counter_ns()
        self.board.pulse_start()
        self.timing.end("start", started)
        started = perf_counter_ns()
        state = self.board.wait_frame(self.board.timeout)
        elapsed = perf_counter_ns() - started
        retries = self.board.link.read_packet_counter()
        self.timing.add("rf", elapsed)
        self.timing.add("rf_retry" if retries else "rf_clean", elapsed)
        return state["exhausted_packets"], retries

    def receive(self, number, stream, stop):
        start_wait = perf_counter_ns()
        while not stop.is_set():
            state = self.board.read_state()
            if state["done"]:
                break
            stop.wait(0.002)
        else:
            return None
        self.done_ns = perf_counter_ns()
        self.timing.add("rx_wait", self.done_ns - start_wait)
        return self.read_slot(number, stream, state)

    def read_slot(self, number, stream, state, *, release=True):
        self.board.link.require_ready()
        started = perf_counter_ns()
        # ARM device mappings require aligned accesses. A vectorized NumPy
        # copy of the 300-byte quality map raises SIGBUS on the RFSoC.
        session, desc = int(self.descriptor[0]), int(self.descriptor[1])
        batch, index = divmod(number, BATCH_SIZE)
        if desc != (BATCH_SIZE << 16) | index or (
                stream is not None and session != (stream + batch) & 0xffffffff):
            raise RuntimeError(f"Unexpected RX descriptor for block {number}: {session:#x}/{desc:#x}")
        good = state["unverified_packets"] == 0 and all(
            int(self.quality[i]) == 0x02020202 for i in range(self.quality.size)
        )
        self.timing.end("quality", started)
        block = None
        if good:
            started = perf_counter_ns()
            hwords = (HEADER.size + 3) // 4
            header = read_words(self.payload[:hwords], self.native)
            try:
                extent = block_extent(header)
            except ValueError:
                pass  # Release a malformed slot and let the stream continue.
            else:
                tail = read_words(self.payload[hwords:(extent + 3) // 4], self.native)
                block = (header + tail)[:extent]
            self.timing.end("payload", started)
        started = perf_counter_ns()
        # Clear the quality map before releasing the slot. A stale '2' must
        # never validate a missing packet on the next block.
        for i in range(self.quality.size):
            self.quality[i] = 0
        if release:
            self.board.pulse_start()
        self.timing.end("release", started)
        if self.done_ns is not None:
            self.timing.end("done_to_release", self.done_ns)
        if release and index == BATCH_SIZE - 1:
            time.sleep(0.12)  # leave CLOSED replayable through A's last FIN retry
            self.arm()
        return (session - batch) & 0xffffffff, block


def send_video(radio, source, fps, stop, stats, preview, *, on_encoded=None,
               jpeg_quality=(85, 20, 95, 5)):
    stream = secrets.randbits(32)
    timing = radio.timing
    camera = Capture(source, fps, stop, timing, on_encoded=on_encoded, jpeg_quality=jpeg_quality)
    camera.start()
    try:
        while True:
            started = perf_counter_ns()
            entries = camera.next_batch()
            timing.end("batch_wait", started)
            if entries is None:
                break
            if not entries:
                continue
            number = stats["blocks"]
            started = perf_counter_ns()
            block = pack_block(stream, number, fps, entries)
            timing.end("pack", started)
            missing, retries = radio.send(stream, number, block)
            now = time.monotonic() - camera.origin
            for _, capture_time, _ in entries:
                timing.add("tx_age", int((now - capture_time) * 1e9))
            stats["retransmissions"] += retries
            stats["blocks"] += 1
            stats["frames_sent"] += len(entries)
            hist = stats.setdefault("frames_per_block_histogram", {})
            hist[len(entries)] = hist.get(len(entries), 0) + 1
            stats["blocks_with_exhausted_packets"] += int(bool(missing))
            preview(entries[-1][2])
        number = stats["blocks"]
        missing, retries = radio.send(stream, number, pack_block(stream, number, fps, []))
        stats["retransmissions"] += retries
        if missing:
            raise RuntimeError("RF completion of the end-of-stream block could not be confirmed")
        stats["end_of_stream"] = True
        if camera.error is not None:
            raise camera.error
    finally:
        stop.set()
        camera.join(timeout=5)
        stats.update(frames_captured=camera.selected, frames_dropped_backlog=camera.dropped)
        if camera.is_alive():
            raise RuntimeError("USB camera read is still blocked after stop")


def receive_video(radio, stop, stats, preview, filename, recording_codec="auto", *, on_decoded=None):
    timing = radio.timing
    recording = Recording(filename, timing, recording_codec, on_decoded=on_decoded)
    jobs = queue.Queue(maxsize=2)
    errors = []

    def record():
        try:
            try:
                while True:
                    job = jobs.get()
                    if job is None:
                        break
                    fps, entries = job
                    for entry in entries:
                        recording.append(fps, entry)
            finally:
                recording.close()
        except BaseException as exc:
            errors.append(exc)
            stop.set()  # Also wake an idle RF receiver if writing fails.

    def enqueue(item):
        while recorder.is_alive():
            try:
                jobs.put(item, timeout=0.05)
                return
            except queue.Full:
                continue
        # The worker sets stop on failure. Its exception is raised after join.

    recorder = threading.Thread(target=record, name="OFDM-record")
    recorder.start()
    stream = None
    first_preview_ns = first_block_frames = None
    try:
        while not stop.is_set():
            number = stats["blocks"]
            result = radio.receive(number, stream, stop)
            if result is None:
                break
            stream, block = result
            if block is None:
                stats["blocks_rejected"] += 1
                stats["blocks"] += 1
                continue
            try:
                started = perf_counter_ns()
                block_stream, block_number, fps, entries = unpack_block(block)
                timing.end("unpack", started)
                if (block_stream, block_number) != (stream, number):
                    raise ValueError("Header does not match the RF descriptor")
            except ValueError as exc:
                stats["blocks_rejected"] += 1
                stats["blocks"] += 1
                print(f"Discarded block {number}: {exc}")
                continue
            if not entries:
                stats["end_of_stream"] = True
                time.sleep(0.12)  # keep B available for a repeated FIN
                break
            stats["frames_received"] += len(entries)
            hist = stats.setdefault("frames_per_block_histogram", {})
            hist[len(entries)] = hist.get(len(entries), 0) + 1
            stats["blocks"] += 1
            preview(entries[-1][2])
            now_ns = perf_counter_ns()
            if first_preview_ns is None:
                first_preview_ns, first_block_frames = now_ns, len(entries)
            elif stats.get("mode") != "single_board" and now_ns > first_preview_ns:
                stats["delivered_fps"] = ((stats["frames_received"] - first_block_frames)
                                          * 1e9 / (now_ns - first_preview_ns))
            if getattr(radio, "done_ns", None) is not None:
                timing.end("rx_service", radio.done_ns)
            started = perf_counter_ns()
            enqueue((fps, entries))
            timing.end("record_queue_wait", started)
    finally:
        started = perf_counter_ns()
        if recorder.is_alive():
            enqueue(None)
        recorder.join()
        timing.end("record_drain", started)
        stats.update(mp4_frames=recording.slots, recorded_input_frames=recording.frames,
                     recording_codec=recording.codec)
        if recording.frames:
            stats["mp4"] = str(filename)
        if errors:
            raise errors[0]


def run_live(role, bitfile, output_root, camera=0, fps=30.0, amplitude=4096, show_preview=True,
             recording_codec="auto", *, on_encoded=None, on_decoded=None,
             jpeg_quality=(85, 20, 95, 5), duration_s=None):
    if role not in ("send", "receive"):
        raise ValueError("Invalid role")
    if duration_s is not None:
        if role != "send" or not np.isfinite(duration_s) or duration_s <= 0:
            raise ValueError("duration_s must be positive and is TX-only")
    if not 0 < fps <= 120:
        raise ValueError("Set fps between 0 (exclusive) and 120")
    output = Path(output_root) / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + role)
    board = BoardRun(role, bitfile, output, amplitude=amplitude)
    stats = dict(role=role, blocks=0, end_of_stream=False)
    if role == "send":
        stats.update(frames_sent=0, blocks_with_exhausted_packets=0, retransmissions=0)
    else:
        stats.update(frames_received=0, blocks_rejected=0)
    stop = threading.Event()
    latest = [None]
    changed = threading.Event()
    timing = StageTimes()
    errors = []

    def preview(jpeg):
        if show_preview:
            latest[0] = (jpeg, perf_counter_ns())
            changed.set()

    def work():
        try:
            if role == "send":
                send_video(radio, camera, fps, stop, stats, preview,
                           on_encoded=on_encoded, jpeg_quality=jpeg_quality)
            else:
                receive_video(radio, stop, stats, preview, output / "live_received.mp4",
                              recording_codec, on_decoded=on_decoded)
        except BaseException as exc:
            errors.append(exc)
        finally:
            changed.set()

    worker = None
    deadline = None
    try:
        native_library()  # Finish optional compilation before the pilot exchange.
        for step in (board.inspect, board.program_clocks, board.download_overlay,
                     board.check_ps_bus, board.check_rfdc, board.prepare_memory, board.calibrate):
            started = perf_counter_ns()
            step()
            timing.end(step.__name__, started)
        started = perf_counter_ns()
        radio = Radio(board)
        timing.end("map_and_arm", started)
        radio.timing = timing
        print(f"{role.upper()} ready. Output directory: {output}", flush=True)
        worker = threading.Thread(target=work, name="OFDM-" + role)
        worker.start()
        if duration_s is not None:
            deadline = threading.Timer(duration_s, stop.set)
            deadline.daemon = True
            deadline.start()
        view = previous = None
        if show_preview:
            from IPython.display import Image, display
        while True:
            try:
                changed.clear()
                finished = not worker.is_alive()
                if show_preview:
                    item = latest[0]
                    if item is not None and item is not previous:
                        previous = item
                        jpeg, queued = item
                        started = perf_counter_ns()
                        timing.add("display_wait", started - queued)
                        img = Image(data=jpeg, format="jpeg")
                        if view is None:
                            view = display(img, display_id=True)
                        else:
                            view.update(img)
                        timing.end("display", started)
                # Event.wait keeps the notebook interruptible without interrupting
                # the worker in the middle of an RF handshake or MP4 write.
                if finished:
                    break
                changed.wait(0.15)
            except KeyboardInterrupt:
                if not stop.is_set():
                    print("Stop requested. Finishing the current transfer and closing the recording.", flush=True)
                stop.set()
        if errors:
            raise errors[0]
    except BaseException as exc:
        stats["error"] = str(exc) or type(exc).__name__
        raise
    finally:
        if deadline is not None:
            deadline.cancel()
        stop.set()
        if worker is not None:
            worker.join()
        try:
            board.close()
        finally:
            stats["latency_report"] = timing.save(output, "live " + role)
            report = output / "summary.json"
            report.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
            print(f"Summary: {report}", flush=True)
    return stats
