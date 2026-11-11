"""Run the board RTL through two modeled RFDC I/Q cables with independent clocks in Vivado XSim."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "assets/image.raw"
BENCHES = [ROOT / "sim" / name for name in ("tb_rf_board.sv", "rf_iq_channel.sv", "tb_rf_host.sv")]
HDL_SUFFIXES = {".v", ".vh", ".sv", ".svh"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def source_paths():
    hdl = sorted(
        path
        for directory in (ROOT / "rtl", ROOT / "board/rtl")
        for path in directory.rglob("*")
        if path.is_file() and path.suffix in HDL_SUFFIXES
    )
    return hdl + BENCHES + [IMAGE, Path(__file__).resolve()]


def fingerprint(paths):
    return {path.relative_to(ROOT).as_posix(): sha256(path.read_bytes()) for path in paths}


def vivado_bin(requested):
    candidates = [requested, os.environ.get("XILINX_VIVADO"), os.environ.get("VIVADO_HOME")]
    on_path = shutil.which("xsim")
    if on_path:
        candidates.append(Path(on_path).parent.parent)
    candidates.extend([Path("D:/Vivado/2024.2"), Path("C:/Xilinx/Vivado/2024.2")])
    suffix = ".bat" if os.name == "nt" else ""
    for home in filter(None, candidates):
        bin_dir = Path(home).resolve() / "bin"
        if all((bin_dir / (name + suffix)).is_file() for name in ("xvlog", "xelab", "xsim")):
            return bin_dir
    raise FileNotFoundError("Vivado XSim was not found. Pass --vivado-home.")


def run_tool(bin_dir, name, arguments, work, log):
    suffix = ".bat" if os.name == "nt" else ""
    command = [str(bin_dir / (name + suffix)), *map(str, arguments)]
    if os.name == "nt":
        if any(any(char in arg for char in '\r\n"%!&|<>^') for arg in command):
            raise ValueError("Unsupported character in the Vivado path or arguments")
        launcher = work / (log.stem + ".cmd")
        command_line = " ".join('"' + arg.replace("\\", "/") + '"' for arg in command)
        launcher.write_text("@echo off\ncall " + command_line + "\nexit /b %errorlevel%\n", encoding="utf-8")
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", launcher.name]
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(command, cwd=work, stdout=stream,
                                   stderr=subprocess.STDOUT, timeout=7200, check=False)
    output = log.read_text(encoding="utf-8", errors="replace")
    if completed.returncode or re.search(r"^(ERROR|Fatal):", output, flags=re.MULTILINE):
        raise RuntimeError(f"{name} failed in {log}\n" + "\n".join(output.splitlines()[-20:]))
    return output


def simulate(bin_dir, snapshot, work, log, plusargs=()):
    flags = [snapshot, "-runall", "-onfinish", "quit", "-onerror", "quit", "-nolog"]
    for item in plusargs:
        flags.extend(("-testplusarg", item))
    return run_tool(bin_dir, "xsim", flags, work, log)


def read_pass(output, marker, log):
    lines = [line for line in output.splitlines() if line.startswith(marker)]
    if len(lines) != 1:
        raise RuntimeError(f"Missing or repeated {marker} in {log}")
    print(lines[0], flush=True)
    return lines[0]


def test_board(bin_dir, work, reports, source, name, configuration, **faults):
    snapshot, packets, channel, clock_skew_ppm, cfo_hz = configuration
    log = reports / f"{name}.log"
    dump = work / f"{name}.raw"
    dump.unlink(missing_ok=True)
    args = [f"OUTPUT={dump.name}"]
    args.extend(f"{key.upper()}={value}" for key, value in faults.items())

    print(f"RUN {name}, {packets} packets", flush=True)
    started = time.monotonic()
    output = simulate(bin_dir, snapshot, work, log, args)
    passed = read_pass(output, "RF_SIM_PASS ", log)
    metrics = {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", passed)}
    result = dict(
        passed=True,
        log=log.relative_to(ROOT).as_posix(),
        wall_seconds=round(time.monotonic() - started, 3),
        channel=dict(data_delay_clocks=113 if channel else 37,
                     ack_delay_clocks=51, data_cfo_hz=cfo_hz, ack_cfo_hz=-cfo_hz,
                     receiver_clock_skew_ppm=clock_skew_ppm,
                     additional_iq_sample_delay=channel,
                     complex_gain_numerator=[3, -7] if channel else [-5, 3],
                     gain_denominator=16,
                     echo_numerator=channel, echo_delay_clocks=8,
                     noise_range_adc_counts=[-3, 4]),
        **faults,
        **{key: value for key, value in metrics.items() if key not in faults},
    )
    original = source[:packets * 1024]
    expected = bytearray(original)
    packet_offset = faults.get("fault_packet", 1) * 1024
    fault = faults.get("data_fault")
    if faults.get("disconnected"):
        expected[:] = bytes(len(expected))
    elif fault in (3, 4):
        expected[packet_offset:packet_offset + 1024] = bytes(1024)
    elif fault == 1:
        expected[packet_offset + 86] ^= 0xff
    received = dump.read_bytes()
    mismatches = sum(actual != initial for actual, initial in zip(received, original))
    if (received != expected or metrics.get("packets") != packets
            or metrics.get("bytes") != len(received) or metrics.get("mismatched_bytes") != mismatches):
        raise RuntimeError(f"BRAM image differs from the expected payload and fallback data: {dump}")
    received_file = reports / dump.name
    shutil.copyfile(dump, received_file)
    result.update(byte_for_byte=received == original, matched_expected_image=True,
                  sha256=sha256(received), received_file=received_file.relative_to(ROOT).as_posix())
    return result


def test_host(bin_dir, work, reports):
    xpm = bin_dir.parent / "data/ip/xpm/xpm_cdc/hdl/xpm_cdc.sv"
    glbl = bin_dir.parent / "data/verilog/src/glbl.v"
    run_tool(bin_dir, "xvlog", ["-sv", xpm, glbl, ROOT / "board/rtl/ofdm_rf_host.v", BENCHES[2]],
             work, reports / "host_compile.log")
    run_tool(bin_dir, "xelab", ["tb_rf_host", "glbl", "-s", "rf_host", "-mt", "4"],
             work, reports / "host_elaborate.log")
    log = reports / "host.log"
    print("RUN host clock crossing and recovery", flush=True)
    output = simulate(bin_dir, "rf_host", work, log)
    read_pass(output, "RF_HOST_SIM_PASS:", log)
    return dict(passed=True, log=log.relative_to(ROOT).as_posix(),
                checked=["cold boot without RF clock", "coherent status", "command echo",
                         "stopped RF clock", "reset and recovery"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vivado-home", type=Path)
    parser.add_argument("--full", action="store_true", help="Also verify the entire 300-packet image")
    options = parser.parse_args()
    profile = "full" if options.full else "quick"
    bin_dir = vivado_bin(options.vivado_home)
    work = ROOT / "build/rf_simulation" / profile
    out = ROOT / "reports/rf_simulation"
    reports = out / profile
    result_path = out / ("result.json" if options.full else "quick_result.json")
    reports.mkdir(parents=True, exist_ok=True)
    (work / "assets").mkdir(parents=True, exist_ok=True)
    result_path.unlink(missing_ok=True)

    paths = source_paths()
    hashes = fingerprint(paths)
    source = IMAGE.read_bytes()
    if len(source) != 307200:
        raise ValueError(f"{IMAGE} must be a 640×480 grayscale image")
    (work / "assets/image.mem").write_text("".join(f"{pixel:02x}\n" for pixel in source),
                                           encoding="ascii")
    rtl = [path for path in paths if path.suffix in (".v", ".sv") and path not in BENCHES]
    run_tool(bin_dir, "xvlog", ["-sv", "-i", ROOT / "rtl", *rtl, *BENCHES[:2]],
             work, reports / "compile.log")
    baseline = ("rf_baseline", 3, 0, 0, 0)
    echo = ("rf_echo", 3, 1, 0, 0)
    clock_reverse = ("rf_clock_reverse", 3, 1, -20, -10000)
    clock_forward = ("rf_clock_forward", 3, 1, 20, 10000)
    full = ("rf_full", 300, 1, 20, 10000)
    cases = [
        ("baseline", baseline, {}),
        ("ack_drop", baseline, {"drop_ack": 1}),
        ("ack_corrupt", baseline, {"corrupt_ack": 1}),
        ("disconnected", baseline, {"disconnected": 1}),
        ("bad_payload_exhausted", baseline, {"data_fault": 1}),
        ("bad_payload_repaired", baseline, {"data_fault": 2}),
        ("missing_payload", baseline, {"data_fault": 3}),
        ("missing_final_payload", baseline, {"data_fault": 3, "fault_packet": 2}),
        ("bad_header", baseline, {"data_fault": 4}),
        ("all_acks_lost_for_packet", baseline, {"block_ack_packet": 1}),
        ("echo", echo, {}),
        ("clock_reverse", clock_reverse, {}),
        ("clock_forward", clock_forward, {}),
    ]
    if options.full:
        cases.append(("full_image", full, {"drop_ack": 300}))
    for snapshot, packets, channel, clock_skew_ppm, cfo_hz in dict.fromkeys(
            configuration for _, configuration, _ in cases):
        run_tool(bin_dir, "xelab", ["tb_rf_board", "-s", snapshot, "-mt", "4", "-O3",
                                   "-generic_top", f"PACKETS={packets}",
                                   "-generic_top", f"CHANNEL_PROFILE={channel}",
                                   "-generic_top", f"CLOCK_SKEW_PPM={clock_skew_ppm}",
                                   "-generic_top", f"CFO_HZ={cfo_hz}"],
                 work, reports / f"elaborate_{snapshot}.log")

    tests = {
        name: test_board(bin_dir, work, reports, source, name, configuration, **faults)
        for name, configuration, faults in cases
    }
    tests["host"] = test_host(bin_dir, work, reports)

    if hashes != fingerprint(source_paths()) or hashes["assets/image.raw"] != sha256(source):
        raise RuntimeError("Sources changed during the simulation. Run it again.")
    baseline_log = (reports / "baseline.log").read_text(encoding="utf-8", errors="replace")
    version = re.search(r"\bxsim v([\d.]+)", baseline_log)
    result = dict(passed=True, profile=profile, physical_board_tested=False,
                  topology="two_rf_endpoints_two_cables",
                  simulator=f"Vivado XSim {version.group(1)}" if version else "Vivado XSim",
                  source_files=hashes, tests=tests)
    temporary = result_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temporary.replace(result_path)
    print(f"PASS {profile} OFDM RF simulation, {result_path}", flush=True)


if __name__ == "__main__":
    main()
