"""Build Vivado IP and run reproducible OOK / CRC / ARQ simulations on Windows."""
import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from analyze_results import analyze, write_summary
from prepare_image import prepare

ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = ROOT / "build/vivado/ook_image_link.sim/sim_1/behav/xsim"
CASES = ("baseline", "data_error", "ack_corrupt", "ack_drop", "last_ack_drop", "wrong_ack",
         "persistent_error", "backpressure", "mixed")


def command(arguments, cwd, log, environment):
    """Use an explicit batch file so CMD preserves quoted NAME=value plusargs."""
    # XSim embeds paths in Tcl; backslashes would turn \n and \r into escapes.
    arguments = [str(argument).replace("\\", "/") for argument in arguments]
    log.parent.mkdir(parents=True, exist_ok=True)
    if str(arguments[0]).lower().endswith(".bat"):
        task_batch = log.parent / (log.stem + "_launch.cmd")
        for argument in arguments:
            if any(char in str(argument) for char in '\r\n"%!&|<>^'):
                raise ValueError(f"Unsupported shell character in tool argument: {argument}")
        invocation = "call " + " ".join('"' + str(argument) + '"' for argument in arguments)
        task_batch.write_text("@echo off\nchcp 65001 >nul\n" + invocation + "\nexit /b %errorlevel%\n", encoding="utf-8")
        arguments = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(task_batch)]
    with log.open("w", encoding="utf-8") as stream:
        tool_error = False
        process = subprocess.Popen(arguments, cwd=cwd, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in process.stdout:
            stream.write(line)
            stream.flush()
            tool_error = tool_error or line.lstrip().startswith("ERROR:")
            if any(word in line for word in ("ERROR", "Fatal", "TEST_", "PROGRESS", "PROJECT_READY", "Built simulation")):
                print(line.rstrip(), flush=True)
        code = process.wait()
    if code or tool_error:
        tail = "\n".join(log.read_text(encoding="utf-8").splitlines()[-35:])
        raise RuntimeError(f"Tool failed ({code}); see {log}\n{tail}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vivado-home", type=Path, default=Path(r"C:\Xilinx\Vivado\2024.2"))
    parser.add_argument("--input", type=Path, help="Optional image to convert to 640x480 grayscale")
    parser.add_argument("--case", choices=CASES, default="baseline")
    parser.add_argument("--packets", type=int, default=3)
    parser.add_argument("--suite", choices=("smoke", "full"))
    parser.add_argument("--rebuild", action="store_true", help="Regenerate project and IP output products")
    parser.add_argument("--skip-build", action="store_true", help="Reuse compiled snapshot; only for unchanged RTL")
    parser.add_argument("--waves", action="store_true", help="Record selected signals in an XSim WDB file")
    parser.add_argument("--synth", action="store_true", help="Also synthesize and report device utilization")
    args = parser.parse_args()
    if not 1 <= args.packets <= 300:
        parser.error("--packets must be 1..300")
    vivado = args.vivado_home / "bin" / "vivado.bat"
    if not vivado.exists():
        parser.error(f"Vivado was not found: {vivado}")
    (ROOT / "build").mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment["PATH"] = str(args.vivado_home / "bin") + os.pathsep + environment["PATH"]
    environment["OOK_WAVES"] = "1" if args.waves else "0"
    if args.input or not (ROOT / "assets/image.mem").exists():
        prepare(args.input)
    if args.rebuild or not (SIM_DIR / "compile.bat").exists():
        command([vivado, "-mode", "batch", "-source", ROOT / "scripts/create_project.tcl", "-notrace",
                 "-log", ROOT / "build/create_project.log", "-journal", ROOT / "build/create_project.jou"],
                ROOT, ROOT / "build/create_project_console.log", environment)
    if not args.skip_build:
        command([SIM_DIR / "compile.bat"], SIM_DIR, ROOT / "build/compile_console.log", environment)
        command([SIM_DIR / "elaborate.bat"], SIM_DIR, ROOT / "build/elaborate_console.log", environment)
    tests = [(args.case, args.packets)]
    if args.suite:
        tests = [(name, 4 if name == "mixed" else (1 if name == "persistent_error" else 3)) for name in CASES]
        if args.suite == "full":
            tests += [("baseline", 300), ("mixed", 300)]
    for case, packets in tests:
        output = ROOT / "reports" / f"{case}_{packets}"
        output.mkdir(parents=True, exist_ok=True)
        (output / "result.json").unlink(missing_ok=True)
        print(f"RUN {case} packets={packets}", flush=True)
        # Keep simulator journals and generated startup scripts private to this run.
        # This also permits independent scenarios to run concurrently.
        run_dir = ROOT / "build/runs" / output.name
        snapshot = run_dir / "xsim.dir/tb_image_link_behav"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SIM_DIR / "xsim.dir/tb_image_link_behav", snapshot, dirs_exist_ok=True)
        shutil.copy2(SIM_DIR / "xsim.ini", run_dir / "xsim.ini")
        begin = time.monotonic()
        command([args.vivado_home / "bin/xsim.bat", "tb_image_link_behav",
                 "-tclbatch", ROOT / "scripts/run_xsim.tcl",
                 "-testplusarg", "ROOT=" + ROOT.as_posix(),
                 "-testplusarg", "OUT=" + output.as_posix(),
                 "-testplusarg", "CASE=" + case, "-testplusarg", "PACKETS=" + str(packets),
                 "-wdb", output / "waves.wdb", "-log", output / "xsim.log", "-onerror", "quit", "-onfinish", "quit"],
                run_dir, output / "console.log", environment)
        result_path = output / "result.json"
        if not result_path.exists():
            raise RuntimeError(f"Simulation did not produce PASS; see {output / 'console.log'}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not result.get("test_passed"):
            raise RuntimeError(f"Simulation failed: {case}")
        result["wall_time_seconds"] = round(time.monotonic() - begin, 3)
        result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        result = analyze(output)
        write_summary()
        print(f"PASS {case}: retries={result['retransmissions']}, simulation={result['transfer_ms']:.5f} ms, "
              f"wall={result['wall_time_seconds']:.1f} s, goodput={result['goodput_mbps']:.5f} Mbit/s", flush=True)
    if args.synth:
        command([vivado, "-mode", "batch", "-source", ROOT / "scripts/synthesize.tcl", "-notrace",
                 "-log", ROOT / "build/synthesis.log", "-journal", ROOT / "build/synthesis.jou"],
                ROOT, ROOT / "build/synthesis_console.log", environment)
    print(f"Results: {ROOT / 'reports/summary.md'}", flush=True)


if __name__ == "__main__":
    main()
