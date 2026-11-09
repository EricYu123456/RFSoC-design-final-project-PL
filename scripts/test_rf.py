"""Verify the RF board RTL with delayed, noisy IQ and real image bytes."""
import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ["sim/tb_rf_board.sv", "board/rtl/ook_rf_board.v", "board/rtl/rf_image_link.v",
           "board/rtl/rf_ook_tx.v", "board/rtl/rf_ook_rx.v", "rtl/image_sender.v",
           "rtl/image_receiver.v", "rtl/packet_rx.v"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Also verify all 307,200 pixels")
    parser.add_argument("--full-only", action="store_true")
    args = parser.parse_args()
    compiler, runtime = shutil.which("iverilog"), shutil.which("vvp")
    if not compiler or not runtime:
        parser.error("Icarus Verilog (iverilog and vvp) is required")
    out = ROOT / "reports/rf_simulation"
    out.mkdir(parents=True, exist_ok=True)
    cases = [] if args.full_only else [("baseline", 3, 37, 0, 0), ("ack_drop", 3, 113, 1, 0),
                                      ("disconnected", 1, 19, 0, 1)]
    if args.full or args.full_only:
        cases.append(("full_image", 300, 113, 1, 0))
    results = []
    for name, packets, delay, drop, disconnected in cases:
        snapshot = ROOT / "build" / f"rf_{name}.vvp"
        subprocess.run([compiler, "-g2012", "-DIVERILOG", "-I", str(ROOT / "rtl"),
                        "-s", "tb_rf_board", f"-Ptb_rf_board.PACKETS={packets}",
                        f"-Ptb_rf_board.CHANNEL_DELAY={delay}", f"-Ptb_rf_board.DROP_ACK={drop}",
                        f"-Ptb_rf_board.DISCONNECT={disconnected}", "-o", str(snapshot),
                        *[str(ROOT / source) for source in SOURCES]], cwd=ROOT, check=True)
        print(f"RUN RF {name}: packets={packets}, cable delay={delay} clocks", flush=True)
        started = time.monotonic()
        with (out / f"{name}.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run([runtime, str(snapshot)], cwd=ROOT, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=7200)
        log_text = (out / f"{name}.log").read_text(encoding="utf-8")
        print(log_text.strip(), flush=True)
        if completed.returncode or "RF_SIM_PASS" not in log_text:
            raise RuntimeError(f"RF simulation failed: {out / (name + '.log')}")
        results.append(dict(case=name, passed=True, packets=packets, delay_clocks=delay,
                            dropped_acks=drop, disconnected=bool(disconnected),
                            wall_seconds=round(time.monotonic()-started, 3)))
        result_path = out / ("full_result.json" if args.full_only else "result.json")
        result_path.write_text(json.dumps(dict(
            simulator="Icarus Verilog; synchronous BRAM and digital IQ channel models",
            physical_board_tested=False,
            source_sha256=hashlib.sha256((ROOT / "assets/image.raw").read_bytes()).hexdigest(),
            results=results), indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
