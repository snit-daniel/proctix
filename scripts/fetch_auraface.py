"""Install the publisher's Apache-2.0 AuraFace recognition weights, pinned by revision and SHA-256."""

from pathlib import Path
import hashlib
import sys
import urllib.request

REVISION = "af6d057c9b0ec4071d4c49c80e3539258798b609"
SHA256 = "a7933ea5330113b01c9b60351d8f4c33003f145d8470ac5f0e52ee2effe25c60"
SIZE = 260694151
ROOT = f"https://huggingface.co/fal/AuraFace-v1/resolve/{REVISION}/"


def main():
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "auraface_v1.onnx"
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != SHA256:
        temporary = target.with_suffix(".download")
        digest = hashlib.sha256()
        size = 0
        try:
            request = urllib.request.Request(
                ROOT + "glintr100.onnx", headers={"User-Agent": "Proctix-model-installer"}
            )
            with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > SIZE:
                        raise RuntimeError("Unexpected model size")
                    digest.update(chunk)
                    out.write(chunk)
                    if size % (32 * 1024 * 1024) == 0:
                        print(f"Downloaded {size // (1024 * 1024)} MiB", flush=True)
            if size != SIZE or digest.hexdigest() != SHA256:
                raise RuntimeError("AuraFace digest mismatch; refusing installation")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    for name, url in {
        "auraface-MODEL_CARD.md": ROOT + "README.md",
        "auraface-LICENSE": "https://www.apache.org/licenses/LICENSE-2.0.txt",
    }.items():
        with urllib.request.urlopen(url, timeout=30) as response:
            (directory / name).write_bytes(response.read(100000))
    print(f"Verified AuraFace v1: {SHA256}", flush=True)


if __name__ == "__main__":
    main()
