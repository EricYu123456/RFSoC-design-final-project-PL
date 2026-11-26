"""Per-run elapsed times measured on one host, with bounded percentile storage."""
import json
import random
import threading
from pathlib import Path
from time import perf_counter_ns

import numpy as np

# Only non-overlapping work in the same serial worker shares a ranking.
# Read waits, queue ages, one-time costs and overlapping observations stay separate.
STAGES = {
    "camera_read": ("Wait", "camera read", "call"),
    "gray_resize": ("Camera", "grayscale / resize", "frame"),
    "jpeg_encode": ("Camera", "JPEG encode incl. quality retries", "frame"),
    "queue_age": ("Wait", "encoded frame in TX queue", "frame"),
    "batch_wait": ("Wait", "TX waiting for first JPEG", "call"),
    "pack": ("TX", "pack + SHA-256", "block"),
    "upload": ("TX", "write TX BRAM + descriptor", "block"),
    "readback": ("Setup", "first TX BRAM readback", "run"),
    "start": ("TX", "TX START handshake", "block"),
    "rf": ("TX", "wait for RF completion incl. retries / ACK", "block"),
    "rf_clean": ("Span", "TX RF completion, zero retries", "block"),
    "rf_retry": ("Span", "TX RF completion, with retries", "block"),
    "rx_wait": ("Wait", "B waiting for DONE incl. idle", "block"),
    "quality": ("RX", "read descriptor + quality map", "block"),
    "payload": ("RX", "read used BRAM bytes in aligned words", "block"),
    "done_to_release": ("Span", "DONE observed to RX slot released", "block"),
    "release": ("RX", "clear quality + release slot", "block"),
    "unpack": ("RX", "verify SHA-256 + unpack", "block"),
    "jpeg_decode": ("Recorder", "JPEG decode and validate", "frame"),
    "bgr": ("Recorder", "gray to BGR", "frame"),
    "writer_open": ("Setup", "open MP4 writer", "run"),
    "mp4_write": ("Recorder", "write MP4 frame", "output frame"),
    "record_queue_wait": ("Wait", "enqueue JPEGs for recording", "data block"),
    "record_drain": ("Finish", "drain and join recorder", "run"),
    "close": ("Finish", "finalize MP4", "run"),
    "display_wait": ("Wait", "latest preview waiting for UI thread", "preview"),
    "display": ("Preview", "submit Jupyter display update", "preview"),
    "prefix_trial": ("Trial", "aligned read of used bytes + zero padding", "block"),
    "full_trial": ("Trial", "previous full Python BRAM read", "block"),
    "rx_service": ("Span", "DONE observed to preview-ready", "data block"),
    "tx_age": ("Span", "camera read return to TX completion", "sent frame"),
}
SAMPLE_LIMIT = 2048


class StageTimes:
    def __init__(self):
        self.data = {}
        self.lock = threading.Lock()
        self.random = random.Random(42)

    def end(self, stage, start):
        self.add(stage, perf_counter_ns() - start)

    def add(self, stage, duration_ns):
        with self.lock:
            count, total, peak, samples = self.data.get(stage, (0, 0, 0, []))
            count += 1
            if len(samples) < SAMPLE_LIMIT:
                samples.append(duration_ns)
            else:
                i = self.random.randrange(count)
                if i < SAMPLE_LIMIT:
                    samples[i] = duration_ns
            self.data[stage] = count, total + duration_ns, max(peak, duration_ns), samples

    def save(self, output, mode):
        rows = []
        for key, (count, total, peak, samples) in self.data.items():
            group, label, unit = STAGES.get(key, ("Setup", key, "run"))
            p50, p95 = np.percentile(samples, [50, 95]) / 1e6
            rows.append(dict(stage=key, group=group, label=label, unit=unit, count=count,
                             total_ms=total / 1e6, mean_ms=total / count / 1e6,
                             p50_ms=float(p50), p95_ms=float(p95), max_ms=peak / 1e6))
        ranked = {}
        for group in ("Camera", "TX", "RX", "Recorder"):
            members = [r for r in rows if r["group"] == group]
            total = sum(r["total_ms"] for r in members)
            if total:
                for r in members:
                    r["worker_pct"] = 100 * r["total_ms"] / total
                ranked[group] = max(members, key=lambda r: r["total_ms"])["stage"]
        path = Path(output) / "latency.json"
        path.write_text(json.dumps(dict(mode=mode, percentile_reservoir=SAMPLE_LIMIT,
                                        top_by_worker=ranked, stages=rows), indent=2), encoding="utf-8")
        return str(path)


def show_latency(path):
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    print(report["mode"], " | ms")
    print(f"{'Group':8} {'Stage':45} {'Unit':12} {'N':>7} {'Mean':>9} {'P50':>9} {'P95':>9} {'Max':>9} {'Work %':>8}")
    for r in sorted(report["stages"], key=lambda row: (row["group"], -row["total_ms"])):
        share = f'{r["worker_pct"]:.1f}' if "worker_pct" in r else ""
        print(f'{r["group"]:8} {r["label"]:45} {r["unit"]:12} {r["count"]:7} {r["mean_ms"]:9.2f} '
              f'{r["p50_ms"]:9.2f} {r["p95_ms"]:9.2f} {r["max_ms"]:9.2f} {share:>8}')
    for group, key in report["top_by_worker"].items():
        r = next(row for row in report["stages"] if row["stage"] == key)
        print(f'{group} 最大項目  {r["label"]}  ({r["worker_pct"]:.1f}% of measured worker time)')
    by_key = {row["stage"]: row for row in report["stages"]}
    if "prefix_trial" in by_key and "payload" in by_key:
        full, prefix = (by_key[k]["mean_ms"] for k in ("payload", "prefix_trial"))
        if full > 0:
            print(f"BRAM A/B 平均 {full:.2f} → {prefix:.2f} ms/區塊，完整讀取減去短讀為 {full-prefix:.2f} ms")
    if "full_trial" in by_key:
        full, live = (by_key[k]["mean_ms"] for k in ("full_trial", "payload"))
        print(f"BRAM on the same slots: full Python {full:.2f} ms, live prefix {live:.2f} ms")
    print(path)
