"""Transfer a 640x480 image over a two-board RFSoC 4x2 OFDM link."""
import argparse
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
OVERLAY_NAME = "ofdm_two_board.bit"
HOST_INTERFACE = "ofdm_two_board_v2"
HOST_SIGNATURE = 0x4f4f4b20  # Shared PS host register ABI.
MODEM_STATUS_ID = 0x3f
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
           ("host_state", 0xA0120000, 16), ("source_ctrl", 0xA0000000, FRAME_BYTES),
           ("sink_ctrl", 0xA0080000, QUALITY_OFFSET + PACKET_COUNT), ("rfdc", 0xA0140000, 262144))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_release_pair(bitfile):
    """Validate the OFDM release and matching bit/HWH files before loading PYNQ."""
    bitfile = Path(bitfile).resolve()
    hwh = bitfile.with_suffix(".hwh")
    manifest_path = bitfile.with_name("manifest.json")
    if bitfile.suffix.lower() != ".bit" or not all(path.is_file() for path in (bitfile, hwh, manifest_path)):
        raise FileNotFoundError("Extract the new release with ofdm_two_board.bit, ofdm_two_board.hwh and manifest.json together")
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
        raise FileNotFoundError("Keep ofdm_two_board.bit and ofdm_two_board.hwh in the same directory")
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

    @staticmethod
    def read_bytes(memory, offset, length):
        # The public memory read(address) API returns one 32-bit word.
        # The RTL stores the first byte in the least significant byte.
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
        state.update(received_complete=bool(value & 8), received_bytes=count)
    return state


def summarize_quality(quality):
    if len(quality) != PACKET_COUNT or set(quality) - {MISSING, CRC_BAD, CRC_VALID}:
        raise ValueError(f"Expected {PACKET_COUNT} packet quality bytes with values 0, 1 or 2")
    missing_ids = [i for i, value in enumerate(quality) if value == MISSING]
    bad_ids = [i for i, value in enumerate(quality) if value == CRC_BAD]
    return dict(missing_packets=len(missing_ids), crc_bad_packets=len(bad_ids),
                crc_valid_packets=quality.count(CRC_VALID),
                missing_packet_ids=missing_ids, crc_bad_packet_ids=bad_ids)


def validate_received_image(received, state, summary):
    materialized = PACKET_COUNT - summary["missing_packets"]
    complete = materialized == PACKET_COUNT
    if (state["received_bytes"] != materialized * PACKET_BYTES
            or state["done"] != complete or state["received_complete"] != complete):
        raise ValueError("B's receive status disagrees with its packet quality table")
    for packet in summary["missing_packet_ids"]:
        start = packet * PACKET_BYTES
        if any(received[start:start + PACKET_BYTES]):
            raise ValueError(f"Missing packet {packet} contains nonzero pixels")


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
                           ofdm_sample_rate_msps=19.2, gross_bit_rate_bps=12800000, amplitude=amplitude)

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
            if self.role == "send":
                pixels = load_pixels(self.image)
                self.link.upload_image(pixels)
                (self.output / "source.raw").write_bytes(pixels)
                self.result["source_sha256"] = digest(pixels)
                print(f"Verified {FRAME_BYTES:,} source bytes in A's BRAM.", flush=True)
            else:
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
                           "Check the cable direction, attenuation and the peer terminal.")

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

    def send(self):
        """Start A only after B's terminal reports that it is armed."""
        with self._step("image_send", "pilot_calibration"):
            input("Wait for RX_ARMED on B, then press Enter here to send the image. ")
            started = time.monotonic()
            for flags in (0, START, 0):
                self.link.apply_control(self.base_control | flags)
            while not self._read_state()["done"]:
                if time.monotonic() - started >= self.timeout:
                    raise TimeoutError("A timed out before finishing all packet attempts")
                time.sleep(0.005)
            self.result["elapsed_seconds"] = time.monotonic() - started
            self.result["retransmissions"] = self.link.read_packet_counter()
            exhausted = self.result["exhausted_packets"]
            if exhausted > PACKET_COUNT:
                raise RuntimeError(f"Unexpected A exhausted packet count {exhausted}")
            self.close()
            self.result.update(completed=True, all_packets_acknowledged=exhausted == 0)
        print(f"TX_DONE: {PACKET_COUNT} packet slots attempted; "
              f"exhausted={exhausted}, retransmissions={self.result['retransmissions']}", flush=True)
        print(f"A result: {self.output / 'result.json'}", flush=True)

    def receive(self):
        """Stay armed until A completes every packet attempt, then export the image."""
        with self._step("image_receive", "pilot_calibration"):
            self.base_control |= RECEIVER
            self.link.apply_control(self.base_control | RESET)
            self.link.apply_control(self.base_control)
            deadline = time.monotonic() + 1.0
            while not self._read_state()["busy"]:
                if time.monotonic() >= deadline:
                    raise TimeoutError("B did not enter receiver mode")
                time.sleep(0.005)
            print("RX_ARMED: press Enter on A to send the image.", flush=True)
            input("After TX_DONE appears on A, press Enter here to save B's image. ")
            state = self._read_state()
            if not state["busy"]:
                raise RuntimeError(f"B stopped listening before ACK shutdown: {state}")
            self.result["duplicates"] = self.link.read_packet_counter()
            self._reset_modem()

        with self._step("image_export", "image_receive"):
            from PIL import Image

            received = self.link.received_image()
            quality = self.link.packet_quality()
            (self.output / "received.raw").write_bytes(received)
            Image.frombytes("L", (640, 480), received).save(self.output / "received.png")
            (self.output / QUALITY_FILE).write_bytes(quality)
            self.result.update(received_sha256=digest(received), packet_quality_sha256=digest(quality))
            summary = summarize_quality(quality)
            self.result.update(summary)
            validate_received_image(received, state, summary)
            self.result.update(completed=True, all_packets_crc_valid=summary["crc_valid_packets"] == PACKET_COUNT)
        print(f"RX_DONE: CRC-valid={summary['crc_valid_packets']}, CRC-bad={summary['crc_bad_packets']}, "
              f"missing={summary['missing_packets']}; result: {self.output / 'result.json'}", flush=True)

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
    session = BoardRun(args.role, args.bit, args.output, image=getattr(args, "input", None),
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
    """Verify both board sessions and report image fidelity and packet quality."""
    tx = json.loads((tx_dir / "result.json").read_text(encoding="utf-8"))
    rx = json.loads((rx_dir / "result.json").read_text(encoding="utf-8"))
    for role, record in (("send", tx), ("receive", rx)):
        if (record.get("role") != role or record.get("host_interface") != HOST_INTERFACE
                or record.get("modem_status_id") != MODEM_STATUS_ID or record.get("completed") is not True
                or record.get("physical_board_tested") is not True):
            raise ValueError(f"Need a completed two-board {role} result")
    exhausted = tx.get("exhausted_packets")
    if tx.get("done") is not True or type(exhausted) is not int or not 0 <= exhausted <= PACKET_COUNT:
        raise ValueError("A's result does not show that all packet slots were attempted")
    for key in ("bit_sha256", "hwh_sha256"):
        if not tx.get(key) or tx[key] != rx.get(key):
            raise ValueError(f"A and B must use the same release ({key})")

    source = (tx_dir / "source.raw").read_bytes()
    received = (rx_dir / "received.raw").read_bytes()
    quality = (rx_dir / QUALITY_FILE).read_bytes()
    if len(source) != FRAME_BYTES or len(received) != FRAME_BYTES:
        raise ValueError(f"Both raw images must contain exactly {FRAME_BYTES:,} bytes")
    source_hash, received_hash, quality_hash = digest(source), digest(received), digest(quality)
    if (source_hash != tx.get("source_sha256") or received_hash != rx.get("received_sha256")
            or quality_hash != rx.get("packet_quality_sha256")):
        raise ValueError("Copied raw files do not match the associated result.json checksums")
    summary = summarize_quality(quality)
    validate_received_image(received, rx, summary)

    mismatches = sum(a != b for a, b in zip(source, received))
    crc_valid = summary["crc_valid_packets"] == PACKET_COUNT
    return dict(passed=mismatches == 0 and crc_valid, completed=True, physical_board_tested=True,
                bytes=FRAME_BYTES, mismatched_bytes=mismatches, byte_for_byte=mismatches == 0,
                all_packets_crc_valid=crc_valid, all_packets_acknowledged=exhausted == 0,
                source_sha256=source_hash, received_sha256=received_hash, packet_quality_sha256=quality_hash,
                bit_sha256=tx["bit_sha256"], hwh_sha256=tx["hwh_sha256"], exhausted_packets=exhausted,
                retransmissions=tx["retransmissions"], duplicates=rx["duplicates"], **summary)


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
            command.add_argument("--input", type=Path, default=release_dir / "image.raw")
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
