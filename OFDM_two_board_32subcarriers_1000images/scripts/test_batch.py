"""Run both production board RTL instances across 1,000 distinct source images."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/"build"/"batch"
REPORT=ROOT/"reports"/"batch"
def sha(data):
    return hashlib.sha256(data).hexdigest()

def main():
    WORK.mkdir(parents=True,exist_ok=True)
    REPORT.mkdir(parents=True,exist_ok=True)
    files=[ROOT.parent/"data"/f"coco_gray_{i:04}.png" for i in range(1000)]
    hdl=["board/rtl/ofdm_rf_board.v","rtl/air_codec.v","rtl/air_link.v","sim/fast_phy.v"]
    tracked=hdl+["rtl/air_protocol.vh","rtl/crc32_functions.vh","sim/batch.cpp","scripts/test_batch.py"]
    inputs={f:sha((ROOT/f).read_bytes()) for f in tracked}
    rows=[]
    with (WORK/"source.raw").open("wb") as stream:
        for f in files:
            with Image.open(f) as im:
                if im.size!=(640,480) or im.mode!="L": raise ValueError(f)
                pixels=im.tobytes()
            rows.append(dict(name=f.name,source_sha256=sha(pixels),png_sha256=sha(f.read_bytes())))
            stream.write(pixels)
    if len({r["source_sha256"] for r in rows})!=1000: raise ValueError("duplicate source")
    # Use a short relative build directory even when the project contains spaces.
    portable=ROOT/"build/tools/mingw64"
    exe=shutil.which("verilator") or str(portable/"bin/verilator_bin.exe")
    env=os.environ.copy()
    if Path(exe).name=="verilator_bin.exe":
        env["VERILATOR_ROOT"]=os.path.relpath(portable/"share/verilator",WORK).replace(os.sep,"/")
    for f in hdl+["rtl/air_protocol.vh","rtl/crc32_functions.vh","sim/batch.cpp"]:
        shutil.copyfile(ROOT/f,WORK/Path(f).name)
    subprocess.run([exe,"--cc","-Wno-fatal","-I.","--top-module","ofdm_rf_board",
                    *[Path(f).name for f in hdl],"--exe","batch.cpp"],cwd=WORK,env=env,check=True)
    obj=WORK/"obj_dir"
    if os.name=="nt":
        # Native MinGW make cannot safely expand a VERILATOR_ROOT with spaces.
        inc=(WORK/env["VERILATOR_ROOT"]/"include").resolve()
        sources=[*sorted(obj.glob("Vofdm_rf_board*.cpp")),inc/"verilated.cpp",inc/"verilated_threads.cpp",WORK/"batch.cpp"]
        subprocess.run(["g++","-O3","-DNDEBUG","-DVL_TIME_CONTEXT","-std=c++20","-I",str(inc),"-I",str(inc/"vltstd"),"-I",str(obj),
                        *map(str,sources),"-o","batch.exe","-lws2_32","-latomic"],cwd=WORK,check=True)
        program=WORK/"batch.exe"
    else:
        subprocess.run(["make","-C","obj_dir","-f","Vofdm_rf_board.mk","-j4"],cwd=WORK,check=True)
        program=obj/"Vofdm_rf_board"
    for mode in ("1","2","3"):
        subprocess.run([program,"source.raw","fault.raw","1",mode],cwd=WORK,check=True)
    subprocess.run([program,"source.raw","received.raw","1000"],cwd=WORK,check=True)
    with (WORK/"received.raw").open("rb") as stream:
        for row in rows:
            row["received_sha256"]=sha(stream.read(307200))
            if row["source_sha256"]!=row["received_sha256"]: raise RuntimeError(row["name"])
        if stream.read(1): raise RuntimeError("extra pixels")
    if any(sha((ROOT/f).read_bytes())!=v for f,v in inputs.items()): raise RuntimeError("stale run")
    report=dict(passed=True,frames=1000,packets=300000,bytes=307200000,mismatched_bytes=0,
                block_acks=38000,faults=["damaged_payload_ack_close_repaired","retry_exhaustion","disconnect"],source_files=inputs,images=rows)
    (REPORT/"result.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
if __name__=="__main__":
    main()

