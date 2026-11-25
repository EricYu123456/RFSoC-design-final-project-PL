"""Method B JPEG framing, webcam capture and incremental MP4 recording."""
import hashlib
import math
import struct
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from webcam_board import FRAME_BYTES

WIDTH, HEIGHT = 640, 480
MAX_FRAMES = 11
MAX_BACKLOG = 22
# A length-delimited body followed by SHA-256, then zero fill up to the PL slot.
# A zero-entry body marks End Of Stream. Monotonic times are relative to A.
HEADER = struct.Struct("<4sIQdHI")  # magic, stream ID, block index, capture FPS, count, body bytes
ENTRY = struct.Struct("<QdI")       # camera frame index, capture seconds, JPEG bytes
MAGIC = b"RFJ2"
DIGEST_BYTES = 32
JPEG_BUDGET = (FRAME_BYTES - HEADER.size - DIGEST_BYTES) // MAX_FRAMES - ENTRY.size


def pack_block(stream, number, fps, frames):
    if not 0 < fps <= 120 or len(frames) > MAX_FRAMES:
        raise ValueError("Invalid frame rate or JPEG count")
    used = HEADER.size + sum(ENTRY.size + len(data) for _, _, data in frames)
    if used + DIGEST_BYTES > FRAME_BYTES:
        raise ValueError("JPEGs exceed the BRAM slot")
    block = bytearray(FRAME_BYTES)
    HEADER.pack_into(block, 0, MAGIC, stream, number, fps, len(frames), used)
    pos = HEADER.size
    for index, seconds, data in frames:
        ENTRY.pack_into(block, pos, index, seconds, len(data))
        pos += ENTRY.size
        block[pos:pos + len(data)] = data
        pos += len(data)
    block[pos:pos + DIGEST_BYTES] = hashlib.sha256(memoryview(block)[:pos]).digest()
    return bytes(block)


def unpack_block(block):
    if len(block) != FRAME_BYTES:
        raise ValueError("Incomplete BRAM slot")
    magic, stream, number, fps, count, used = HEADER.unpack_from(block)
    if (magic != MAGIC or not 0 < fps <= 120 or count > MAX_FRAMES
            or not HEADER.size <= used <= FRAME_BYTES - DIGEST_BYTES):
        raise ValueError("Invalid video header")
    if hashlib.sha256(memoryview(block)[:used]).digest() != block[used:used + DIGEST_BYTES]:
        raise ValueError("Video block SHA-256 mismatch")
    pos, frames = HEADER.size, []
    for _ in range(count):
        if pos + ENTRY.size > used:
            raise ValueError("Truncated frame header")
        index, seconds, size = ENTRY.unpack_from(block, pos)
        pos += ENTRY.size
        if not size or pos + size > used or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Invalid JPEG length or timestamp")
        frames.append((index, seconds, block[pos:pos + size]))
        pos += size
    if pos != used:
        raise ValueError("Invalid video body length")
    return stream, number, fps, frames


def open_camera(source, fps):
    is_device = isinstance(source, int)
    cap = cv2.VideoCapture(source, cv2.CAP_V4L2) if is_device else cv2.VideoCapture(str(source))
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f"Cannot open camera or video {source!r}")
    if is_device:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class Capture(threading.Thread):
    """One producer, a bounded JPEG queue, and an interruptible consumer."""

    def __init__(self, source, fps, stop):
        super().__init__(daemon=True)
        self.source, self.fps, self.stop = source, fps, stop
        self.ready = deque(maxlen=MAX_BACKLOG)
        self.changed = threading.Condition()
        self.done = False
        self.error = None
        self.selected = self.dropped = 0

    def run(self):
        cap = None
        try:
            cap = open_camera(self.source, self.fps)
            file_source = not isinstance(self.source, int)
            origin = next_frame = time.monotonic()
            quality = 85
            while not self.stop.is_set():
                if file_source and self.stop.wait(max(0, next_frame - time.monotonic())):
                    break
                ok, frame = cap.read()
                now = time.monotonic()
                if not ok:
                    if file_source or self.stop.is_set():
                        break
                    raise RuntimeError("USB camera stopped delivering frames")
                if now + 0.001 < next_frame:
                    continue
                next_frame = max(next_frame + 1 / self.fps, now)
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
                if gray.shape != (HEIGHT, WIDTH):
                    gray = cv2.resize(gray, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
                while True:
                    ok, buf = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, quality])
                    if not ok:
                        raise RuntimeError("Cannot encode camera frame as JPEG")
                    if buf.size <= JPEG_BUDGET or quality == 20:
                        break
                    quality = max(20, quality - 5)
                item = (self.selected, now - origin, buf.tobytes())
                self.selected += 1
                if buf.size + HEADER.size + ENTRY.size + DIGEST_BYTES > FRAME_BYTES:
                    self.dropped += 1
                    continue
                if buf.size < 0.85 * JPEG_BUDGET:
                    quality = min(95, quality + 5)
                with self.changed:
                    if len(self.ready) == self.ready.maxlen:
                        self.dropped += 1
                    self.ready.append(item)
                    self.changed.notify()
        except Exception as exc:
            self.error = exc
        finally:
            if cap is not None:
                cap.release()
            with self.changed:
                self.done = True
                self.changed.notify_all()

    def next_batch(self):
        with self.changed:
            self.changed.wait_for(lambda: self.ready or self.done or self.stop.is_set(), timeout=0.25)
            result, size = [], HEADER.size + DIGEST_BYTES
            while self.ready and len(result) < MAX_FRAMES:
                added = ENTRY.size + len(self.ready[0][2])
                if size + added > FRAME_BYTES:
                    break
                result.append(self.ready.popleft())
                size += added
            if not result and (self.done or self.stop.is_set()):
                return None
            return result


class Recording:
    """Write B's decoded frames as they arrive, retaining one frame for timing."""

    def __init__(self, filename):
        self.filename = Path(filename)
        self.writer = self.previous = self.origin = self.last_time = self.fps = None
        self.index = -1
        self.slots = self.frames = 0

    def append(self, fps, entry):
        index, seconds, jpeg = entry
        if index <= self.index or (self.last_time is not None and seconds < self.last_time):
            raise ValueError("Out-of-order camera frame")
        gray = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
        if gray is None or gray.shape != (HEIGHT, WIDTH):
            raise ValueError("Invalid 640 x 480 JPEG")
        if self.writer is None:
            self.writer = cv2.VideoWriter(str(self.filename), cv2.VideoWriter_fourcc(*"mp4v"),
                                          fps, (WIDTH, HEIGHT), True)
            if not self.writer.isOpened():
                raise RuntimeError("OpenCV cannot open the mp4v writer on board B")
            self.origin, self.fps = seconds, fps
        if self.fps != fps:
            raise ValueError("Camera frame rate changed within a stream")
        slot = round((seconds - self.origin) * fps)
        while self.slots < slot:
            self.writer.write(self.previous)
            self.slots += 1
        self.previous = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        self.last_time, self.index = seconds, index
        self.frames += 1

    def close(self):
        if self.writer is not None:
            try:
                if self.previous is not None:
                    self.writer.write(self.previous)
                    self.slots += 1
            finally:
                self.writer.release()
                self.writer = None
