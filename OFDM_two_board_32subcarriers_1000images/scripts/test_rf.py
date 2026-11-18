"""Run the board RTL and host CDC with Vivado tooling in Vivado XSim."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
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


