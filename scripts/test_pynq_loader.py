"""Check the loader and public APIs with official PYNQ 3.1.2 code, without FPGA I/O.

Requires pynqmetadata==0.1.9 and pynqutils<0.2. Use --fetch to cache the official
PYNQ metadata views and GPIO source under build/. GPIO and memory API tests use
register/byte-buffer fixtures; these do not test RFDC or an actual RF link.
"""
import argparse
import ast
import concurrent.futures
import hashlib
import importlib.util
from importlib.metadata import version
import json
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "build/pynq_source_3_1_2"
sys.path.insert(0, str(ROOT / "board"))
from run_on_board import (FRAME_BYTES, HOST_INTERFACE, HOST_SIGNATURE, PynqImageLinkIO,
                          metadata_region, prepare_overlay_files, run, verify_release_pair)


def fetch_sources():
    request = urllib.request.Request(
        "https://api.github.com/repos/Xilinx/PYNQ/contents/pynq/metadata?ref=v3.1.2",
        headers={"User-Agent": "OOK-loader-regression"})
    entries = json.load(urllib.request.urlopen(request, timeout=30))
    paths = [entry["path"] for entry in entries if entry["name"].endswith(".py")]
    paths.extend(("pynq/pl_server/embedded_device.py", "pynq/lib/axigpio.py"))

    def fetch(path):
        target = VENDOR / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(urllib.request.urlopen(
            "https://raw.githubusercontent.com/Xilinx/PYNQ/v3.1.2/" + path, timeout=30).read())
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, paths))


def metadata_classes():
    # Only import PYNQ's pure metadata code on Windows; the package initializer
    # would otherwise initialize ARM-specific device drivers.
    for name, path in (("pynq", VENDOR / "pynq"), ("pynq.metadata", VENDOR / "pynq/metadata")):
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
    from pynqmetadata.frontends import Metadata
    from pynqmetadata.frontends.metadata import UnknownInputFileExtension
    from pynq.metadata.runtime_metadata_parser import RuntimeMetadataParser
    source = (VENDOR / "pynq/pl_server/embedded_device.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BitstreamHandler")
    handler.body = [node for node in handler.body if isinstance(node, ast.FunctionDef)
                    and node.name in ("__init__", "is_xsa")]
    namespace = {"Path": Path}
    exec(compile(ast.Module(body=[handler], type_ignores=[]), "PYNQ-v3.1.2-handler", "exec"), namespace)
    return Metadata, UnknownInputFileExtension, RuntimeMetadataParser, namespace["BitstreamHandler"]


class RegisterFixture:
    def __init__(self, description):
        self.registers = {}

    def read(self, offset):
        return self.registers.get(offset, 0)

    def write(self, offset, value):
        self.registers[offset] = value


class BufferFixture:
    def __init__(self):
        self.data = bytearray(524288)

    def read(self, offset):
        return int.from_bytes(self.data[offset:offset + 4], "little")

    def write(self, offset, value):
        if isinstance(value, int):
            value = value.to_bytes(4, "little")
        self.data[offset:offset + len(value)] = value


def public_driver_classes():
    """Load the real channel/memory implementations over local test storage."""
    spec = importlib.util.spec_from_file_location("official_axigpio", VENDOR / "pynq/lib/axigpio.py")
    gpio_module = importlib.util.module_from_spec(spec)
    with patch.object(sys.modules["pynq"], "DefaultIP", RegisterFixture, create=True):
        spec.loader.exec_module(gpio_module)

    class MemoryBase:
        def __init__(self, device, description):
            pass

    tree = ast.parse((VENDOR / "pynq/pl_server/embedded_device.py").read_text(encoding="utf-8"))
    memory_class = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == "EmbeddedXrtMemory")
    namespace = {"XrtMemory": MemoryBase}
    exec(compile(ast.Module(body=[memory_class], type_ignores=[]), "PYNQ-v3.1.2-memory", "exec"), namespace)
    return gpio_module.AxiGPIO, namespace["EmbeddedXrtMemory"]


class LoaderRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bit = self.directory / "ook_rf.bit"
        for suffix in ("bit", "hwh"):
            shutil.copy2(ROOT / "board/output" / f"ook_rf.{suffix}", self.bit.with_suffix("." + suffix))
        self.manifest = self.directory / "manifest.json"
        self.manifest.write_text(json.dumps(dict(host_interface=HOST_INTERFACE, files={
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (self.bit, self.bit.with_suffix(".hwh"))})), encoding="utf-8")

    def test_original_layout_reproduces_reported_exception(self):
        self.bit.with_suffix(".xsa").write_bytes(b"export exists")
        self.assertTrue(Handler(self.bit).is_xsa())
        with self.assertRaises(UnknownInputFileExtension):
            Metadata(input=self.bit)

    def test_old_layout_is_loaded_from_an_exact_bit_hwh_pair(self):
        xsa = self.bit.with_suffix(".xsa")
        xsa.write_bytes(b"export exists")
        prepared = prepare_overlay_files(self.bit)
        self.assertNotEqual(prepared, self.bit)
        self.assertTrue(xsa.exists())
        self.assertFalse(Handler(prepared).is_xsa())
        for suffix in (".bit", ".hwh"):
            self.assertEqual(self.bit.with_suffix(suffix).read_bytes(), prepared.with_suffix(suffix).read_bytes())
        runtime = RuntimeMetadataParser(Metadata(input=prepared.with_suffix(".hwh")))
        self.assertIn("rfdc", runtime.ip_dict)
        self.assertNotIn("source_ctrl", runtime.ip_dict)
        self.assertIn("source_ctrl", runtime.mem_dict)
        for name, address, size in (("source_ctrl", 0xA0000000, 524288), ("sink_ctrl", 0xA0080000, 524288),
                                    ("control", 0xA0100000, 65536), ("telemetry", 0xA0110000, 65536)):
            self.assertEqual(metadata_region(runtime, name, address, size), (address, size))

    def test_clean_layout_uses_the_original_bit_hwh_pair(self):
        self.assertEqual(verify_release_pair(self.bit), self.bit)
        self.assertEqual(prepare_overlay_files(self.bit), self.bit)
        self.assertFalse(Handler(self.bit).is_xsa())

    def test_legacy_release_is_rejected_before_overlay_load(self):
        self.manifest.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "predates the AXI reset"):
            run(self.bit, ROOT / "assets/image.raw", self.directory / "logs")
        result = json.loads((self.directory / "logs/result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["failed_stage"], "metadata")

    def test_mixed_bit_hwh_pair_is_rejected_before_overlay_load(self):
        self.bit.with_suffix(".hwh").write_bytes(b"different hardware handoff")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch for ook_rf.hwh"):
            verify_release_pair(self.bit)

    def test_release_archive_has_no_xsa_beside_bitstream(self):
        with zipfile.ZipFile(ROOT / "board/ook_rf_rfsoc4x2.zip") as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("ook_rf.bit", archive.namelist())
            self.assertIn("ook_rf.hwh", archive.namelist())
            self.assertIn("hardware_description/ook_rf.xsa", archive.namelist())
            self.assertNotIn("ook_rf.xsa", archive.namelist())
            self.assertEqual(archive.read("run_on_board.py"), (ROOT / "board/run_on_board.py").read_bytes())
            self.assertEqual(archive.read("run_on_board.ipynb"), (ROOT / "board/run_on_board.ipynb").read_bytes())
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["host_interface"], HOST_INTERFACE)
            for name, expected in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)

    def driver_fixture(self):
        runtime = RuntimeMetadataParser(Metadata(input=self.bit.with_suffix(".hwh")))
        gpio_class, memory_class = public_driver_classes()
        source, sink = BufferFixture(), BufferFixture()
        source_memory, sink_memory = memory_class(None, {}), memory_class(None, {})
        source_memory._mmio, sink_memory._mmio = source, sink
        overlay = types.SimpleNamespace(ip_dict=runtime.ip_dict, mem_dict=runtime.mem_dict,
                                        control=gpio_class({"interrupts": {}}),
                                        telemetry=gpio_class({"interrupts": {}}),
                                        host_state=gpio_class({"interrupts": {}}),
                                        source_ctrl=source_memory, sink_ctrl=sink_memory)
        return overlay, source, sink

    def test_official_gpio_and_memory_apis_preserve_channels_and_image_bytes(self):
        overlay, source, sink = self.driver_fixture()
        link = PynqImageLinkIO(overlay)
        overlay.host_state.registers[0] = HOST_SIGNATURE
        self.assertFalse(link.rf_ready())
        with self.assertRaisesRegex(RuntimeError, "PS AXI is responding"):
            link.read_status()
        overlay.host_state.registers[0] = HOST_SIGNATURE | 1
        overlay.control.registers[8] = 0x9e000008
        overlay.telemetry.registers.update({0: 0x12340456, 8: 7})
        for word in (0xfedcba07, 0x04004001):
            link.write_control(word)
            self.assertEqual(overlay.control.registers[0], word)
            self.assertEqual(link.read_status(), 0x9e000008)
        self.assertEqual(link.read_levels(), 0x12340456)
        self.assertEqual(link.read_retries(), 7)
        with self.assertRaises(RuntimeError):
            link.status.write(1, 0xffffffff)
        with self.assertRaises(RuntimeError):
            link.control.read()
        pixels = (ROOT / "assets/image.raw").read_bytes()
        link.upload_image(pixels)
        self.assertEqual(bytes(source.data[:FRAME_BYTES]), pixels)
        self.assertEqual(bytes(source.data[FRAME_BYTES:]), bytes(524288 - FRAME_BYTES))
        self.assertEqual(bytes(sink.data[:FRAME_BYTES]), b"\xef\xbe\xad\xde" * (FRAME_BYTES // 4))
        sink.data[:FRAME_BYTES] = pixels  # Memory API check, not an RF simulation.
        self.assertEqual(link.received_image(), pixels)

    def test_driver_binding_rejects_mismatched_hwh_before_accessing_ips(self):
        overlay = types.SimpleNamespace(ip_dict={"control": {"phys_addr": 0, "addr_range": 65536}})
        with self.assertRaisesRegex(RuntimeError, "Unexpected HWH memory map"):
            PynqImageLinkIO(overlay)

    def test_image_upload_detects_bram_readback_corruption(self):
        overlay, source, _ = self.driver_fixture()
        link = PynqImageLinkIO(overlay)
        with patch.object(source, "read", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "upload/readback mismatch"):
                link.upload_image((ROOT / "assets/image.raw").read_bytes())

    def test_application_has_no_direct_mmio_or_mapped_array_access(self):
        tree = ast.parse((ROOT / "board/run_on_board.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.assertFalse(any(item.name.split(".")[-1].lower() == "mmio" for item in node.names))
            elif isinstance(node, ast.Name):
                self.assertNotEqual(node.id.lower(), "mmio")
            elif isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr.lower(), ("mmio", "array"))

    def check_stage_boundary(self, stage, expected_calls):
        calls = []
        runtime = RuntimeMetadataParser(Metadata(input=self.bit.with_suffix(".hwh")))

        class StagedOverlay:
            def __init__(self, path, download=True):
                calls.append(("Overlay", download))
                self.ip_dict = runtime.ip_dict
                self.mem_dict = runtime.mem_dict

            def download(self):
                calls.append("download")

            @property
            def rfdc(self):
                raise AssertionError("RFDC register access crossed the diagnostic stop boundary")

        def forbidden_mmio(*args, **kwargs):
            raise AssertionError("MMIO access crossed the diagnostic stop boundary")

        clock_module = types.ModuleType("xrfclk")
        clock_module.set_ref_clks = lambda **kwargs: calls.append("clocks")
        pynq_module = sys.modules["pynq"]
        with patch.object(pynq_module, "Overlay", StagedOverlay, create=True), \
                patch.object(pynq_module, "MMIO", forbidden_mmio, create=True), \
                patch.dict(sys.modules, {"xrfdc": types.ModuleType("xrfdc"), "xrfclk": clock_module}):
            result = run(self.bit, ROOT / "assets/image.raw", self.directory / "logs", stop_after=stage)
        self.assertEqual(calls, expected_calls)
        self.assertEqual(result["stopped_after"], stage)
        self.assertFalse(result["physical_board_tested"])
        log = (self.directory / "logs/bringup.log").read_text(encoding="utf-8")
        self.assertIn("OK " + stage, log)

    def test_metadata_diagnostic_never_programs_clocks_or_fpga(self):
        self.check_stage_boundary("metadata", [("Overlay", False)])

    def test_download_diagnostic_never_accesses_rfdc_or_mmio(self):
        self.check_stage_boundary("download", [("Overlay", False), "clocks", "download"])

    def test_notebook_runs_staged_flow_and_displays_verified_image(self):
        overlay, source, sink = self.driver_fixture()
        shutil.copy2(ROOT / "assets/image.raw", self.directory / "image.raw")
        overlay.download = lambda: None
        overlay.host_state.registers[0] = HOST_SIGNATURE | 1
        overlay.control.registers[8] = 0x4f << 25
        write_register = overlay.control.write

        def apply_command(offset, word):
            write_register(offset, word)
            overlay.host_state.registers[8] = word
            overlay.telemetry.registers[0] = 1500 if word & 4 else 10
            if word & 1:
                overlay.control.registers[8] = 0x4f << 25
            if word & 2:
                sink.data[:FRAME_BYTES] = source.data[:FRAME_BYTES]
                overlay.control.registers[8] = (0x4f << 25) | (FRAME_BYTES << 6) | 9

        overlay.control.write = apply_command

        def tile(frequency, mode):
            block = types.SimpleNamespace(MixerSettings=dict(Freq=frequency, MixerMode=mode, MixerType=2),
                                          BlockStatus={})
            return types.SimpleNamespace(PLLLockStatus=2, FIFOStatus=1, blocks=[block, block])

        ready = [{}, {}, {"TileState": 15}]
        overlay.rfdc = types.SimpleNamespace(adc_tiles=[None, None, tile(-500, 3)],
                                             dac_tiles=[None, None, tile(500, 2)],
                                             IPStatus=dict(ADCTileStatus=ready, DACTileStatus=ready))
        converters = types.ModuleType("xrfdc")
        converters.MIXER_MODE_R2C, converters.MIXER_MODE_C2R, converters.MIXER_TYPE_FINE = 3, 2, 2
        clocks = types.ModuleType("xrfclk")
        clocks.set_ref_clks = lambda **kwargs: None
        shown = []
        display = types.ModuleType("IPython.display")
        display.display = shown.append
        notebook_path = ROOT / "board/run_on_board.ipynb"
        notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
        namespace = {}
        with patch.object(sys.modules["pynq"], "Overlay", lambda *args, **kwargs: overlay, create=True), \
                patch.dict(sys.modules, {"xrfdc": converters, "xrfclk": clocks, "IPython.display": display}), \
                patch.object(Path, "cwd", return_value=self.directory), patch("run_on_board.time.sleep"):
            for index, cell in enumerate(notebook["cells"], start=1):
                if cell["cell_type"] == "code":
                    exec(compile("".join(cell["source"]), f"{notebook_path} cell {index}", "exec"), namespace)
        self.assertTrue(namespace["result"]["passed"])
        self.assertEqual(namespace["result"]["mismatched_bytes"], 0)
        self.assertTrue(overlay.control.registers[0] & 1)
        self.assertEqual([picture.size for picture in shown], [(1280, 480)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--skip-package", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        fetch_sources()
    global Metadata, UnknownInputFileExtension, RuntimeMetadataParser, Handler
    Metadata, UnknownInputFileExtension, RuntimeMetadataParser, Handler = metadata_classes()
    names = unittest.defaultTestLoader.getTestCaseNames(LoaderRegression)
    if args.skip_package:
        names.remove("test_release_archive_has_no_xsa_beside_bitstream")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(LoaderRegression(name) for name in names))
    destination = ROOT / "reports/pynq_loader"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "result.json").write_text(json.dumps(dict(
        passed=result.wasSuccessful(), tests_run=result.testsRun,
        pynq_source="Xilinx/PYNQ v3.1.2", pynqmetadata=version("pynqmetadata"), physical_board_tested=False,
        checked=names,
        application_sha256=hashlib.sha256((ROOT / "board/run_on_board.py").read_bytes()).hexdigest(),
        bitstream_sha256=hashlib.sha256((ROOT / "board/output/ook_rf.bit").read_bytes()).hexdigest()), indent=2)+"\n", encoding="utf-8")
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
