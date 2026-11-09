"""Prepare row-major grayscale pixels and independent zlib CRC references."""
import argparse
import json
import struct
import zlib
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def prepare(input_path=None):
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    if input_path:
        with Image.open(input_path) as source:
            image = source.convert("L").resize((640, 480), Image.Resampling.LANCZOS)
        source_name = str(Path(input_path).resolve())
    else:
        pixels = bytearray(640 * 480)
        for y in range(480):
            for x in range(640):
                pixels[y * 640 + x] = (x * 255 // 639) if y < 240 else ((x * 17 + y * 31) ^ (x >> 2)) & 255
        image = Image.frombytes("L", (640, 480), bytes(pixels))
        draw = ImageDraw.Draw(image)
        draw.rectangle((24, 24, 615, 95), fill=25, outline=255, width=3)
        draw.text((40, 45), "RFSoC 4x2 | OOK + CRC-32 + ARQ | 640 x 480", fill=255)
        for i, value in enumerate((0, 64, 128, 192, 255)):
            draw.ellipse((30 + i * 120, 120, 110 + i * 120, 200), fill=value, outline=255, width=2)
        source_name = "deterministic grayscale test pattern"
    raw = image.tobytes()
    image.save(assets / "source.png")
    (assets / "image.raw").write_bytes(raw)
    (assets / "image.mem").write_text("".join(f"{value:02x}\n" for value in raw), encoding="ascii")
    data_crcs, ack_crcs = [], []
    for packet in range(300):
        header = struct.pack(">HHH", 0, packet, 1024)
        payload = raw[packet * 1024:(packet + 1) * 1024]
        data_crcs.append(zlib.crc32(header + payload))
        ack_crcs.append(zlib.crc32(struct.pack(">HH", 0, packet)))
    for name, values in (("data_crc.mem", data_crcs), ("ack_crc.mem", ack_crcs)):
        (assets / name).write_text("".join(f"{value:08x}\n" for value in values), encoding="ascii")
    metadata = {"source": source_name, "width": 640, "height": 480, "bytes": len(raw),
                "packets": 300, "payload_bytes": 1024, "pixel_crc32": f"{zlib.crc32(raw):08X}"}
    (assets / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Optional PNG/JPEG/BMP photograph")
    args = parser.parse_args()
    prepare(args.input)
