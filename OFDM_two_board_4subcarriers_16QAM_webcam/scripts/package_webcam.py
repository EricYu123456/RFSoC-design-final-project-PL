"""Package the routed bit/HWH and notebook used on both boards."""
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "build/hardware/artifacts"
TIMING = ROOT / "reports/implementation_qam16/timing_summary_routed.rpt"
NOTEBOOK = "RFSoC4x2_OFDM_Live_Webcam_MethodB.ipynb"
HARDWARE = ["ofdm_qam16.bit", "ofdm_qam16.hwh"]
SOFTWARE = [NOTEBOOK, "webcam_board.py", "webcam_live.py", "webcam_stream.py"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if "All user specified timing constraints are met." not in TIMING.read_text(encoding="utf-8"):
        raise RuntimeError("Routed timing did not pass")
    # Both outputs are written after the timing/DRC/CDC gate in route_rf.tcl.
    hwh = ET.parse(ARTIFACTS / HARDWARE[1])
    regs = {r.get("INSTANCE"): (int(r.get("BASEVALUE"), 0), int(r.get("HIGHVALUE"), 0))
            for r in hwh.iter("MEMRANGE")}
    for name, start, size in (("source_ctrl", 0xa0000000, 524288), ("sink_ctrl", 0xa0080000, 524288),
                              ("control", 0xa0100000, 65536), ("telemetry", 0xa0110000, 65536),
                              ("host_state", 0xa0120000, 65536), ("rfdc", 0xa0140000, 262144)):
        if regs.get(name) != (start, start + size - 1):
            raise ValueError(f"Unexpected HWH memory map for {name}")
    modules = {m.get("INSTANCE"): {p.get("NAME"): p.get("VALUE")
                                   for p in m.findall("PARAMETERS/PARAMETER")}
               for m in hwh.iter("MODULE")}
    modem, rfdc = modules["modem"], modules["rfdc"]
    if modem.get("FRAME_BYTES") != "307200" or modem.get("WINDOW") != "8" or "ACK_BPSK" in modem:
        raise ValueError("Unexpected modem block in HWH")
    if (rfdc.get("C_ADC_NCO_Freq22"), rfdc.get("C_DAC_NCO_Freq20")) != ("-0.5", "0.5"):
        raise ValueError("Unexpected ADC/DAC mixer configuration")
    for name in HARDWARE:
        shutil.copyfile(ARTIFACTS / name, ROOT / name)
    manifest = dict(board="RFSoC4x2", part="xczu48dr-ffvg1517-2-e", vivado="2024.2",
                    phy="ofdm", host_interface="ofdm_qam16_v1", modem_status_id=0x42,
                    fft_size=8, active_bins=[1, 2, -2, -1], cyclic_prefix_samples=4,
                    data_modulation="16-QAM", ack_modulation="16-QAM", carrier_mhz=500,
                    baseband_msps=19.2, frame_bytes=307200, physical_board_tested=False,
                    files={name: digest(ROOT / name) for name in HARDWARE + SOFTWARE})
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    output = ROOT / "RFSoC4x2_OFDM_4SC_16QAM_Webcam.zip"
    temporary = output.with_suffix(".tmp")
    with ZipFile(temporary, "w", ZIP_DEFLATED) as arch:
        for name in [*HARDWARE, *SOFTWARE, "manifest.json"]:
            arch.write(ROOT / name, name)
    with ZipFile(temporary) as arch:
        for name, expected in manifest["files"].items():
            if hashlib.sha256(arch.read(name)).hexdigest() != expected:
                raise RuntimeError(f"Package mismatch: {name}")
    temporary.replace(output)
    print(output)


if __name__ == "__main__":
    main()
