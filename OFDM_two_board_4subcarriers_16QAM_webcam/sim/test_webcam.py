"""Exercise the notebook entry point, Kernel Interrupt and MP4 with a local RF-slot substitute."""
import _thread
import json
import queue
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import webcam_live as live
from webcam_stream import pack_block, unpack_block


def check_descriptor_rollover():
    # Exercise the actual Radio methods with copied MMIO words, including
    # the final two slots of one uint16 batch and the first of the next.
    stream = 0xffffffff
    controls = []
    board = SimpleNamespace(role="send", base_control=0, timeout=3, pulse_start=lambda: None,
                            link=SimpleNamespace(apply_control=controls.append, read_packet_counter=lambda: 0),
                            wait_frame=lambda timeout: {"exhausted_packets": 0},
                            read_state=lambda: {"done": True, "unverified_packets": 0})
    def mock_mmio(address, size):
        return SimpleNamespace(array=np.zeros(size // 4, dtype="<u4"))
    with patch.dict(sys.modules, {"pynq": SimpleNamespace(MMIO=mock_mmio)}):
        sender = live.Radio(board)
        board.role = "receive"
        radio = live.Radio(board)
    controls.clear()
    for number in range(live.BATCH_SIZE - 2, live.BATCH_SIZE + 1):
        block = pack_block(stream, number, 12, [])
        assert sender.send(stream, number, block) == (0, 0)
        radio.descriptor[:] = sender.descriptor
        radio.payload[:] = sender.payload
        epoch, index = divmod(number, live.BATCH_SIZE)
        assert tuple(radio.descriptor) == ((stream + epoch) & 0xffffffff, (65535 << 16) | index)
        radio.quality[:] = 0x02020202
        assert radio.receive(number, stream, threading.Event()) == (stream, block)
        assert not radio.quality.any()
    assert controls == [board.base_control | live.RESET, board.base_control]
    radio.quality.fill(0)
    assert radio.receive(live.BATCH_SIZE, stream, threading.Event()) == (stream, None)


def main():
    check_descriptor_rollover()
    class LocalBoard:
        def __init__(self, role, bitfile, output, **kwargs):
            self.output = output
            output.mkdir(parents=True)
        def inspect(self):
            pass
        program_clocks = download_overlay = check_ps_bus = check_rfdc = inspect
        prepare_memory = calibrate = close = inspect

    channel = queue.Queue(maxsize=1)

    class LocalRadio:
        def __init__(self, board):
            pass
        def send(self, stream, number, block):
            channel.put((stream, block), timeout=5)
            return 0, 0
        def receive(self, number, stream, stop):
            while not stop.is_set():
                try:
                    return channel.get(timeout=0.2)
                except queue.Empty:
                    pass

    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        source, fps = folder / "input.avi", 12.0
        writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), fps, (640, 480))
        assert writer.isOpened()
        try:
            for i in range(300):
                writer.write(np.full((480, 640, 3), 20 + i % 200, dtype=np.uint8))
        finally:
            writer.release()
        results, errors = {}, []

        def receive():
            try:
                results["rx"] = live.run_live("receive", "", folder, show_preview=False)
            except BaseException as exc:
                errors.append(exc)

        with patch.object(live, "BoardRun", LocalBoard), patch.object(live, "Radio", LocalRadio):
            receiver = threading.Thread(target=receive, daemon=True)
            receiver.start()
            interrupt = threading.Timer(12.4, _thread.interrupt_main)
            interrupt.start()
            try:
                results["tx"] = live.run_live("send", "", folder, camera=str(source), fps=fps, show_preview=False)
            finally:
                interrupt.cancel()
            receiver.join(timeout=8)
            assert not receiver.is_alive(), "Receiver did not stop at EOS"
        if errors:
            raise errors[0]
        tx, rx = results["tx"], results["rx"]
        assert tx["end_of_stream"] and rx["end_of_stream"]
        assert rx["frames_received"] == tx["frames_sent"] and rx["blocks_rejected"] == 0
        cap = cv2.VideoCapture(rx["mp4"])
        count, levels = 0, set()
        try:
            assert cap.isOpened()
            recorded_fps = cap.get(cv2.CAP_PROP_FPS)
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                levels.add(round(float(frame.mean())))
                count += 1
        finally:
            cap.release()
        duration = count / recorded_fps
        assert count == rx["mp4_frames"] and 10.5 < duration < 20 and len(levels) > 40
        # Corrupt one JPEG byte with an otherwise plausible block header.
        corrupted = bytearray(pack_block(2, 0, fps, [(0, 0.0, b"jpeg")]))
        corrupted[48] ^= 1
        try:
            unpack_block(corrupted)
        except ValueError:
            pass
        else:
            raise AssertionError("RX accepted a corrupted block")
        evidence = dict(test="local camera-file / simulated RF slots / actual OpenCV MP4",
                        kernel_interrupt=True, remote_eos=True, frames_decoded=count,
                        descriptor_rollover=True, incomplete_slot_rejected=True,
                        duration_seconds=duration, fps=recorded_fps, blocks_rejected=rx["blocks_rejected"])
        print(json.dumps(evidence, indent=2))
        reports = ROOT / "reports"
        reports.mkdir(exist_ok=True)
        (reports / "webcam.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()
