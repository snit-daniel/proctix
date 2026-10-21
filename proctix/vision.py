"""Local inference with immutable model digests. Scores are evidence, not identity proof or liveness."""

import hashlib
import io
import threading
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

MODELS = {
    "face_detection_yunet_2023mar.onnx": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    "auraface_v1.onnx": "a7933ea5330113b01c9b60351d8f4c33003f145d8470ac5f0e52ee2effe25c60",
}
Image.MAX_IMAGE_PIXELS = 16_000_000


def canonical_image(data: bytes):
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ("JPEG", "PNG", "WEBP") or source.width * source.height > 16_000_000:
                raise ValueError("Use a JPEG, PNG or WebP image up to 16 megapixels")
            image = ImageOps.exif_transpose(source).convert("RGB")
            if min(image.size) < 160:
                raise ValueError("Image must be at least 160 pixels on each side")
            image.thumbnail((1920, 1920))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90)  # strips metadata
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("Invalid or oversized image") from None


class AuraFaceRecognizer:
    """Publisher's ArcFace-compatible RGB / 127.5 preprocessing, no model downloads or fallback."""

    name = "AuraFace-v1-ResNet100"

    def __init__(self, path):
        self.net = cv2.dnn.readNetFromONNX(str(path))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    @staticmethod
    def alignCrop(image, face):
        source = np.asarray(face[4:14], dtype=np.float64).reshape(5, 2)
        target = np.array(
            [
                [38.2946, 51.6963],
                [73.5318, 51.5014],
                [56.0252, 71.7366],
                [41.5493, 92.3655],
                [70.7299, 92.2041],
            ],
            dtype=np.float64,
        )
        source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
        centered = source - source_mean
        variance = np.square(centered).sum() / 5
        if variance < 1e-6 or not np.isfinite(source).all():
            raise ValueError("Invalid face landmarks")
        u, singular, vt = np.linalg.svd((target - target_mean).T @ centered / 5)
        sign = np.array([1.0, 1.0 if np.linalg.det(u @ vt) > 0 else -1.0])
        rotation = (u * sign) @ vt
        scale = float(singular @ sign) / variance
        matrix = np.column_stack((scale * rotation, target_mean - scale * rotation @ source_mean))
        return cv2.warpAffine(image, matrix, (112, 112), flags=cv2.INTER_LINEAR)

    def feature(self, aligned):
        blob = cv2.dnn.blobFromImage(aligned, 1 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        self.net.setInput(blob)
        embedding = self.net.forward().reshape(-1).astype(np.float32)
        norm = np.linalg.norm(embedding)
        if embedding.size != 512 or not np.isfinite(embedding).all() or norm < 1e-8:
            raise ValueError("Invalid recognition embedding")
        return embedding / norm

    @staticmethod
    def match(first, second, _metric=None):
        return float(np.clip(np.dot(first, second), -1, 1))


class FaceEngine:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.Lock()
        self.detector = None
        self.recognizer = None
        self.error = "Model weights have not been installed"
        try:
            for name, sha in MODELS.items():
                path = self.directory / name
                if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                    raise ValueError(f"Model digest mismatch: {name}")
            self.detector = cv2.FaceDetectorYN.create(
                str(self.directory / next(iter(MODELS))), "", (320, 320), 0.9
            )
            self.recognizer = AuraFaceRecognizer(self.directory / "auraface_v1.onnx")
            # Exercise the actual model graph at startup; loading alone is insufficient.
            self.recognizer.feature(np.full((112, 112, 3), 128, dtype=np.uint8))
            self.error = ""
        except (OSError, ValueError, cv2.error) as e:
            self.detector = None
            self.error = str(e)

    @property
    def available(self):
        return self.detector is not None

    def _faces(self, data):
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Invalid image")
        self.detector.setInputSize((image.shape[1], image.shape[0]))
        _, faces = self.detector.detect(image)
        return image, faces

    def assess(self, data):
        return self._assess(data)

    def assess_document(self, data):
        return self._assess(data, document=True)

    @staticmethod
    def primary_portrait(faces, document=False):
        if faces is None or len(faces) == 0:
            return None
        if len(faces) == 1:
            return faces[0]
        if document:
            order = sorted(faces, key=lambda face: float(face[2] * face[3]), reverse=True)
            # Some IDs have a small secondary printed portrait. Select only an
            # unambiguous dominant portrait; this says nothing about authenticity.
            if order[0][2] * order[0][3] >= 2.5 * order[1][2] * order[1][3]:
                return order[0]
        return None

    def _assess(self, data, document=False):
        if not self.available:
            return {"status": "unavailable", "requires_human_review": True}
        with self.lock:
            image, faces = self._faces(data)
            count = 0 if faces is None else len(faces)
            # Measure the face itself: a sharp background can hide a blurry face.
            region = image
            primary = self.primary_portrait(faces, document)
            if primary is not None:
                x, y, w, h = primary[:4]
                left, top = max(0, int(x)), max(0, int(y))
                right, bottom = min(image.shape[1], int(x + w)), min(image.shape[0], int(y + h))
                region = image[top:bottom, left:right]
                if not region.size:
                    return {"status": "invalid_face_region", "requires_human_review": True}
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            normalized = cv2.resize(
                gray, (112, 112), interpolation=cv2.INTER_AREA if min(gray.shape) > 112 else cv2.INTER_LINEAR
            )
            normalized_sharpness = float(cv2.Laplacian(normalized, cv2.CV_64F).var())
            brightness = float(gray.mean())
            face_pixels = float(min(primary[2:4])) if primary is not None else 0
            return {
                "status": "assessed",
                "face_count": count,
                "boxes": []
                if faces is None
                else [
                    {
                        "x": round(float(f[0]) / image.shape[1], 5),
                        "y": round(float(f[1]) / image.shape[0], 5),
                        "width": round(float(f[2]) / image.shape[1], 5),
                        "height": round(float(f[3]) / image.shape[0], 5),
                    }
                    for f in faces
                ],
                "brightness": round(brightness, 2),
                "sharpness": round(sharpness, 2),
                "normalized_sharpness": round(normalized_sharpness, 2),
                "face_pixels": round(face_pixels, 2),
                "quality_ok": primary is not None
                and 40 < brightness < 220
                and sharpness > 40
                and face_pixels >= 80,
                # Capture recommendations are stricter than inference eligibility.
                # Small printed ID portraits and smooth large selfies should not
                # be rejected solely by a raw, resolution-dependent blur score.
                "comparison_usable": primary is not None
                and 25 < brightness < 235
                and normalized_sharpness >= 20
                and face_pixels >= 48,
                "document_portrait_selected": document and count > 1 and primary is not None,
                "quality_region": "face" if primary is not None else "frame",
                "detection_confidences": [] if faces is None else [round(float(f[-1]), 4) for f in faces],
                "requires_human_review": True,
                "model": "YuNet-2023mar",
            }

    def compare_live(self, reference, candidate):
        try:
            return self.compare(reference, candidate, reference_document=False)
        except (ValueError, OSError, cv2.error):
            return {"status": "unavailable", "requires_human_review": True, "liveness": "not_established"}

    def compare(self, reference, candidate, *, reference_document=True):
        if not self.available:
            return {"status": "unavailable", "requires_human_review": True, "liveness": "not_established"}
        quality = self.assess(candidate)
        reference_quality = self.assess_document(reference) if reference_document else self.assess(reference)
        if not quality.get("comparison_usable", quality.get("quality_ok")) or not reference_quality.get(
            "comparison_usable", reference_quality.get("quality_ok")
        ):
            return {
                "status": "insufficient_quality",
                "reference": reference_quality,
                "candidate": quality,
                "requires_human_review": True,
                "liveness": "not_established",
            }
        with self.lock:
            first, f1 = self._faces(reference)
            second, f2 = self._faces(candidate)
            a = self.recognizer.feature(
                self.recognizer.alignCrop(first, self.primary_portrait(f1, document=True))
            )
            b = self.recognizer.feature(self.recognizer.alignCrop(second, f2[0]))
            score = float(self.recognizer.match(a, b, cv2.FaceRecognizerSF_FR_COSINE))
        return {
            "status": "compared",
            "cosine_similarity": round(score, 5),
            "model": "AuraFace-v1-ResNet100",
            "review_threshold": 0.4,
            "threshold_source": "Provisional AuraFace review threshold; not institution-calibrated",
            "quality_warning": not quality.get("quality_ok") or not reference_quality.get("quality_ok"),
            "candidate": quality,
            "reference": reference_quality,
            "requires_human_review": True,
            "liveness": "not_established",
            "interpretation": "Similarity score is uncalibrated for this institution; review against the enrollment record.",
        }
