"""Transfer a batch of Gray8 images using the RFSoC 4x2 same-frequency TDD overlay."""
import argparse
import secrets
import struct
from contextlib import contextmanager
import hashlib
import json
import math
import os
import shutil
import signal
import statistics
import sys
import time
from pathlib import Path

FRAME_BYTES = 640 * 480
PACKET_BYTES = 1024
PACKET_COUNT = FRAME_BYTES // PACKET_BYTES
QUALITY_OFFSET = FRAME_BYTES
QUALITY_FILE = "packet_quality.raw"
MISSING = 0
CRC_BAD = 1
CRC_VALID = 2
HERE = Path(__file__).resolve().parent
OVERLAY_NAME = "ofdm_air8.bit"
HOST_INTERFACE = "ofdm_air8_v1"
HOST_SIGNATURE = 0x4f4f4b20  # Shared PS host register ABI.
MODEM_STATUS_ID = 0x41
RESET = 1 << 0
START = 1 << 1
PILOT = 1 << 2
RECEIVER = 1 << 3
PILOT_SECONDS = 2.0
PILOT_REPLY_TIMEOUT = 10.0
PILOT_TURNAROUND_SECONDS = 0.25
PLL_LOCKED = 2  # XRFDC_PLL_LOCKED, as opposed to the nonzero unlocked value 1.
TILE_RUNNING = 15
REGIONS = (("control", 0xA0100000, 16), ("telemetry", 0xA0110000, 16),
           ("host_state", 0xA0120000, 16), ("source_ctrl", 0xA0000000, 524288),
           ("sink_ctrl", 0xA0080000, 524288), ("rfdc", 0xA0140000, 262144))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_release_pair(bitfile):
    """Validate the OFDM release and matching bit/HWH files before loading PYNQ."""
    bitfile = Path(bitfile).resolve()
    hwh = bitfile.with_suffix(".hwh")
    manifest_path = bitfile.with_name("manifest.json")
    if bitfile.suffix.lower() != ".bit" or not all(path.is_file() for path in (bitfile, hwh, manifest_path)):
        raise FileNotFoundError("Extract the new release with ofdm_air8.bit, ofdm_air8.hwh and manifest.json together")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("board") != "RFSoC4x2" or manifest.get("phy") != "ofdm"
            or manifest.get("host_interface") != HOST_INTERFACE
            or manifest.get("modem_status_id") != MODEM_STATUS_ID):
        raise RuntimeError("Extract the RFSoC 4x2 OFDM release with the matching host and modem interfaces.")
    hashes = {path.name: digest(path.read_bytes()) for path in (bitfile, hwh)}
    for name, sha256 in hashes.items():
        if sha256 != manifest["files"].get(name):
            raise RuntimeError(f"Release checksum mismatch for {name}. Extract the matching bit/HWH pair.")
    return bitfile, hashes[bitfile.name], hashes[hwh.name]


def prepare_overlay_files(bitfile):
    """Keep PYNQ's .bit/HWH path separate from a same-name XSA export."""
    bitfile = Path(bitfile).resolve()
    hwh = bitfile.with_suffix(".hwh")
    if bitfile.suffix.lower() != ".bit" or not bitfile.is_file() or not hwh.is_file():
        raise FileNotFoundError("Keep ofdm_air8.bit and ofdm_air8.hwh in the same directory")
    if not bitfile.with_suffix(".xsa").exists():
        return bitfile
    # PYNQ 3.x checks for a sibling XSA before choosing its HWH parser, then
    # passes the .bit filename to the XSA metadata frontend. Preserve the
    # user's files and load an exact bit/HWH pair from a persistent directory.
    fingerprint = hashlib.sha256(bitfile.read_bytes() + hwh.read_bytes()).hexdigest()[:16]
    runtime = bitfile.parent / ".pynq_runtime" / (bitfile.stem + "-" + fingerprint)
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / bitfile.name
    if target.with_suffix(".xsa").exists():
        raise RuntimeError(f"Remove the unrelated XSA from the loader directory: {runtime}")
    shutil.copy2(bitfile, target)
    shutil.copy2(hwh, target.with_suffix(".hwh"))
    if bitfile.with_suffix(".dtbo").is_file():
        shutil.copy2(bitfile.with_suffix(".dtbo"), target.with_suffix(".dtbo"))
    print(f"PYNQ compatibility: using the bit/HWH pair in {runtime}", flush=True)
    return target


def metadata_region(overlay, name, expected_address, minimum_size):
    """PYNQ 3.x stores BRAM regions in mem_dict rather than ip_dict."""
    info = None
    for mapping_name in ("ip_dict", "mem_dict"):
        mapping = getattr(overlay, mapping_name, {})
        if name in mapping:
            info = mapping[name]
            break
    if info is None:
        raise KeyError(f"HWH does not expose {name} in ip_dict or mem_dict")
    if info["phys_addr"] != expected_address or info["addr_range"] < minimum_size:
        raise RuntimeError(f"Unexpected HWH memory map for {name}: {info}")
    return info["phys_addr"], info["addr_range"]


class PynqImageLinkIO:
    """Use Overlay's named GPIO drivers and BRAM memory objects.

    PYNQ v3.1 documents the GPIO channels. EmbeddedXrtMemory provides
    the public BRAM read and write methods on the RFSoC device.
    """

    def __init__(self, overlay):
        for name, address, size in REGIONS:
            metadata_region(overlay, name, address, size)
        self.control = overlay.control.channel1
        self.status = overlay.control.channel2
        self.levels = overlay.telemetry.channel1
        self.packet_counter = overlay.telemetry.channel2
        self.health = overlay.host_state.channel1
        self.applied_control = overlay.host_state.channel2
        for channel, direction in ((self.control, "out"), (self.status, "in"),
                                   (self.levels, "in"), (self.packet_counter, "in"),
                                   (self.health, "in"), (self.applied_control, "in")):
            channel.setlength(32)
            channel.setdirection(direction)
        self.rfdc = overlay.rfdc
        self.source = overlay.source_ctrl
        self.sink = overlay.sink_ctrl

    def write_control(self, value):
        self.control.write(value, 0xffffffff)

    def rf_ready(self):
        word = int(self.health.read())
        if word & 0xfffffffe != HOST_SIGNATURE:
            raise RuntimeError(f"Unexpected RF host signature 0x{word:08x}")
        return bool(word & 1)

    def require_ready(self):
        if not self.rf_ready():
            raise RuntimeError("RF datapath clock or reset is not ready. PS AXI is responding.")

    def apply_control(self, value):
        """Wait for the RF domain to acknowledge each full control word."""
        self.require_ready()
        self.write_control(value)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            self.require_ready()
            if int(self.applied_control.read()) == value:
                return
            time.sleep(0.001)
        raise TimeoutError(f"RF domain did not apply control 0x{value:08x}")

    def read_status(self):
        self.require_ready()
        word = int(self.status.read())
        if word >> 25 != MODEM_STATUS_ID:
            raise RuntimeError(f"Unexpected OFDM modem status 0x{word:08x}")
        return word

    def read_levels(self):
        return int(self.levels.read())

    def read_packet_counter(self):
        return int(self.packet_counter.read())

    def adc_overrange(self):
        # RFDC Gen3 ADC tile index 2, dual ADC block 1 occupies slices 2/3.
        # PG269 converter ISR, matching XRFdc_GetIntrStatus in xrfdc_intr.c.
        tile_base = 0x14000 + 2 * 0x4000
        return any(int(self.rfdc.read(tile_base + 0x208 + 8*index)) & 0x08 for index in (2,3))

    @staticmethod
    def read_bytes(memory, offset, length):
        # The public memory read(address) API returns one 32-bit word.
        # The RTL stores the first byte in the least significant byte.
        array = getattr(getattr(memory, "mmio", None), "array", None)
        if array is not None:
            return array[offset//4:(offset+length+3)//4].astype("<u4", copy=False).tobytes()[:length]
        return b"".join(int(memory.read(address)).to_bytes(4, "little")
                        for address in range(offset, offset + length, 4))[:length]

    def upload_image(self, pixels):
        if len(pixels) != FRAME_BYTES:
            raise ValueError(f"Image must contain exactly {FRAME_BYTES} bytes")
        self.source.write(0, pixels)
        if self.read_bytes(self.source, 0, FRAME_BYTES) != pixels:
            raise RuntimeError("Source BRAM upload/readback mismatch")

    def clear_sink(self):
        # Unreceived image slots and all packet quality bytes start at zero.
        self.sink.write(0, bytes(QUALITY_OFFSET + PACKET_COUNT))

    def received_image(self):
        return self.read_bytes(self.sink, 0, FRAME_BYTES)

    def packet_quality(self):
        return self.read_bytes(self.sink, QUALITY_OFFSET, PACKET_COUNT)


def load_pixels(path):
    path = Path(path)
    if path.suffix.lower() == ".raw":
        pixels = path.read_bytes()
        if len(pixels) != FRAME_BYTES:
            raise ValueError(f"Raw image must contain exactly {FRAME_BYTES} bytes")
        return pixels
    from PIL import Image
    with Image.open(path) as image:
        resampling = getattr(Image, "Resampling", Image)
        return image.convert("L").resize((640, 480), resampling.LANCZOS).tobytes()


def decode_status(value, role):
    count = (value >> 6) & 0x7ffff
    state = dict(done=bool(value & 1), failed=bool(value & 2), busy=bool(value & 4),
                 transport_fault=bool(value & 16), address_fault=bool(value & 32))
    if role == "send":
        state["exhausted_packets"] = count
    else:
        state.update(received_complete=bool(value & 8), unverified_packets=count)
    return state


def summarize_quality(quality):
    if len(quality) != PACKET_COUNT or set(quality) - {MISSING, CRC_BAD, CRC_VALID}:
        raise ValueError(f"Expected {PACKET_COUNT} packet quality bytes with values 0, 1 or 2")
    missing_ids = [i for i, value in enumerate(quality) if value == MISSING]
    bad_ids = [i for i, value in enumerate(quality) if value == CRC_BAD]
    return dict(missing_packets=len(missing_ids), crc_bad_packets=len(bad_ids),
                crc_valid_packets=quality.count(CRC_VALID),
                missing_packet_ids=missing_ids, crc_bad_packet_ids=bad_ids)


class BoardRun:
    """One endpoint of the two-board terminal workflow."""

    def __init__(self, role, bitfile, output, *, image=None, amplitude=4096, timeout=30.0, wait=180.0):
        if not isinstance(amplitude, int) or not 64 <= amplitude <= 16380 or amplitude % 4:
            raise ValueError("amplitude must be a multiple of four in 64..16380")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if not math.isfinite(wait) or wait <= 0:
            raise ValueError("wait must be positive and finite")
        self.role = role
        self.bitfile = Path(bitfile)
        self.image = image
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.wait = wait
        self.phase = "new"
        self.base_control = (1024 << 16) | ((amplitude // 4) << 4)
        self.started = time.monotonic()
        self.overlay = None
        self.link = None
        self._control_in_use = False
        self._board_lock = None
        self.result = dict(completed=False, physical_board_tested=False, board="RFSoC4x2",
                           phy="ofdm", host_interface=HOST_INTERFACE, modem_status_id=MODEM_STATUS_ID,
                           role=role, data_direction="A DAC_A -> B ADC_A",
                           ack_direction="B DAC_A -> A ADC_A",
                           carrier_mhz=500.0, sample_rate_msps=4915.2, fabric_clock_mhz=153.6,
                           ofdm_sample_rate_msps=19.2, gross_bit_rate_bps=15360000,
                           ofdm_fft_size=64, ofdm_active_subcarriers=32, ofdm_cp_samples=16,
                           ofdm_data_symbols_per_training=2, amplitude=amplitude, window_packets=8, ack_modulation="BPSK")

    def _mark(self, stage, state):
        self.result.update(stage=stage, stage_state=state)
        message = f"[{time.monotonic() - self.started:8.3f}s] {state} {stage}"
        print(message, flush=True)
        with (self.output / "bringup.log").open("a", encoding="utf-8") as log:
            log.write(message + "\n")
            log.flush()
            os.fsync(log.fileno())

    def _save_result(self):
        target = self.output / "result.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.result, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)

    @contextmanager
    def _step(self, stage, after):
        if self.phase != after:
            raise RuntimeError(f"{stage} requires phase {after}; current phase is {self.phase}")
        self._mark(stage, "BEGIN")
        self._save_result()
        try:
            yield
        except (Exception, KeyboardInterrupt) as error:
            self.result.update(error=str(error) or type(error).__name__, failed_stage=stage)
            try:
                self.close()
            except Exception as cleanup_error:
                self.result["cleanup_error"] = str(cleanup_error)
            self._mark(stage, "FAIL")
            self._save_result()
            raise
        self.phase = stage
        self._mark(stage, "OK")
        self._save_result()

    def inspect(self):
        """Check release hashes and parse metadata before touching the fabric."""
        with self._step("metadata", "new"):
            bitfile, bit_hash, hwh_hash = verify_release_pair(self.bitfile)
            self.result.update(bit_sha256=bit_hash, hwh_sha256=hwh_hash)
            bitfile = prepare_overlay_files(bitfile)
            self.result["loader_bitfile"] = str(bitfile)
            os.environ.setdefault("BOARD", "RFSoC4x2")
            import xrfdc  # Register the RFDC driver before Overlay binds its IP.
            from pynq import Overlay
            self.xrfdc = xrfdc
            self.overlay = Overlay(str(bitfile), download=False)
            for name, address, size in REGIONS:
                metadata_region(self.overlay, name, address, size)

    def program_clocks(self):
        """Program the RFSoC 4x2 reference clocks before downloading the PL."""
        with self._step("clocks", "metadata"):
            if os.name == "posix":
                import fcntl
                self._board_lock = open("/tmp/ook-rfsoc4x2.lock", "a", encoding="utf-8")
                try:
                    fcntl.flock(self._board_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise RuntimeError("Another RFSoC 4x2 session is active. Close it before programming clocks.") from error
            import xrfclk
            print("Programming LMK/LMX references: 245.76 / 491.52 MHz", flush=True)
            xrfclk.set_ref_clks(lmk_freq=245.76, lmx_freq=491.52)
            time.sleep(0.5)

    def download_overlay(self):
        """Load this session's validated OFDM bitstream."""
        with self._step("download", "clocks"):
            self.overlay.download()

    def check_ps_bus(self):
        """Read a host signature that does not depend on the RF clock."""
        with self._step("ps_bus", "download"):
            self.link = PynqImageLinkIO(self.overlay)
            self.result["rf_ready_at_ps_probe"] = self.link.rf_ready()
            print(f"PS AXI responding. RF clock ready: {self.result['rf_ready_at_ps_probe']}", flush=True)

    def check_rfdc(self):
        """Wait for tile startup and check the converter settings and RF clock."""
        with self._step("rfdc", "ps_bus"):
            rfdc = self.overlay.rfdc
            adc_tile, dac_tile = rfdc.adc_tiles[2], rfdc.dac_tiles[2]
            deadline = time.monotonic() + 8.0
            while True:
                ip_status = rfdc.IPStatus
                adc_status = dict(ip_status["ADCTileStatus"][2])
                dac_status = dict(ip_status["DACTileStatus"][2])
                adc_lock, dac_lock = int(adc_tile.PLLLockStatus), int(dac_tile.PLLLockStatus)
                rf_ready = self.link.rf_ready()
                self.result.update(adc_tile_status=adc_status, dac_tile_status=dac_status,
                                   adc_pll_locked=adc_lock == PLL_LOCKED, dac_pll_locked=dac_lock == PLL_LOCKED,
                                   adc_pll_status=adc_lock, dac_pll_status=dac_lock, rf_clock_ready=rf_ready)
                if (adc_status["TileState"] == dac_status["TileState"] == TILE_RUNNING
                        and adc_lock == dac_lock == PLL_LOCKED and rf_ready):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"RFDC not ready. ADC/DAC states {adc_status['TileState']}/{dac_status['TileState']}, "
                        f"PLL {adc_lock}/{dac_lock}, RF clock ready={rf_ready}")
                time.sleep(0.05)

            for name, tile, block, frequency, mode in (
                    ("adc", adc_tile, adc_tile.blocks[1], -500.0, self.xrfdc.MIXER_MODE_R2C),
                    ("dac", dac_tile, dac_tile.blocks[0], 500.0, self.xrfdc.MIXER_MODE_C2R)):
                if not tile.FIFOStatus:
                    tile.SetupFIFO(True)
                settings = dict(block.MixerSettings)
                settings.update(Freq=frequency, EventSource=self.xrfdc.EVNT_SRC_IMMEDIATE)
                block.MixerSettings = settings
                block.UpdateEvent(self.xrfdc.EVENT_MIXER)
                mixer = dict(block.MixerSettings)
                self.result[f"{name}_mixer"] = mixer
                self.result[f"{name}_block_status"] = dict(block.BlockStatus)
                if not tile.FIFOStatus:
                    raise RuntimeError(f"{name.upper()} FIFO did not enable")
                if (mixer["MixerMode"] != mode or mixer["MixerType"] != self.xrfdc.MIXER_TYPE_FINE
                        or not math.isclose(mixer["Freq"], frequency, abs_tol=0.1)):
                    raise RuntimeError(f"Unexpected {name.upper()} mixer settings: {mixer}")
            self.link.read_status()
            print("RFDC ready: ADC -500 MHz, DAC +500 MHz, both PLLs locked.", flush=True)

    def prepare_memory(self):
        """Prepare local BRAM while the modem stays in reset."""
        with self._step("image_prepare", "rfdc"):
            self._control_in_use = True
            self.link.apply_control(self.base_control | RESET)
            if self.role == "receive":
                self.link.clear_sink()

    def _median_level(self):
        readings = []
        for _ in range(32):
            self.link.require_ready()
            readings.append(self.link.read_levels() & 0xffff)
            time.sleep(0.001)
        return int(statistics.median(readings))

    def _wait_for_level(self, threshold, above, timeout, description):
        """Require two consecutive matching windows to reject short transients."""
        deadline = time.monotonic() + timeout
        matching = None
        strongest = 0
        while time.monotonic() < deadline:
            level = self._median_level()
            strongest = max(strongest, level)
            if (level >= threshold) == above:
                if matching is not None:
                    return (matching + level) // 2
                matching = level
            else:
                matching = None
        raise TimeoutError(f"{description}: threshold={threshold}, strongest={strongest}. "
                           "Check antenna pairing, spacing and the peer terminal.")

    def _emit_pilot(self):
        self.link.apply_control(self.base_control | PILOT)
        try:
            time.sleep(PILOT_SECONDS)
        finally:
            self.link.apply_control(self.base_control)

    def calibrate(self):
        """Exchange pilots and set this board's independent receive threshold."""
        with self._step("pilot_calibration", "image_prepare"):
            self.result["physical_board_tested"] = True
            # Both boards use idle sender mode during calibration. B arms after the pilot exchange.
            self.link.apply_control(self.base_control)
            time.sleep(0.05)
            idle = self._median_level()
            self.result["idle_magnitude"] = idle
            detect_level = max(idle * 3, idle + 64)
            if self.role == "send":
                print("TX_PILOT: A DAC_A -> B ADC_A", flush=True)
                self._emit_pilot()
                pilot = self._wait_for_level(detect_level, True, PILOT_REPLY_TIMEOUT,
                                             "A did not detect B's reply pilot")
            else:
                print("RX_WAITING_FOR_PILOT: start the send command on A now.", flush=True)
                pilot = self._wait_for_level(detect_level, True, self.wait,
                                             "B did not detect A's pilot")

            self.result["pilot_magnitude"] = pilot
            if pilot > 24000:
                raise RuntimeError(f"ADC pilot too strong ({pilot}). Increase attenuation or lower the peer amplitude.")
            threshold = (idle + pilot) // 2
            self._wait_for_level(threshold, False, PILOT_REPLY_TIMEOUT, "Peer pilot did not switch off")
            if self.role == "receive":
                time.sleep(PILOT_TURNAROUND_SECONDS)
                print("RX_PILOT: B DAC_A -> A ADC_A", flush=True)
                self._emit_pilot()

            self.base_control = (threshold << 16) | (self.base_control & 0xfff0)
            self.link.apply_control(self.base_control | RESET)
            self.result["threshold"] = threshold
            print(f"RF pilot: idle={idle}, on={pilot}, receive threshold={threshold}", flush=True)

    def _read_state(self):
        state = decode_status(self.link.read_status(), self.role)
        self.result.update(state)
        if state["transport_fault"] or state["address_fault"] or state["failed"]:
            raise RuntimeError(f"RF transfer failed: {state}")
        return state

    def pulse_start(self):
        self.link.apply_control(self.base_control | START)
        self.link.apply_control(self.base_control)

    def wait_frame(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._read_state()
            if state["done"]:
                return state
            time.sleep(0.002)
        raise TimeoutError("Timed out waiting for the radio frame handshake")

    def frame_dir(self, session, frame):
        target = self.output / f"{session:08x}" / f"{frame:04d}"
        target.mkdir(parents=True, exist_ok=False)
        return target

    def send(self):
        with self._step("image_send", "pilot_calibration"):
            session = secrets.randbits(32)
            count = len(self.image)
            self.result.update(session=session, count=count, frames=[])
            self.link.apply_control(self.base_control)
            for frame, path in enumerate(self.image):
                folder = self.frame_dir(session, frame)
                pixels = load_pixels(path)
                self.link.upload_image(pixels)
                (folder / "source.raw").write_bytes(pixels)
                self.link.source.write(0x7fff0, struct.pack("<II", session, count << 16 | frame))
                started = time.monotonic()
                self.pulse_start()
                state = self.wait_frame(self.timeout)
                self.result["frames"].append(dict(frame=frame, name=path.name, sha256=digest(pixels),
                    elapsed_seconds=time.monotonic()-started, rx_peak=self.link.read_levels() >> 16, adc_overrange_latched=self.link.adc_overrange(),
                    exhausted=state["exhausted_packets"], retries=self.link.read_packet_counter()))
                self._save_result()
                print(f"TX {frame+1}/{count}", flush=True)
            self.result.update(completed=True, total_elapsed_seconds=time.monotonic()-self.started)
        print("TX_DONE", flush=True)

    def receive(self):
        with self._step("image_receive", "pilot_calibration"):
            self.base_control |= RECEIVER
            self.link.apply_control(self.base_control | RESET)
            self.link.apply_control(self.base_control)
            print("RX_ARMED", flush=True)
            frame = 0
            self.result["frames"] = []
            while True:
                state = self.wait_frame(self.wait)
                session, desc = struct.unpack("<II", self.link.read_bytes(self.link.sink, 0x7fff0, 8))
                count = desc >> 16
                if (desc & 0xffff) != frame or not frame < count <= 1000:
                    raise RuntimeError("Unexpected frame descriptor")
                if frame and (session, count) != (self.result["session"], self.result["count"]):
                    raise RuntimeError("Batch changed during reception")
                self.result.update(session=session, count=count)
                folder = self.frame_dir(session, frame)
                pixels, quality = self.link.received_image(), self.link.packet_quality()
                summary = summarize_quality(quality)
                if state["unverified_packets"] != PACKET_COUNT-summary["crc_valid_packets"]:
                    raise RuntimeError("Status/quality count mismatch")
                (folder / "received.raw").write_bytes(pixels)
                (folder / QUALITY_FILE).write_bytes(quality)
                from PIL import Image
                Image.frombytes("L", (640,480), pixels).save(folder / "received.png")
                self.result["frames"].append(dict(frame=frame, sha256=digest(pixels),
                    quality_sha256=digest(quality), rx_peak=self.link.read_levels() >> 16, adc_overrange_latched=self.link.adc_overrange(), **summary))
                self._save_result()
                # CLOSED remains replayable while PS exports and clears the slot.
                self.link.clear_sink()
                self.pulse_start()
                frame += 1
                print(f"RX {frame}/{count}", flush=True)
                if frame == count:
                    time.sleep(0.2)  # cover all 10 final RF response retries
                    break
            self.result.update(completed=True, total_elapsed_seconds=time.monotonic()-self.started)
        print("RX_DONE", flush=True)

    def _reset_modem(self):
        if self._control_in_use:
            reset = self.base_control | RESET
            self.link.write_control(reset)
            self.link.apply_control(reset)
            self._control_in_use = False

    def close(self):
        """Leave the modem reset and release the session's board lock."""
        try:
            self._reset_modem()
        finally:
            if self._board_lock is not None:
                self._board_lock.close()
                self._board_lock = None


STOP_STAGES = ("metadata", "clocks", "download", "ps_bus", "rfdc")


def run(args):
    images = []
    if args.role == "send":
        if args.input:
            images = [args.input]
        else:
            directory = args.input_dir or args.bit.parent / "data"
            images = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in
                            {".png", ".raw", ".jpg", ".jpeg", ".bmp"})
            if not 1 <= args.count <= 1000 or len(images) < args.count:
                raise ValueError("Source directory cannot supply the requested 1..1000 frames")
            images = images[:args.count]
    session = BoardRun(args.role, args.bit, args.output, image=images,
                       amplitude=args.amplitude, timeout=getattr(args, "timeout", 30.0), wait=getattr(args, "wait", 180.0))
    try:
        actions = (session.inspect, session.program_clocks, session.download_overlay,
                   session.check_ps_bus, session.check_rfdc)
        for stage, action in zip(STOP_STAGES, actions):
            action()
            if args.stop_after == stage:
                session.result["stopped_after"] = stage
                session._save_result()
                print(f"DIAGNOSTIC_COMPLETE stage={stage}", flush=True)
                return
        session.prepare_memory()
        session.calibrate()
        if args.role == "send":
            session.send()
        else:
            session.receive()
    finally:
        session.close()


def verify(tx_dir, rx_dir):
    tx, rx = [json.loads((d / "result.json").read_text(encoding="utf-8")) for d in (tx_dir, rx_dir)]
    for role, record in (("send", tx), ("receive", rx)):
        if (record.get("role") != role or record.get("completed") is not True or
            record.get("physical_board_tested") is not True or record.get("host_interface") != HOST_INTERFACE):
            raise ValueError("Incomplete or incompatible board record")
    for key in ("session", "count", "bit_sha256", "hwh_sha256"):
        if tx[key] != rx[key]:
            raise ValueError(f"Different board runs: {key}")
    count = tx["count"]
    if not 1 <= count <= 1000 or any(len(r["frames"]) != count for r in (tx, rx)):
        raise ValueError("Incomplete frame list")
    frames = []
    for frame, (a,b) in enumerate(zip(tx["frames"],rx["frames"])):
        sub = Path(f"{tx['session']:08x}") / f"{frame:04d}"
        original = (tx_dir / sub / "source.raw").read_bytes()
        pixels = (rx_dir / sub / "received.raw").read_bytes()
        quality = (rx_dir / sub / QUALITY_FILE).read_bytes()
        if (len(original) != FRAME_BYTES or len(pixels) != FRAME_BYTES or a["frame"] != frame or
            b["frame"] != frame or digest(original) != a["sha256"] or digest(pixels) != b["sha256"]
            or digest(quality) != b["quality_sha256"]):
            raise ValueError(f"Invalid frame evidence: {frame}")
        summary = summarize_quality(quality)
        errors = 0 if original == pixels else sum(x!=y for x,y in zip(original,pixels))
        frames.append(dict(frame=frame, name=a["name"], mismatched_bytes=errors,
                           passed=errors==0 and summary["crc_valid_packets"]==PACKET_COUNT, **summary))
    return dict(passed=all(f["passed"] for f in frames), count=count, frames=frames)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="role", required=True)
    release_dir = HERE if (HERE / OVERLAY_NAME).exists() else HERE / "output"
    for role, help_text, folder in (
            ("send", "Run board A (send image, receive RF ACKs)", "tx_results"),
            ("receive", "Run board B (receive image, send RF ACKs)", "rx_results")):
        command = commands.add_parser(role, help=help_text)
        command.add_argument("--bit", type=Path, default=release_dir / OVERLAY_NAME)
        command.add_argument("--output", type=Path, default=HERE / folder)
        command.add_argument("--amplitude", type=int, default=4096,
                             help="Local transmit amplitude (64..16380, multiple of four; default 4096)")
        command.add_argument("--stop-after", choices=STOP_STAGES,
                             help="Stop after a startup diagnostic stage")
        if role == "send":
            group = command.add_mutually_exclusive_group()
            group.add_argument("--input", type=Path)
            group.add_argument("--input-dir", type=Path)
            command.add_argument("--count", type=int, default=1000)
            command.add_argument("--timeout", type=float, default=30.0,
                                 help="Whole-image timeout in seconds after starting A (default 30)")
        else:
            command.add_argument("--wait", type=float, default=180.0,
                                 help="Timeout in seconds waiting for A's pilot (default 180)")
    check = commands.add_parser("verify", help="Compare copied A and B outputs on any Python 3 computer")
    check.add_argument("--tx-dir", type=Path, required=True)
    check.add_argument("--rx-dir", type=Path, required=True)
    check.add_argument("--output", type=Path, help="Also save the combined JSON report here")
    args = parser.parse_args()
    if args.role == "verify":
        result = verify(args.tx_dir, args.rx_dir)
        report = json.dumps(result, indent=2) + "\n"
        if args.output:
            args.output.write_text(report, encoding="utf-8")
        print(report, end="")
        return 0 if result["passed"] else 1
    run(args)
    return 0


def interrupt(signum, _frame):
    raise KeyboardInterrupt(signal.Signals(signum).name)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, interrupt)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (RuntimeError, OSError, ValueError, KeyError, EOFError) as error:
        sys.exit(str(error) or type(error).__name__)
