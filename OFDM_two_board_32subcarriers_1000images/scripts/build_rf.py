"""Build the OFDM overlay after full simulation, then verify and package it."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit_hwh(path):
    tree = ET.parse(path)
    modules = {node.get("INSTANCE"): node for node in tree.findall(".//MODULE")}
    for name, module_type in (("modem", "ofdm_rf_board"), ("host", "ofdm_rf_host")):
        if modules[name].get("MODTYPE") != module_type:
            raise ValueError(f"HWH has the wrong module for {name}: expected {module_type}")
    all_parameters = {name: {p.get("NAME"): p.get("VALUE") for p in module.findall("./PARAMETERS/PARAMETER")}
                      for name, module in modules.items()}
    ports = {name: {p.get("NAME"): p.get("SIGNAME") for p in module.findall("./PORTS/PORT")}
             for name, module in modules.items()}

    def check_connection(instance, pin, source, source_pin):
        net = ports[instance].get(pin)
        if not net or net != ports[source].get(source_pin):
            raise ValueError(f"HWH connection mismatch: {instance}/{pin} must connect to {source}/{source_pin}")

    for name, value in (("zero", 0), ("one", 1)):
        if int(all_parameters[name]["CONST_VAL"], 0) != value:
            raise ValueError(f"HWH has wrong reset constant: {name}")
    for reset in ("ps_reset", "rf_reset"):
        for key in ("C_EXT_RESET_HIGH", "C_AUX_RESET_HIGH"):
            if all_parameters[reset][key] != "0":
                raise ValueError(f"HWH reset polarity mismatch: {reset}/{key}")
        check_connection(reset, "ext_reset_in", "ps", "pl_resetn0")
        check_connection(reset, "aux_reset_in", "one", "dout")
        check_connection(reset, "mb_debug_sys_rst", "zero", "dout")
    check_connection("ps_reset", "slowest_sync_clk", "ps", "pl_clk0")
    check_connection("ps_reset", "dcm_locked", "one", "dout")
    check_connection("rf_reset", "slowest_sync_clk", "rf_clock", "clk_out1")
    check_connection("rf_reset", "dcm_locked", "rf_clock", "locked")

    check_connection("axi", "aclk", "ps", "pl_clk0")
    check_connection("axi", "aresetn", "ps_reset", "peripheral_aresetn")
    check_connection("ps", "maxihpm0_fpd_aclk", "ps", "pl_clk0")
    for name in ("rfdc", "control", "telemetry", "host_state", "source_ctrl", "sink_ctrl"):
        check_connection(name, "s_axi_aclk", "ps", "pl_clk0")
        check_connection(name, "s_axi_aresetn", "ps_reset", "peripheral_aresetn")
    check_connection("host", "ps_clk", "ps", "pl_clk0")
    check_connection("host", "ps_aresetn", "ps_reset", "peripheral_aresetn")
    check_connection("host", "rf_clk", "rf_clock", "clk_out1")
    check_connection("host", "rf_aresetn", "rf_reset", "peripheral_aresetn")
    check_connection("modem", "clk", "rf_clock", "clk_out1")
    check_connection("modem", "aresetn", "rf_reset", "peripheral_aresetn")
    for port, interface in (("adc_i", "m22_axis"), ("adc_q", "m23_axis"), ("dac", "s20_axis")):
        for suffix, axi_suffix in (("data", "tdata"), ("valid", "tvalid"), ("ready", "tready")):
            modem_pin = port if port.startswith("adc_") and suffix == "data" else f"{port}_{suffix}"
            check_connection("modem", modem_pin, "rfdc", f"{interface}_{axi_suffix}")
    for name, modem_port in (("source", "src_clk"), ("sink", "dst_clk")):
        ram = name + "_ram"
        if all_parameters[ram]["Assume_Synchronous_Clk"].lower() != "false":
            raise ValueError(f"HWH requires independent BRAM clocks: {ram}")
        check_connection(ram, "clka", name + "_ctrl", "bram_clk_a")
        check_connection(ram, "clkb", "modem", modem_port)

    parameters = all_parameters["rfdc"]
    expected = dict(C_ADC0_Enable="0", C_ADC2_Enable="1", C_DAC0_Enable="0", C_DAC2_Enable="1",
                    C_ADC2_Fabric_Freq="153.6", C_DAC2_Fabric_Freq="153.6",
                    C_ADC2_Refclk_Freq="491.52", C_DAC2_Refclk_Freq="491.52",
                    C_ADC2_Sampling_Rate="4.9152", C_DAC2_Sampling_Rate="4.9152",
                    C_ADC_Data_Width22="2", C_ADC_Data_Width23="2", C_DAC_Data_Width20="4",
                    C_ADC_Decimation_Mode22="16", C_DAC_Interpolation_Mode20="16",
                    C_ADC_Data_Type22="1", C_DAC_Data_Type20="0",
                    C_ADC_Mixer_Type22="2", C_DAC_Mixer_Type20="2",
                    C_ADC_NCO_Freq22="-0.5", C_DAC_NCO_Freq20="0.5")
    for key, value in expected.items():
        if Decimal(parameters[key]) != Decimal(value):
            raise ValueError(f"HWH configuration mismatch: {key}={parameters[key]}, expected {value}")
    mapping = {node.get("INSTANCE"): dict(base=int(node.get("BASEVALUE"), 16),
               size=int(node.get("HIGHVALUE"), 16)-int(node.get("BASEVALUE"), 16)+1)
               for node in modules["ps"].findall(".//MEMRANGE")}
    for name, address, size in (("source_ctrl", 0xA0000000, 524288), ("sink_ctrl", 0xA0080000, 524288),
                                ("control", 0xA0100000, 65536), ("telemetry", 0xA0110000, 65536),
                                ("host_state", 0xA0120000, 65536), ("rfdc", 0xA0140000, 262144)):
        if mapping[name] != dict(base=address, size=size):
            raise ValueError(f"HWH memory map mismatch: {name}")
    return dict(rfdc={key: parameters[key] for key in expected}, memory_map=mapping,
                host_interface="ofdm_air8_v1", ps_axi_clock_mhz=100.0,
                rf_datapath_clock_mhz=153.6, aux_reset_inactive_level=1,
                ofdm=dict(fft_size=64, active_subcarriers=32, cp_samples=16,
                          sample_rate_msps=19.2, data_symbols_per_training=2,
                          gross_bit_rate_bps=15360000, data_mhz=500, ack_mhz=500, window_packets=8, ack_modulation="diagonal BPSK"))


def find_vivado(requested=None):
    candidates = [requested, os.environ.get("XILINX_VIVADO"), os.environ.get("VIVADO_HOME"),
                  r"D:\Vivado\2024.2", r"C:\Xilinx\Vivado\2024.2"]
    on_path = shutil.which("vivado") or shutil.which("vivado.bat")
    if on_path:
        candidates.insert(1, str(Path(on_path).parent.parent))
    for candidate in candidates:
        if candidate:
            executable = Path(candidate) / "bin" / ("vivado.bat" if os.name == "nt" else "vivado")
            if executable.is_file():
                return executable
    raise FileNotFoundError("Vivado 2024.2 was not found; pass --vivado-home")



SIMULATION_REPORT = "reports/batch/result.json"
IQ_REPORT = "reports/iq/result.json"
IQ_CASES = ("clean","fading","reverse","window1","window4","qpsk_ack","full_frames","host")
def data_dir():
    return Path(os.environ.get("AIR8_SOURCE_DIR", ROOT.parent / "data"))

FRAME_BYTES = 640 * 480
RELEASE_RECEIPT = "build/rf/release_receipt.json"
TIMING_SCRIPTS = ("rf_clock_constraints.tcl", "rf_cdc_waivers.tcl",
                  "verify_rf_timing.tcl", "check_rf_bus_skew.tcl")
IMPLEMENTATION_REPORTS = ("timing_summary_routed.rpt", "drc.rpt", "route_status.rpt",
                          "cdc.rpt", "methodology.rpt", "cdc_waivers.xdc", "bus_skew.rpt",
                          "hold_paths.rpt", "clock_interaction.rpt", "utilization.rpt")
HARDWARE_FILES = ("ofdm_air8.bit", "ofdm_air8.hwh", "hardware_description/ofdm_air8.xsa")
SUPPORT_FILES = {"image.raw": "assets/image.raw", "source.png": "assets/source.png",
                 "run_on_board.py": "board/run_on_board.py", "run_on_board.ipynb": "board/run_on_board.ipynb",
                 "verification/iq.json": IQ_REPORT, "README.md": "docs/rfsoc4x2_rf_bringup.md", "verification/result.json": SIMULATION_REPORT}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rtl_paths():
    return {path.relative_to(ROOT).as_posix()
            for directory in ("rtl", "board/rtl")
            for path in (ROOT / directory).iterdir()
            if path.is_file() and path.suffix in (".v", ".sv", ".vh", ".svh")}


def check_simulation():
    batch = json.loads((ROOT / SIMULATION_REPORT).read_text(encoding="utf-8"))
    iq = json.loads((ROOT / IQ_REPORT).read_text(encoding="utf-8"))
    if (batch["passed"] is not True or iq["passed"] is not True or batch["frames"] != 1000
        or batch["packets"] != 300000 or batch["bytes"] != 307200000 or batch["block_acks"] != 38000
        or batch["mismatched_bytes"] != 0 or len(batch["images"]) != 1000
        or set(batch["faults"]) != {"damaged_payload_ack_close_repaired","retry_exhaustion","disconnect"}):
        raise ValueError("The complete 1,000-frame RTL test is required")
    for name in IQ_CASES:
        evidence = iq["tests"][name]
        if name == "host":
            if evidence["passed"] is not True:
                raise ValueError("Host clock crossing test failed")
        elif not evidence.startswith("AIR_PASS frames=2 packets=" + ("300 " if name=="full_frames" else "12 ")):
            raise ValueError(f"Incomplete I/Q test: {name}")
    hashes = {}
    for record in (batch, iq):
        for name, expected in record["source_files"].items():
            if not (ROOT/name).resolve().is_relative_to(ROOT) or sha256(ROOT/name)!=expected:
                raise ValueError(f"Stale simulation: {name}")
            hashes[name] = expected
    required = rtl_paths() | {"sim/batch.cpp","sim/fast_phy.v","sim/tb_rf_board.sv","sim/rf_iq_channel.sv",
        "sim/tb_rf_host.sv","scripts/test_batch.py","scripts/test_iq.py","scripts/test_rf.py"}
    if required - hashes.keys(): raise ValueError("Missing simulation input fingerprints")
    with (ROOT/"build/batch/received.raw").open("rb") as received:
        for n,row in enumerate(batch["images"]):
            name=f"coco_gray_{n:04}.png"
            data=received.read(FRAME_BYTES)
            if (row["name"]!=name or sha256(data_dir()/name)!=row["png_sha256"] or len(data)!=FRAME_BYTES
                or hashlib.sha256(data).hexdigest()!=row["source_sha256"] or row["source_sha256"]!=row["received_sha256"]):
                raise ValueError(f"Invalid source or received BRAM dump for {name}")
        if received.read(1): raise ValueError("Oversized BRAM dump")
    if iq["input_sha256"]!=[row["source_sha256"] for row in batch["images"][:2]]:
        raise ValueError("I/Q source image mismatch")
    for name in (SIMULATION_REPORT, IQ_REPORT):
        hashes[name]=sha256(ROOT/name)
    return hashes


def build_inputs():
    sources = dict(check_simulation())
    for name in ("scripts/build_rf.py", "scripts/build_rf.tcl", "constraints/rfsoc4x2_rf.xdc",
                 "board/run_on_board.py", "board/run_on_board.ipynb", "docs/rfsoc4x2_rf_bringup.md",
                 *(f"scripts/{script}" for script in TIMING_SCRIPTS)):
        sources[name] = sha256(ROOT / name)
    fingerprint = hashlib.sha256(json.dumps(sources, sort_keys=True).encode("utf-8")).hexdigest()
    return sources, fingerprint


def hardware_artifacts():
    return {**{name: ROOT / "build/rf/artifacts" / name for name in HARDWARE_FILES},
            **{f"verification/{name}": ROOT / "reports/implementation_rf" / name
               for name in IMPLEMENTATION_REPORTS}}


def read_timing(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) == 12 and all(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", field) for field in fields):
            timing = dict(wns_ns=float(fields[0]), tns_ns=float(fields[1]),
                          setup_failing_endpoints=int(fields[2]), whs_ns=float(fields[4]),
                          ths_ns=float(fields[5]), hold_failing_endpoints=int(fields[6]),
                          wpws_ns=float(fields[8]), pulse_width_failing_endpoints=int(fields[10]))
            if (min(timing["wns_ns"], timing["whs_ns"], timing["wpws_ns"]) < 0
                    or any(timing[name] for name in ("setup_failing_endpoints", "hold_failing_endpoints",
                                                    "pulse_width_failing_endpoints"))):
                raise RuntimeError("Package refused: routed timing failed")
            return timing
    raise RuntimeError("Cannot extract routed timing summary")


def read_drc(path):
    text = path.read_text(encoding="utf-8")
    count = re.search(r"Checks found:\s+(\d+)\b", text)
    rows = re.findall(r"^\|\s*([A-Z]+-\d+)\s*\|\s*([^|]+?)\s*\|[^|]*\|\s*(\d+)\s*\|",
                      text, flags=re.MULTILINE)
    # These DSP pipelining checks are performance advisories. Routed timing
    # still has to meet every constraint, and the report retains every check.
    advisory = {"DPIP-2", "DPOP-3", "DPOP-4"}
    if (count is None or int(count.group(1)) != sum(int(total) for _, _, total in rows)
            or any(rule not in advisory or severity != "Warning" for rule, severity, _ in rows)):
        raise RuntimeError("Release refused: unexpected or blocking DRC findings")
    return {rule: int(total) for rule, _, total in rows}


def check_hardware(artifacts):
    def read_report(name):
        return artifacts[f"verification/{name}"].read_text(encoding="utf-8", errors="replace")

    timing = read_timing(artifacts["verification/timing_summary_routed.rpt"])
    drc_warnings = read_drc(artifacts["verification/drc.rpt"])
    if not re.search(r"nets with routing errors\.*\s*:\s+0\s+:", read_report("route_status.rpt")):
        raise RuntimeError("Release refused: routing errors remain")
    if re.search(r"Critical Warning|[ \t]Error[ \t]", read_report("methodology.rpt")):
        raise RuntimeError("Release refused: critical methodology remains")
    if re.search(r"CDC-\d+\s+Critical\s+[1-9]", read_report("cdc.rpt")):
        raise RuntimeError("Release refused: critical CDC remains")
    bus_skew = read_report("bus_skew.rpt")
    if "Slack (VIOLATED)" in bus_skew or "Slack (MET)" not in bus_skew:
        raise RuntimeError("Release refused: bus skew failed")
    return timing, audit_hwh(artifacts["ofdm_air8.hwh"]), drc_warnings


def record_build(expected):
    _, fingerprint = build_inputs()
    if fingerprint != expected:
        raise RuntimeError("OFDM build inputs changed during hardware export")
    artifacts = hardware_artifacts()
    check_hardware(artifacts)
    receipt = dict(input_fingerprint=fingerprint,
                   files={name: sha256(path) for name, path in artifacts.items()})
    target = ROOT / RELEASE_RECEIPT
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


@contextmanager
def vivado_workspace():
    # Keep the generated IP and implementation paths short on Windows.
    if os.name != "nt":
        yield ROOT
        return
    for letter in "ZYXWVUTSRQPONMLKJIHGFED":
        drive = f"{letter}:"
        if Path(drive + "/").exists():
            continue
        result = subprocess.run(["subst", drive, str(ROOT)], capture_output=True, check=False)
        if result.returncode == 0:
            try:
                yield Path(drive + "/")
            finally:
                subprocess.run(["subst", drive, "/d"], check=True)
            return
    raise RuntimeError("No free Windows drive letter for Vivado. Free a drive letter and retry.")


def run_vivado(executable):
    (ROOT / "build").mkdir(exist_ok=True)
    receipt = ROOT / RELEASE_RECEIPT
    receipt.unlink(missing_ok=True)
    arguments = [str(executable), "-mode", "batch", "-notrace", "-source", "scripts/build_rf.tcl",
                 "-log", "build/rf_build.log", "-journal", "build/rf_build.jou"]
    env = dict(os.environ, OFDM_BUILD_PYTHON=sys.executable, AIR8_SOURCE_DIR=str(data_dir().resolve()))
    with vivado_workspace() as cwd:
        if os.name == "nt":
            if any(any(c in arg for c in '\r\n"%!&|<>^') for arg in arguments):
                raise ValueError("Unsupported shell characters in the Vivado launcher arguments")
            launcher = cwd / "build/rf_build_launch.cmd"
            command = " ".join(f'"{arg}"' for arg in arguments)
            launcher.write_text(f"@echo off\ncall {command}\nexit /b %errorlevel%\n", encoding="utf-8")
            arguments = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "build\\rf_build_launch.cmd"]
        subprocess.run(arguments, cwd=cwd, env=env, check=True)
    if not receipt.is_file():
        for log in (ROOT / "build/rf/vivado/ofdm32.runs").glob("*synth_1/runme.log"):
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                if "[Common 17-348]" in line or "[Common 17-345]" in line:
                    raise RuntimeError(f"Vivado synthesis blocked: {line} ({log})")
        raise RuntimeError("Vivado did not complete the OFDM release gate. See build/rf_build.log.")


def package():
    sources, fingerprint = build_inputs()
    receipt_path = ROOT / RELEASE_RECEIPT
    if not receipt_path.is_file():
        raise RuntimeError("No completed OFDM routed build. Run python scripts/build_rf.py.")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    artifacts = hardware_artifacts()
    if (receipt.get("input_fingerprint") != fingerprint
            or receipt.get("files") != {name: sha256(path) for name, path in artifacts.items()}):
        raise RuntimeError("OFDM routed build is stale or its verified artifacts changed.")
    timing, configuration, drc_warnings = check_hardware(artifacts)
    board = ROOT / "board"
    out = board / "output"
    archive = board / "ofdm_air8_rfsoc4x2.zip"
    with tempfile.TemporaryDirectory(prefix=".ofdm-release-", dir=board) as temp:
        stage = Path(temp) / "bundle"
        for name, source in {**artifacts, **{name: ROOT / path for name, path in SUPPORT_FILES.items()}}.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for row in json.loads((ROOT/SIMULATION_REPORT).read_text())["images"]:
            image = stage / "data" / row["name"]
            image.parent.mkdir(exist_ok=True)
            shutil.copyfile(data_dir()/row["name"],image)
            if sha256(image)!=row["png_sha256"]: raise RuntimeError("Release image checksum mismatch")
        (stage / "configuration.json").write_text(json.dumps(configuration, indent=2) + "\n", encoding="utf-8")
        files = {path.relative_to(stage).as_posix(): sha256(path) for path in sorted(stage.rglob("*")) if path.is_file()}
        if any(files[name] != expected for name, expected in receipt["files"].items()):
            raise RuntimeError("Verified hardware artifacts changed during packaging.")
        manifest = dict(board="RFSoC4x2", part="xczu48dr-ffvg1517-2-e", vivado="2024.2",
                        phy="ofdm", topology="four_antennas_same_frequency_tdd", modem_status_id=0x41, host_interface=configuration["host_interface"],
                        physical_board_tested=False, full_simulation_passed=True,
                        routed_timing_and_drc_passed=True, routed_timing=timing,
                        drc_advisory_warnings=drc_warnings,
                        source_files=sources, files=files)
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        pending_archive = shutil.make_archive(str(Path(temp) / archive.stem), "zip", stage)
        if build_inputs()[1] != fingerprint or files["verification/result.json"] != sha256(ROOT / SIMULATION_REPORT):
            raise RuntimeError("OFDM simulation evidence or inputs changed during packaging.")
        for name in (*files, "manifest.json"):
            target = out / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stage / name, target)
        os.replace(pending_archive, archive)
    print(f"RF release package: {archive}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vivado-home", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--package-only", action="store_true", help="Package the verified build for the current sources")
    mode.add_argument("--check-simulation", action="store_true", help="Verify the full simulation and print the input fingerprint")
    mode.add_argument("--record-build", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.record_build is not None:
        record_build(args.record_build)
        return
    _, fingerprint = build_inputs()
    if args.check_simulation:
        print(fingerprint)
        return
    if not args.package_only:
        run_vivado(find_vivado(args.vivado_home))
    package()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
