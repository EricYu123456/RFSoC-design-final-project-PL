"""Run one RF endpoint. The capture thread owns the camera and B owns the MP4."""
import json
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from webcam_board import BoardRun, FRAME_BYTES, PACKET_COUNT, RECEIVER, REGIONS, RESET
from webcam_stream import Capture, Recording, pack_block, unpack_block

# The PL descriptor is uint16 count/index. Start a new RF batch at each boundary.
BATCH_SIZE = 65535
DESCRIPTOR = 0x7fff0


class Radio:
    def __init__(self, board):
        from pynq import MMIO
        self.board = board
        name = "source_ctrl" if board.role == "send" else "sink_ctrl"
        addr, size = next((addr, size) for region, addr, size in REGIONS if region == name)
        self.mmio = MMIO(addr, size)
        self.words = np.asarray(self.mmio.array)
        if self.words.dtype != np.dtype("<u4") or self.words.size * 4 < size:
            raise RuntimeError(f"Unexpected MMIO word layout for {name}")
        self.payload = self.words[:FRAME_BYTES // 4]
        self.quality = self.words[FRAME_BYTES // 4:(FRAME_BYTES + PACKET_COUNT) // 4]
        self.descriptor = self.words[DESCRIPTOR // 4:DESCRIPTOR // 4 + 2]
        self.checked = False
        if board.role == "receive":
            board.base_control |= RECEIVER
            self.arm()
        else:
            board.link.apply_control(board.base_control)

    def arm(self):
        self.board.link.apply_control(self.board.base_control | RESET)
        self.board.link.apply_control(self.board.base_control)

    def send(self, stream, number, block):
        batch, index = divmod(number, BATCH_SIZE)
        self.payload[:] = np.frombuffer(block, dtype="<u4")
        if not self.checked:
            if self.payload.copy().tobytes() != block:
                raise RuntimeError("TX BRAM readback differs from the video block")
            self.checked = True
        self.descriptor[:] = ((stream + batch) & 0xffffffff, (BATCH_SIZE << 16) | index)
        self.board.pulse_start()
        state = self.board.wait_frame(self.board.timeout)
        return state["exhausted_packets"], self.board.link.read_packet_counter()

    def receive(self, number, stream, stop):
        while not stop.is_set():
            state = self.board.read_state()
            if state["done"]:
                break
            stop.wait(0.002)
        else:
            return None
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
        block = None
        if good:
            words = np.fromiter((self.payload[i] for i in range(self.payload.size)),
                                dtype="<u4", count=self.payload.size)
            block = words.tobytes()
        # Clear the quality map before releasing the slot. A stale '2' must
        # never validate a missing packet on the next block.
        for i in range(self.quality.size):
            self.quality[i] = 0
        self.board.pulse_start()
        if index == BATCH_SIZE - 1:
            time.sleep(0.12)  # leave CLOSED replayable through A's last FIN retry
            self.arm()
        return (session - batch) & 0xffffffff, block


def send_video(radio, source, fps, stop, stats, preview):
    stream = secrets.randbits(32)
    camera = Capture(source, fps, stop)
    camera.start()
    try:
        while True:
            entries = camera.next_batch()
            if entries is None:
                break
            if not entries:
                continue
            number = stats["blocks"]
            missing, retries = radio.send(stream, number, pack_block(stream, number, fps, entries))
            stats["retransmissions"] += retries
            stats["blocks"] += 1
            stats["frames_sent"] += len(entries)
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


def receive_video(radio, stop, stats, preview, filename):
    recording = Recording(filename)
    stream = None
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
                block_stream, block_number, fps, entries = unpack_block(block)
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
            for entry in entries:
                recording.append(fps, entry)
                stats["frames_received"] += 1
            stats["blocks"] += 1
            preview(entries[-1][2])
    finally:
        recording.close()
        stats["mp4_frames"] = recording.slots
        if recording.frames:
            stats["mp4"] = str(filename)


def run_live(role, bitfile, output_root, camera=0, fps=30.0, amplitude=4096, show_preview=True):
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
    errors = []

    def preview(jpeg):
        if show_preview:
            latest[0] = jpeg

    def work():
        try:
            if role == "send":
                send_video(radio, camera, fps, stop, stats, preview)
            else:
                receive_video(radio, stop, stats, preview, output / "live_received.mp4")
        except BaseException as exc:
            errors.append(exc)

    worker = None
    try:
        for step in (board.inspect, board.program_clocks, board.download_overlay,
                     board.check_ps_bus, board.check_rfdc, board.prepare_memory, board.calibrate):
            step()
        radio = Radio(board)
        print(f"{role.upper()} ready. Output directory: {output}", flush=True)
        worker = threading.Thread(target=work, name="OFDM-" + role)
        worker.start()
        view = previous = None
        if show_preview:
            from IPython.display import Image, display
        while True:
            try:
                if not worker.is_alive():
                    break
                if show_preview:
                    jpeg = latest[0]
                    if jpeg is not None and jpeg is not previous:
                        previous = jpeg
                        img = Image(data=jpeg, format="jpeg")
                        if view is None:
                            view = display(img, display_id=True)
                        else:
                            view.update(img)
                # Event.wait keeps the notebook interruptible without interrupting
                # the worker in the middle of an RF handshake or MP4 write.
                time.sleep(0.15)
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
        stop.set()
        if worker is not None:
            worker.join()
        try:
            board.close()
        finally:
            report = output / "summary.json"
            report.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
            print(f"Summary: {report}", flush=True)
    return stats
