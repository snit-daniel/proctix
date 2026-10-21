"""Download the versioned Google MediaPipe FaceLandmarker model for local calibration."""

from pathlib import Path
import hashlib
import urllib.request

URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
if __name__ == "__main__":
    path = Path("proctix/static/vendor/gaze/face_landmarker.task")
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL, timeout=45) as response:
        data = response.read(10 * 1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()
    if digest != "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff":
        raise RuntimeError("Gaze model digest mismatch; refusing installation")
    path.write_bytes(data)
    print("Verified FaceLandmarker v1: " + digest)
