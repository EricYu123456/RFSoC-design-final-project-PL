"""PYNQ bring-up and BRAM access for the two-board webcam notebook."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
import shutil
import statistics
import time
from pathlib import Path

import numpy as np

FRAME_BYTES = 640 * 480
PACKET_BYTES = 1024
PACKET_COUNT = FRAME_BYTES // PACKET_BYTES
QUALITY_OFFSET = FRAME_BYTES
PROFILES = {"ofdm_qam16_v1": (0x42, "4 carriers / 16-QAM"),
            "ofdm_air8_v1": (0x41, "32 carriers / QPSK, BPSK ACK")}
HOST_SIGNATURE = 0x4f4f4b20  # Shared PS host register ABI.
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
        raise FileNotFoundError("Keep the bitstream, matching .hwh, and manifest.json together")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("board") != "RFSoC4x2" or manifest.get("phy") != "ofdm"
            or manifest.get("host_interface") not in PROFILES
            or manifest.get("modem_status_id") != PROFILES[manifest["host_interface"]][0]):
        raise RuntimeError("Extract the RFSoC 4x2 OFDM release with the matching host and modem interfaces.")
    hashes = {path.name: digest(path.read_bytes()) for path in (bitfile, hwh)}
    for name, sha256 in hashes.items():
        if sha256 != manifest["files"].get(name):
            raise RuntimeError(f"Release checksum mismatch for {name}. Extract the matching bit/HWH pair.")
    return bitfile, hashes, manifest["host_interface"]


def prepare_overlay_files(bitfile):
    """Keep PYNQ's .bit/HWH path separate from a same-name XSA export."""
    bitfile = Path(bitfile).resolve()
    hwh = bitfile.with_suffix(".hwh")
    if bitfile.suffix.lower() != ".bit" or not bitfile.is_file() or not hwh.is_file():
        raise FileNotFoundError("Keep the matching bitstream and HWH in the same directory")
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
    """Use the validated overlay's named GPIO channels and 32-bit BRAM banks."""

    def __init__(self, overlay, status_id):
        self.overlay = overlay
        self.status_id = status_id
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

    def bram_words(self, name):
        """Use the named PYNQ bank only after the downloaded modem responds."""
        if name not in ("source_ctrl", "sink_ctrl"):
            raise ValueError(f"Unknown BRAM bank {name}")
        self.read_status()
        _, address, size = next(region for region in REGIONS if region[0] == name)
        bank = getattr(self.overlay, name)
        if (bank.base_address, bank.size) != (address, size):
            raise RuntimeError(f"Unexpected PYNQ bank for {name}")
        words = bank.mmio.array
        if (not isinstance(words, np.ndarray) or words.dtype != np.dtype("<u4")
                or words.shape != (size // 4,) or not words.flags.c_contiguous
                or words.ctypes.data % 4):
            raise RuntimeError(f"Unexpected BRAM word layout for {name}")
        return words

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
        # The control GPIO is clocked by the PS. Leave RESET requested there
        # if the RF clock stops so that it takes effect when the clock returns.
        if value & RESET:
            self.rf_ready()  # Check the PS signature even if RF is down.
        else:
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
        if word >> 25 != self.status_id:
            raise RuntimeError(f"Unexpected OFDM modem status 0x{word:08x}")
        return word

    def read_levels(self):
        return int(self.levels.read())

    def read_packet_counter(self):
        return int(self.packet_counter.read())

    def clear_sink(self):
        from webcam_native import native_library, write_words
        words = self.bram_words("sink_ctrl")[:(QUALITY_OFFSET + PACKET_COUNT) // 4]
        write_words(words, np.zeros(words.size, dtype="<u4"), native_library())

def decode_status(value, role):
    count = (value >> 6) & 0x7ffff
    state = dict(done=bool(value & 1), failed=bool(value & 2), busy=bool(value & 4),
                 transport_fault=bool(value & 16), address_fault=bool(value & 32))
    if role == "send":
        state["exhausted_packets"] = count
    else:
        state.update(received_complete=bool(value & 8), unverified_packets=count)
    return state


class BoardRun:
    """Bring up one endpoint of the two-board RF link."""

    def __init__(self, role, bitfile, output, *, amplitude=4096, timeout=35.0, wait=180.0):
        if role not in ("send", "receive"):
            raise ValueError("role must be send or receive")
        if not isinstance(amplitude, int) or not 64 <= amplitude <= 16380 or amplitude % 4:
            raise ValueError("amplitude must be a multiple of four in 64..16380")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if not math.isfinite(wait) or wait <= 0:
            raise ValueError("wait must be positive and finite")
        self.role = role
        self.bitfile = Path(bitfile)
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
        self.result = dict(role=role, board="RFSoC4x2", amplitude=amplitude)

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
            bitfile, hashes, profile = verify_release_pair(self.bitfile)
            self.status_id, modulation = PROFILES[profile]
            self.result.update(files=hashes, profile=profile, modulation=modulation)
            print(f"Loaded release profile: {modulation}", flush=True)
            os.environ.setdefault("BOARD", "RFSoC4x2")
            import xrfdc  # Register the RFDC driver before Overlay binds its IP.
            from pynq import Overlay
            self.xrfdc = xrfdc
            # Even download=False changes the process-wide PYNQ device metadata.
            if os.name == "posix":
                import fcntl
                self._board_lock = open("/tmp/ook-rfsoc4x2.lock", "a", encoding="utf-8")
                try:
                    fcntl.flock(self._board_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise RuntimeError("Another RFSoC 4x2 session is active.") from error
            bitfile = prepare_overlay_files(bitfile)
            self.result["loader_bitfile"] = str(bitfile)
            self.overlay = Overlay(str(bitfile), download=False)
            for name, address, size in REGIONS:
                metadata_region(self.overlay, name, address, size)

    def program_clocks(self):
        """Program the RFSoC 4x2 reference clocks before downloading the PL."""
        with self._step("clocks", "metadata"):
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
            self.link = PynqImageLinkIO(self.overlay, self.status_id)
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
                # The overlay configures both NCOs. Preserve the high-speed ADC's event source.
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

    def read_state(self):
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
            state = self.read_state()
            if state["done"]:
                return state
            time.sleep(0.002)
        raise TimeoutError("Timed out waiting for the radio frame handshake")

    def _reset_modem(self):
        if self._control_in_use:
            self._control_in_use = False
            self.link.apply_control(self.base_control | RESET)

    def close(self):
        """Leave the modem reset and release the session's board lock."""
        try:
            self._reset_modem()
        finally:
            if self._board_lock is not None:
                self._board_lock.close()
                self._board_lock = None


