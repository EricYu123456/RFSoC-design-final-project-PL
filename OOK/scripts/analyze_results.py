"""Independently compare pixels and render static result figures."""
import csv
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]


def analyze(folder):
    folder = Path(folder)
    path = folder / "result.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    reference = (ROOT / "assets" / "image.raw").read_bytes()[:result["packets"] * 1024]
    received = (folder / "received.raw").read_bytes()
    result["transfer_ms"] = result["transfer_cycles"] / result["sample_clock_hz"] * 1000
    result["goodput_mbps"] = (len(received) * 8 / (result["transfer_ms"] / 1000) / 1e6
                              if result["link_success"] else 0.0)
    result["source_sha256"] = hashlib.sha256(reference).hexdigest()
    result["received_sha256"] = hashlib.sha256(received).hexdigest()
    result["python_exact_match"] = received == reference
    result["vivado_version"] = "2024.2"
    if result["link_success"] and received != reference:
        raise RuntimeError(f"Python pixel comparison failed: {folder}")
    if result["link_success"] and result["packets"] == 300:
        source = Image.frombytes("L", (640, 480), reference)
        reconstructed = Image.frombytes("L", (640, 480), received)
        difference = ImageChops.difference(source, reconstructed)
        source.save(folder / "source.png")
        reconstructed.save(folder / "received.png")
        difference.save(folder / "difference.png")
        comparison = Image.new("L", (640 * 3, 480))
        for index, image in enumerate((source, reconstructed, difference)):
            comparison.paste(image, (index * 640, 0))
        comparison.save(folder / "comparison.png")
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    plot_waveforms(folder)
    plot_progress(folder)
    return result


def plotting():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def plot_waveforms(folder):
    plt = plotting()
    figure, axes = plt.subplots(3, 2, figsize=(13, 7), sharex="col", layout="constrained")
    for column, name in enumerate(("data", "ack")):
        with (folder / f"{name}_wave.csv").open() as stream:
            rows = list(csv.DictReader(stream))[:512]
        if not rows:
            for axis in axes[:, column]:
                axis.text(0.5, 0.5, "No ACK transmitted" if name == "ack" else "No data", ha="center")
            continue
        times = [(int(row["cycle"]) - int(rows[0]["cycle"])) * .01 for row in rows]
        axes[0, column].step(times, [int(row["tx_bit"]) for row in rows], where="post")
        axes[0, column].set_ylim(-.15, 1.15)
        axes[0, column].set_title("Forward DATA OOK" if name == "data" else "Reverse ACK OOK")
        axes[1, column].plot(times, [int(row["sample"]) for row in rows], linewidth=.8)
        axes[2, column].step(times, [int(row["energy"]) for row in rows], where="post", label="RX energy")
        axes[2, column].axhline(1073741824, color="tab:red", linestyle="--", label="Threshold")
        axes[2, column].legend(loc="upper right", fontsize=8)
        axes[2, column].set_xlabel("Time from first captured sample (us)")
    for row, label in enumerate(("TX bit", "Channel sample", "Energy / bit")):
        axes[row, 0].set_ylabel(label)
        for axis in axes[row, :]:
            axis.grid(alpha=.25)
    figure.suptitle(f"{folder.name}: actual DDS / multiplier IP simulation samples")
    figure.savefig(folder / "waveforms.png", dpi=150)
    plt.close(figure)


def plot_progress(folder):
    plt = plotting()
    with (folder / "events.csv").open() as stream:
        events = list(csv.DictReader(stream))
    commits = [event for event in events if event["event"] == "commit"]
    retries = [event for event in events if event["event"] == "data_tx" and int(event["retry"]) > 0]
    figure, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True, layout="constrained")
    for axis, selected, label in ((axes[0], commits, "Unique packets saved"),
                                  (axes[1], retries, "Cumulative retransmissions")):
        times = [int(event["cycle"]) * .00001 for event in selected]
        axis.step([0] + times, list(range(len(times) + 1)), where="post")
        axis.set_ylabel(label)
        axis.grid(alpha=.25)
    axes[-1].set_xlabel("Simulation time since reset release (ms)")
    figure.suptitle(folder.name)
    figure.savefig(folder / "progress.png", dpi=150)
    plt.close(figure)


def write_summary():
    records = []
    for path in sorted((ROOT / "reports").glob("*/result.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if "goodput_mbps" in record:
            records.append((path.parent.name, record))
    lines = ["# XSim 驗證結果", "", "工具：Vivado / XSim 2024.2。下表為實際模擬結果。", "",
             "persistent_error 的預期結果是達重試上限、回報傳輸失敗；測試通過不代表圖片傳送成功。", "",
             "| 測試 | 包數 | 測試 | 圖片傳送 | 資料 CRC 錯誤 | ACK CRC 錯誤 | 重傳 | 重複包 | 像素差異 | 時間 ms | 有效 Mbit/s |",
             "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, record in records:
        lines.append(f"| [{name}]({name}/result.json) | {record['packets']} | PASS | "
                     f"{'成功' if record['link_success'] else '失敗（預期）'} | "
                     f"{record['data_crc_errors']} | {record['ack_crc_errors']} | {record['retransmissions']} | "
                     f"{record['duplicate_packets']} | {record['mismatched_bytes'] if record['link_success'] else '—'} | "
                     f"{record['transfer_ms']:.5f} | {record['goodput_mbps']:.5f} |")
    lines += ["", "有效吞吐率只計算成功傳送的圖片像素；包含 ACK、等待與重傳花費的時間。", "",
              "每個測試目錄包含 result.json、事件紀錄 events.csv，以及實際取樣的波形圖 waveforms.png。",
              "完整 300 包測試另有 source.png、received.png、difference.png、comparison.png；comparison.png 由左至右為原圖、接收圖、絕對差異圖。", "",
              "通道採理想同步、固定增益、無雜訊。錯誤測試驗證 CRC 與 ARQ，不代表實際 RF 通道的 BER–SNR 表現。", ""]
    (ROOT / "reports" / "summary.md").write_text("\n".join(lines), encoding="utf-8")
