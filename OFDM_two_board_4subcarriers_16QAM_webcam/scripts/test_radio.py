"""Exercise two actual RTL modems with lost DATA and SACK, CFO and clock skew."""
import argparse
import os
import random
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [*sorted((ROOT / "rtl").glob("*.v")),
           *(p for p in sorted((ROOT / "board/rtl").glob("*.v")) if p.name != "ofdm_rf_host.v"),
           ROOT / "sim/rf_iq_channel.sv", ROOT / "sim/tb_rf_board.sv"]


def run(args, cwd, env=None):
    subprocess.run([str(x) for x in args], cwd=cwd, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Use timed Verilator for two full 307200-byte slots")
    parser.add_argument("--verilator-root", type=Path, help="Verilator share/verilator directory")
    args = parser.parse_args()
    work = ROOT / "build" / ("full_iq" if args.full else "short_iq")
    work.mkdir(parents=True, exist_ok=True)
    for seed, name in ((1, "image"), (2, "image2")):
        rng = random.Random(seed)
        (work / (name + ".mem")).write_text("".join(f"{rng.randrange(256):02x}\n" for _ in range(307200)))
    inputs = []
    for source in [*SOURCES, *(ROOT / "rtl").glob("*.vh")]:
        shutil.copyfile(source, work / source.name)
        if source in SOURCES:
            inputs.append(source.name)
    params = {"PACKETS": 300 if args.full else 3, "CFO_HZ": 3500,
              "CLOCK_SKEW_PPM": 4, "CHANNEL_PROFILE": 1}
    top = "tb_rf_board"
    if args.full:
        env = os.environ.copy()
        if args.verilator_root:
            share = args.verilator_root.resolve()
            exe = share.parent.parent / "bin" / ("verilator_bin.exe" if os.name == "nt" else "verilator")
            env["VERILATOR_ROOT"] = os.path.relpath(share, work).replace(os.sep, "/")
        else:
            exe = "verilator"
        options = [f"-G{k}={v}" for k, v in params.items()]
        command = [exe, "--cc", "--timing", "--main", "--top-module", top, "-Wno-fatal"]
        if os.name != "nt":
            command += ["--exe", "--build"]
        run([*command, *options, *inputs], work, env)
        obj = work / "obj_dir"
        if os.name == "nt":
            if not args.verilator_root:
                parser.error("Use --verilator-root on Windows")
            inc = share / "include"
            run(["g++", "-O2", "-std=c++20", "-DVL_TIME_CONTEXT", "-I", inc, "-I", inc / "vltstd",
                 "-I", obj, *sorted(obj.glob("Vtb_rf_board*.cpp")), inc / "verilated.cpp",
                 inc / "verilated_timing.cpp", inc / "verilated_threads.cpp",
                 "-o", "iq.exe", "-lws2_32", "-latomic"], work)
            program = [work / "iq.exe"]
        else:
            program = [obj / ("V" + top)]
    else:
        run(["iverilog", "-g2012", "-I.", "-s", top,
             *(f"-P{top}.{k}={v}" for k, v in params.items()), "-o", "iq.vvp", *inputs], work)
        program = ["vvp", "iq.vvp"]
    result = subprocess.run([*map(str, program), "+LOSS"], cwd=work, capture_output=True, text=True)
    print(result.stdout + result.stderr, end="")
    result.check_returncode()
    if "AIR_PASS" not in result.stdout:
        raise RuntimeError("Radio simulation did not finish")
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / ("full_iq.txt" if args.full else "short_iq.txt")).write_text(result.stdout)


if __name__ == "__main__":
    main()
