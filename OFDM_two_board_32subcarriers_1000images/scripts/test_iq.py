"""Waveform and asynchronous host simulation using Vivado XSim 2024.2."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import hashlib
from PIL import Image
from test_rf import ROOT, IMAGE, BENCHES, vivado_bin, run_tool, simulate, test_host, read_pass, fingerprint, source_paths

def main():
    out = ROOT / "reports" / "iq"
    work = ROOT / "build" / "iq"
    for path in (work, out):
        path.mkdir(parents=True, exist_ok=True)
    hashes = fingerprint(source_paths() + [Path(__file__).resolve()])
    (work / "image.mem").write_text("".join(f"{b:02x}\n" for b in IMAGE.read_bytes()), encoding="ascii")
    input_hashes=[]
    for idx,name in enumerate(("image.mem","image2.mem")):
        with Image.open(ROOT.parent/"data"/f"coco_gray_{idx:04}.png") as im:
            input_hashes.append(hashlib.sha256(im.tobytes()).hexdigest())
            (work/name).write_text("".join(f"{b:02x}\n" for b in im.tobytes()),encoding="ascii")
    bin_dir = vivado_bin(None)
    rtl = sorted((ROOT / "rtl").glob("*.v")) + sorted((ROOT / "board/rtl").glob("*.v"))
    run_tool(bin_dir, "xvlog", ["-sv","-i",ROOT/"rtl",*rtl,*BENCHES[:2]],work,out/"compile.log")
    cases = [("clean",12,0,0,0,8,1),("fading",12,20,10000,1,8,1),("reverse",12,-20,-10000,1,8,1),("qpsk_ack",12,20,10000,1,8,0),("window1",12,0,0,0,1,1),("window4",12,0,0,0,4,1),("full_frames",300,20,10000,1,8,1)]
    for name,n,ppm,cfo,loss,w,bpsk in cases:
        run_tool(bin_dir,"xelab",["tb_rf_board","-s",name,"-O3","-generic_top",f"PACKETS={n}",
            "-generic_top",f"CFO_HZ={cfo}","-generic_top",f"CLOCK_SKEW_PPM={ppm}",
            "-generic_top","CHANNEL_PROFILE=1","-generic_top",f"WINDOW={w}","-generic_top",f"ACK_BPSK={bpsk}"],work,out/f"{name}_elab.log")
    def test(case):
        name,n,ppm,cfo,loss,w,bpsk=case
        log=out/f"{name}.log"
        result=simulate(bin_dir,name,work,log,["LOSS"] if loss else [])
        return name,read_pass(result,"AIR_PASS ",log)
    with ThreadPoolExecutor(max_workers=3) as pool:
        tests=dict(pool.map(test,reversed(cases)))
    tests["host"]=test_host(bin_dir,work,out)
    if hashes!=fingerprint(source_paths()+[Path(__file__).resolve()]):
        raise RuntimeError("Source changed during waveform test")
    (out/"result.json").write_text(json.dumps(dict(passed=True,input_sha256=input_hashes,tests=tests,source_files=hashes),indent=2))
if __name__=="__main__":
    main()

