"""Build and package the RFSoC4x2 RF image link; release only after timing passes."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit_hwh(path):
    tree = ET.parse(path)
    modules = {node.get("INSTANCE"): node for node in tree.findall(".//MODULE")}
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
                    C_ADC_Decimation_Mode22="16", C_DAC_Interpolation_Mode20="16")
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
                host_interface="ps_clock_v2", ps_axi_clock_mhz=100.0,
                rf_datapath_clock_mhz=153.6, aux_reset_inactive_level=1)


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vivado-home", type=Path)
    parser.add_argument("--package-only", action="store_true", help="Package an already verified build")
    args = parser.parse_args()
    out = ROOT / "board/output"
    if not args.package_only:
        executable = find_vivado(args.vivado_home)
        arguments = [str(executable), "-mode", "batch", "-source", "scripts/build_rf.tcl",
                     "-log", "build/rf_build.log", "-journal", "build/rf_build.jou"]
        if os.name == "nt":
            # Quotes must survive both CMD and Vivado's batch launcher.
            if any(any(c in argument for c in '\r\n"%!&|<>^') for argument in arguments):
                raise ValueError("Unsupported shell characters in Vivado path")
            launcher = ROOT / "build/rf_build_launch.cmd"
            launcher.parent.mkdir(exist_ok=True)
            launcher.write_text("@echo off\ncall " + " ".join('"'+a+'"' for a in arguments)
                                + "\nexit /b %errorlevel%\n", encoding="utf-8")
            arguments = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)]
        subprocess.run(arguments, cwd=ROOT, check=True)
    logs = [path for path in (ROOT / "build/rf_build.log", ROOT / "build/rf_release.log", ROOT / "build/rf_export.log") if path.exists()]
    log = max(logs, key=lambda path: path.stat().st_mtime).read_text(encoding="utf-8", errors="replace")
    if "RF_BUILD_PASS=" not in log:
        raise RuntimeError("Build has not passed its routed timing/DRC release gate")
    # Keep XSA exports out of PYNQ's bit/HWH loader directory. Older releases
    # placed them together, causing PYNQ 3.x to choose the wrong frontend.
    hardware_dir = out / "hardware_description"
    hardware_dir.mkdir(exist_ok=True)
    legacy_xsa = out / "ook_rf.xsa"
    xsa = hardware_dir / "ook_rf.xsa"
    if legacy_xsa.exists():
        destination = xsa if not xsa.exists() else ROOT / "build" / f"ook_rf_legacy_{time.time_ns()}.xsa"
        shutil.move(str(legacy_xsa), str(destination))
    for suffix in ("bit", "hwh"):
        if not (out / f"ook_rf.{suffix}").is_file():
            raise FileNotFoundError(out / f"ook_rf.{suffix}")
    if not xsa.is_file():
        raise FileNotFoundError(xsa)
    configuration = audit_hwh(out / "ook_rf.hwh")
    (out / "configuration.json").write_text(json.dumps(configuration, indent=2)+"\n", encoding="utf-8")
    shutil.copy2(ROOT / "assets/image.raw", out / "image.raw")
    shutil.copy2(ROOT / "assets/source.png", out / "source.png")
    shutil.copy2(ROOT / "board/run_on_board.py", out / "run_on_board.py")
    shutil.copy2(ROOT / "board/run_on_board.ipynb", out / "run_on_board.ipynb")
    shutil.copy2(ROOT / "docs/rfsoc4x2_rf_bringup.md", out / "README.md")
    evidence = out / "verification"
    evidence.mkdir(exist_ok=True)
    for filename in ("timing_summary_routed.rpt", "drc.rpt", "route_status.rpt", "cdc.rpt",
                     "methodology.rpt", "cdc_waivers.xdc", "bus_skew.rpt"):
        shutil.copy2(ROOT / "reports/implementation_rf" / filename, evidence / filename)
    for filename in ("result.json", "xsim_result.json", "host_result.json"):
        path = ROOT / "reports/rf_simulation" / filename
        if path.exists():
            shutil.copy2(path, evidence / filename)
    loader_result = ROOT / "reports/pynq_loader/result.json"
    packaged_loader_result = evidence / "pynq_loader_result.json"
    if loader_result.exists():
        loader_report = json.loads(loader_result.read_text(encoding="utf-8"))
        if (loader_report.get("passed")
                and loader_report.get("bitstream_sha256") == hashlib.sha256((out / "ook_rf.bit").read_bytes()).hexdigest()
                and loader_report.get("application_sha256") == hashlib.sha256((out / "run_on_board.py").read_bytes()).hexdigest()):
            shutil.copy2(loader_result, packaged_loader_result)
        else:
            packaged_loader_result.unlink(missing_ok=True)
    timing_report = (evidence / "timing_summary_routed.rpt").read_text(encoding="utf-8")
    timing = None
    for line in timing_report.splitlines():
        fields = line.split()
        if len(fields) == 12 and all(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", field) for field in fields):
            timing = dict(wns_ns=float(fields[0]), tns_ns=float(fields[1]), setup_failing_endpoints=int(fields[2]),
                          whs_ns=float(fields[4]), ths_ns=float(fields[5]), hold_failing_endpoints=int(fields[6]),
                          wpws_ns=float(fields[8]), pulse_width_failing_endpoints=int(fields[10]))
            break
    if timing is None:
        raise RuntimeError("Cannot extract routed timing summary")
    if any(timing[key] < 0 for key in ("wns_ns", "whs_ns", "wpws_ns")) or any(
            timing[key] for key in ("setup_failing_endpoints", "hold_failing_endpoints", "pulse_width_failing_endpoints")):
        raise RuntimeError("Package refused: routed timing failed")
    if not re.search(r"Checks found:\s+0\b", (evidence / "drc.rpt").read_text(encoding="utf-8")):
        raise RuntimeError("Package refused: DRC is not clean")
    if re.search(r"Critical Warning|[ \t]Error[ \t]", (evidence / "methodology.rpt").read_text(encoding="utf-8")):
        raise RuntimeError("Package refused: critical methodology remains")
    if re.search(r"CDC-\d+\s+Critical\s+[1-9]", (evidence / "cdc.rpt").read_text(encoding="utf-8")):
        raise RuntimeError("Package refused: critical CDC remains")
    bus_skew = (evidence / "bus_skew.rpt").read_text(encoding="utf-8")
    if "Slack (VIOLATED)" in bus_skew or "Slack (MET)" not in bus_skew:
        raise RuntimeError("Package refused: bus skew failed")
    manifest = dict(board="RFSoC4x2", part="xczu48dr-ffvg1517-2-e", vivado="2024.2",
                    host_interface=configuration["host_interface"],
                    physical_board_tested=False, routed_timing_and_drc_passed=True,
                    routed_timing=timing,
                    source_files={str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in [*sorted((ROOT / "board/rtl").glob("*.v")),
                                               *[ROOT / "rtl" / name for name in ("image_sender.v", "image_receiver.v", "packet_rx.v", "crc32_functions.vh")],
                                               ROOT / "scripts/build_rf.tcl", ROOT / "scripts/rf_clock_constraints.tcl", ROOT / "scripts/rf_cdc_waivers.tcl",
                                               ROOT / "scripts/verify_rf_timing.tcl"]},
                    files={path.relative_to(out).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in sorted(out.rglob("*")) if path.is_file() and path.suffix in (".bit", ".hwh", ".xsa", ".raw", ".py", ".ipynb")})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    archive = shutil.make_archive(str(ROOT / "board/ook_rf_rfsoc4x2"), "zip", out)
    print(f"RF release package: {archive}")


if __name__ == "__main__":
    main()
