"""Export viewable Gray8 images from the verified receiver BRAM dump."""
import hashlib
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

def main():
    report_dir = ROOT / "reports/batch"
    report = json.loads((report_dir / "result.json").read_text(encoding="utf-8"))
    rows = report["images"]
    if report["passed"] is not True or len(rows) != 1000:
        raise ValueError("Expected a passing 1000-frame simulation")
    destination = report_dir / "received_png"
    destination.mkdir(exist_ok=True)
    with (ROOT / "build/batch/received.raw").open("rb") as stream:
        for index, row in enumerate(rows):
            name = f"coco_gray_{index:04d}.png"
            pixels = stream.read(640*480)
            if name != row["name"] or hashlib.sha256(pixels).hexdigest() != row["received_sha256"]:
                raise ValueError(f"Receiver dump mismatch at frame {index}")
            with Image.open(ROOT.parent / "data" / name) as source:
                if pixels != source.tobytes():
                    raise ValueError(f"Source differs at frame {index}")
            Image.frombytes("L", (640, 480), pixels).save(destination / name, compress_level=1)
        if stream.read(1):
            raise ValueError("Receiver dump has extra bytes")
    print(f"Verified and exported {len(rows)} received images to {destination}")

if __name__ == "__main__":
    main()

