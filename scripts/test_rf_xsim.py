"""Run the RF board RTL with Vivado's compiled simulator and modeled cable/BRAM."""
import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from build_rf import find_vivado
from test_rf import SOURCES, ROOT


def invoke(executable, arguments, cwd, log):
    args = [str(executable), *map(str, arguments)]
    if os.name == "nt":
        if any(any(c in arg for c in '\r\n"%!&|<>^') for arg in args):
            raise ValueError("Unsupported shell characters")
        launcher = cwd / (log.stem + ".cmd")
        launcher.write_text("@echo off\ncall " + " ".join('"'+arg.replace('\\', '/')+'"' for arg in args)
                            + "\nexit /b %errorlevel%\n", encoding="utf-8")
        args = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)]
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(args, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, timeout=7200)
    text = log.read_text(encoding="utf-8", errors="replace")
    if result.returncode or any(line.startswith("ERROR:") for line in text.splitlines()):
        raise RuntimeError(f"Tool failed: {log}\n" + "\n".join(text.splitlines()[-20:]))
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vivado-home", type=Path)
    parser.add_argument("--packets", type=int, default=300)
    parser.add_argument("--delay", type=int, default=113)
    parser.add_argument("--drop-ack", type=int, default=1, choices=(0, 1))
    parser.add_argument("--host-only", action="store_true", help="Simulate command crossing and RF clock loss")
    args = parser.parse_args()
    bindir = find_vivado(args.vivado_home).parent
    ext = ".bat" if os.name == "nt" else ""
    work = ROOT / "build/rf_xsim"
    out = ROOT / "reports/rf_simulation"
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    if args.host_only:
        xpm = bindir.parent / "data/ip/xpm/xpm_cdc/hdl/xpm_cdc.sv"
        glbl = bindir.parent / "data/verilog/src/glbl.v"
        host_rtl = ROOT / "board/rtl/ook_rf_host.v"
        invoke(bindir / ("xvlog" + ext), ["-sv", xpm, glbl, host_rtl, ROOT / "sim/tb_rf_host.sv"],
               work, out / "host_compile.log")
        invoke(bindir / ("xelab" + ext), ["tb_rf_host", "glbl", "-s", "rf_host"], work, out / "host_elaborate.log")
        text = invoke(bindir / ("xsim" + ext), ["rf_host", "-runall", "-onfinish", "quit", "-onerror", "quit",
                      "-log", out / "xsim_rf_host.log"], work, out / "host_simulation.log")
        if "RF_HOST_SIM_PASS" not in text:
            raise RuntimeError("RF host clock crossing did not reach PASS")
        print(next(line for line in text.splitlines() if "RF_HOST_SIM_PASS" in line))
        result = dict(simulator="Vivado XSim 2024.2 with XPM CDC models", passed=True,
                      checked=["clock absent at boot", "coherent status", "command acknowledgement",
                               "RF clock loss and recovery"], physical_board_tested=False,
                      host_rtl_sha256=hashlib.sha256(host_rtl.read_bytes()).hexdigest())
        (out / "host_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return
    # The testbench's readmemh path is relative to its runtime working directory.
    (work / "assets").mkdir(exist_ok=True)
    import shutil
    shutil.copy2(ROOT / "assets/image.mem", work / "assets/image.mem")
    invoke(bindir / ("xvlog" + ext), ["-sv", "-d", "IVERILOG", "-i", ROOT / "rtl",
           *[ROOT / path for path in SOURCES]], work, out / "xsim_compile.log")
    invoke(bindir / ("xelab" + ext), ["tb_rf_board", "-s", "rf_board", "-mt", "4",
           "-generic_top", f"PACKETS={args.packets}", "-generic_top", f"CHANNEL_DELAY={args.delay}",
           "-generic_top", f"DROP_ACK={args.drop_ack}"], work, out / "xsim_elaborate.log")
    start = time.monotonic()
    print(f"RUN XSim RF: {args.packets} packets, cable delay={args.delay}, dropped ACK={args.drop_ack}", flush=True)
    text = invoke(bindir / ("xsim" + ext), ["rf_board", "-runall", "-onfinish", "quit", "-onerror", "quit",
                  "-log", out / "xsim_rf.log"], work, out / "xsim_full_image.log")
    if "RF_SIM_PASS" not in text:
        raise RuntimeError("RF simulation did not reach PASS")
    print("\n".join(line for line in text.splitlines() if "RF_SIM_PASS" in line), flush=True)
    result = dict(simulator="Vivado XSim 2024.2; synchronous BRAM and digital IQ channel models",
                  physical_board_tested=False, packets=args.packets, channel_delay_clocks=args.delay,
                  dropped_acks=args.drop_ack, passed=True, mismatched_bytes=0,
                  wall_seconds=round(time.monotonic()-start, 3),
                  source_sha256=hashlib.sha256((ROOT / "assets/image.raw").read_bytes()).hexdigest())
    (out / "xsim_result.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
