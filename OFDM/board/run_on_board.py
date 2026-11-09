"""Send a 640x480 image over OFDM from RFSoC 4x2 DAC_A to ADC_A."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
import shutil
import statistics
import time
from pathlib import Path

FRAME_BYTES = 640 * 480
HERE = Path(__file__).resolve().parent
HOST_INTERFACE = "ps_clock_v2"
HOST_SIGNATURE = 0x4f4f4b20  # Shared PS host register ABI.
MODEM_STATUS_ID = 0x3d
PLL_LOCKED = 2  # XRFDC_PLL_LOCKED, as opposed to the nonzero unlocked value 1.
TILE_RUNNING = 15
REGIONS = (("control", 0xA0100000, 16), ("telemetry", 0xA0110000, 16),
           ("host_state", 0xA0120000, 16), ("source_ctrl", 0xA0000000, FRAME_BYTES),
           ("sink_ctrl", 0xA0080000, FRAME_BYTES), ("rfdc", 0xA0140000, 262144))


def verify_release_pair(bitfile):
    """Validate the OFDM release and matching bit/HWH files before loading PYNQ."""
    bitfile = Path(bitfile).resolve()
    hwh = bitfile.with_suffix(".hwh")
    manifest_path = bitfile.with_name("manifest.json")
    if bitfile.suffix.lower() != ".bit" or not all(path.is_file() for path in (bitfile, hwh, manifest_path)):
        raise FileNotFoundError("Extract the new release with ofdm_rf.bit, ofdm_rf.hwh and manifest.json together")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("phy") != "ofdm" or manifest.get("host_interface") != HOST_INTERFACE
            or manifest.get("modem_status_id") != MODEM_STATUS_ID):
        raise RuntimeError("Extract the RFSoC 4x2 OFDM release with the matching host and modem interfaces.")
    for path in (bitfile, hwh):
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"].get(path.name):
            raise RuntimeError(f"Release checksum mismatch for {path.name}. Extract the matching bit/HWH pair.")
    return bitfile


def prepare_overlay_files(bitfile):
    """Keep PYNQ's .bit/HWH path separate from a same-name XSA export."""
    bitfile = Path(bitfile).resolve()
    hwh = bitfile.with_suffix(".hwh")
    if bitfile.suffix.lower() != ".bit" or not bitfile.is_file() or not hwh.is_file():
        raise FileNotFoundError("Keep ofdm_rf.bit and ofdm_rf.hwh in the same directory")
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
        self.retries = overlay.telemetry.channel2
        self.health = overlay.host_state.channel1
        self.applied_control = overlay.host_state.channel2
        for channel, direction in ((self.control, "out"), (self.status, "in"),
                                   (self.levels, "in"), (self.retries, "in"),
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

    def read_retries(self):
        return int(self.retries.read())

    @staticmethod
    def read_frame(memory):
        # The public memory read(address) API returns one 32-bit word.
        # The RTL stores the first pixel in the least significant byte.
        return b"".join(int(memory.read(offset)).to_bytes(4, "little")
                        for offset in range(0, FRAME_BYTES, 4))

    def upload_image(self, pixels):
        if len(pixels) != FRAME_BYTES:
            raise ValueError(f"Image must contain exactly {FRAME_BYTES} bytes")
        self.source.write(0, pixels)
        self.sink.write(0, b"\xef\xbe\xad\xde" * (FRAME_BYTES // 4))
        if self.read_frame(self.source) != pixels:
            raise RuntimeError("Source BRAM upload/readback mismatch")

    def received_image(self):
        return self.read_frame(self.sink)


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


def decode_status(value):
    return dict(done=bool(value & 1), failed=bool(value & 2), busy=bool(value & 4),
                received_complete=bool(value & 8), transport_fault=bool(value & 16),
                address_fault=bool(value & 32), received_bytes=(value >> 6) & 0x7ffff)


class BoardRun:
    """One bring-up session, stepped from a Notebook or the terminal."""

    def __init__(self, bitfile, image, output, amplitude=4096, timeout=30.0):
        if not isinstance(amplitude, int) or not 64 <= amplitude <= 16380 or amplitude % 4:
            raise ValueError("amplitude must be a multiple of four in 64..16380")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self.bitfile = Path(bitfile)
        self.image = Path(image)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.phase = "new"
        self.base_control = (1024 << 16) | ((amplitude // 4) << 4)
        self.started = time.monotonic()
        self.overlay = None
        self.link = None
        self._control_in_use = False
        self._board_lock = None
        self.result = dict(passed=False, physical_board_tested=False, board="RFSoC4x2",
                           phy="ofdm", host_interface=HOST_INTERFACE, modem_status_id=MODEM_STATUS_ID,
                           path="DAC_A (tile230/block0) -> ADC_A (tile226/block1)",
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
        (self.output / "result.json").write_text(
            json.dumps(self.result, indent=2) + "\n", encoding="utf-8")

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
            self._mark(stage, "FAIL")
            try:
                self.close()
            except Exception as cleanup_error:
                self.result["cleanup_error"] = str(cleanup_error)
            self._save_result()
            raise
        self.phase = stage
        self._mark(stage, "OK")
        self._save_result()

    def inspect(self):
        """Check release hashes and parse metadata before touching the fabric."""
        with self._step("metadata", "new"):
            bitfile = verify_release_pair(self.bitfile)
            self.pixels = load_pixels(self.image)
            self.result["source_sha256"] = hashlib.sha256(self.pixels).hexdigest()
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

    def upload(self):
        """Hold the modem in reset while uploading and verifying the pixels."""
        with self._step("image_upload", "rfdc"):
            self._control_in_use = True
            self.link.apply_control(self.base_control | 1)
            self.link.upload_image(self.pixels)
            print(f"Verified {FRAME_BYTES:,} source bytes in BRAM.", flush=True)

    def calibrate(self):
        """Measure idle and pilot amplitudes with the DAC_A to ADC_A cable."""
        with self._step("pilot_calibration", "image_upload"):
            self.result["physical_board_tested"] = True
            magnitudes = []
            for pilot in (0, 4):
                self.link.apply_control(self.base_control | pilot)
                time.sleep(0.02)
                readings = []
                for _ in range(40):
                    self.link.require_ready()
                    readings.append(self.link.read_levels() & 0xffff)
                    time.sleep(0.001)
                magnitudes.append(int(statistics.median(readings)))
            self.link.apply_control(self.base_control | 1)
            off, on = magnitudes
            self.result.update(noise_magnitude=off, pilot_magnitude=on)
            if on < max(off * 3, off + 64):
                raise RuntimeError(f"RF pilot too weak (idle={off}, pilot={on}). Check DAC_A to ADC_A.")
            if on > 24000:
                raise RuntimeError(f"ADC pilot too strong ({on}). Increase attenuation or reduce amplitude.")
            # The packet start marker uses the same DC amplitude as the cable pilot.
            threshold = (off + on) // 2
            self.result["threshold"] = threshold
            self.base_control = (threshold << 16) | (self.base_control & 0xfff0)
            print(f"RF pilot: idle={off}, on={on}, threshold={threshold}", flush=True)

    def transfer(self):
        """Run the FPGA modem, read the received image, and verify every byte."""
        with self._step("image_transfer", "pilot_calibration"):
            for word in (self.base_control | 1, self.base_control, self.base_control | 2, self.base_control):
                self.link.apply_control(word)
            started = time.monotonic()
            while True:
                state = decode_status(self.link.read_status())
                self.result.update(state)
                if state["transport_fault"] or state["address_fault"] or state["failed"]:
                    raise RuntimeError(f"RF transfer failed: {state}")
                if state["done"]:
                    break
                if time.monotonic() - started > self.timeout:
                    raise TimeoutError(f"RF transfer timed out: {state}")
                time.sleep(0.005)
            self.result["elapsed_seconds"] = time.monotonic() - started
            self.result["retransmissions"] = self.link.read_retries()
            self.link.apply_control(self.base_control | 1)
            self._control_in_use = False

        with self._step("image_verification", "image_transfer"):
            from PIL import Image
            received = self.link.received_image()
            (self.output / "received.raw").write_bytes(received)
            Image.frombytes("L", (640, 480), received).save(self.output / "received.png")
            self.result["received_sha256"] = hashlib.sha256(received).hexdigest()
            mismatches = sum(a != b for a, b in zip(self.pixels, received))
            self.result["mismatched_bytes"] = mismatches
            if not (state["received_complete"] and state["received_bytes"] == FRAME_BYTES
                    and received == self.pixels):
                raise RuntimeError(f"Image verification failed: {state['received_bytes']} bytes, {mismatches} mismatches")
            self.result["passed"] = True
            print(f"PASS: {FRAME_BYTES:,} bytes, mismatch=0, retries={self.result['retransmissions']}", flush=True)
        self.close()
        return self.result

    def close(self):
        """Leave the modem reset and release the session's board lock."""
        try:
            if self._control_in_use:
                self.link.write_control(self.base_control | 1)
        finally:
            self._control_in_use = False
            self.phase = "closed"
            if self._board_lock is not None:
                self._board_lock.close()
                self._board_lock = None


STOP_STAGES = ("metadata", "clocks", "download", "ps_bus", "rfdc")


def run(bitfile, image, output, amplitude=4096, timeout=30.0, stop_after=None):
    if stop_after is not None and stop_after not in STOP_STAGES:
        raise ValueError(f"stop_after must be one of {STOP_STAGES}")
    session = BoardRun(bitfile, image, output, amplitude, timeout)
    try:
        for stage, action in zip(STOP_STAGES, (session.inspect, session.program_clocks,
                                              session.download_overlay, session.check_ps_bus, session.check_rfdc)):
            action()
            if stop_after == stage:
                session.result["stopped_after"] = stage
                session._save_result()
                print(f"DIAGNOSTIC_COMPLETE stage={stage}", flush=True)
                return session.result
        session.upload()
        session.calibrate()
        return session.transfer()
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    release_dir = HERE if (HERE / "ofdm_rf.bit").exists() else HERE / "output"
    parser.add_argument("--bit", type=Path, default=release_dir / "ofdm_rf.bit")
    parser.add_argument("--input", type=Path, default=release_dir / "image.raw")
    parser.add_argument("--output", type=Path, default=HERE / "received")
    parser.add_argument("--amplitude", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--stop-after", choices=STOP_STAGES,
                        help="Stop after a startup stage. Metadata only checks files and parses the HWH.")
    args = parser.parse_args()
    run(args.bit, args.input, args.output, args.amplitude, args.timeout, args.stop_after)


if __name__ == "__main__":
    main()
