"""Download official OpenCV Zoo models, verify immutable Git LFS SHA-256, preserve licenses."""

from pathlib import Path
import hashlib
import urllib.request
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proctix.vision import MODELS

ROOT = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
RAW = "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/"


def main():
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    directory.mkdir(parents=True, exist_ok=True)
    for name, digest in MODELS.items():
        if name == "auraface_v1.onnx":
            from scripts.fetch_auraface import main as install_auraface

            install_auraface()
            continue
        folder = "face_detection_yunet" if "yunet" in name else "face_recognition_sface"
        path = directory / name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            request = urllib.request.Request(
                ROOT + folder + "/" + name, headers={"User-Agent": "Proctix-model-installer/0.2"}
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read(50 * 1024 * 1024)
            if hashlib.sha256(payload).hexdigest() != digest:
                raise RuntimeError(f"Model digest mismatch: {name}; refusing to install")
            temporary = path.with_suffix(".download")
            temporary.write_bytes(payload)
            temporary.replace(path)
        for resource in ("LICENSE", "README.md"):
            request = urllib.request.Request(RAW + folder + "/" + resource)
            with urllib.request.urlopen(request, timeout=30) as response:
                (directory / (folder + "-" + resource)).write_bytes(response.read(100000))
        print(f"Verified {name}: {digest}")


if __name__ == "__main__":
    main()
