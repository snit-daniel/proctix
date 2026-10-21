from pathlib import Path
import pytest
from proctix.vision import FaceEngine, canonical_image


def test_image_is_decoded_and_metadata_stripped(image):
    first = canonical_image(image)
    assert first.startswith(b"\xff\xd8")
    # JPEG re-encoding is lossy for the text-bearing identity fixture.
    import io
    from PIL import Image

    decoded = Image.open(io.BytesIO(canonical_image(first)))
    assert decoded.size == Image.open(io.BytesIO(image)).size
    assert not decoded.getexif()
    with pytest.raises(ValueError):
        canonical_image(b"not an image")


def test_missing_models_fail_closed(tmp_path, image):
    engine = FaceEngine(tmp_path)
    assert not engine.available
    assert engine.compare(image, image)["status"] == "unavailable"
    assert engine.compare(image, image)["requires_human_review"]


def test_official_models_load_and_do_not_find_a_face_in_a_blank_image(image):
    models = Path("models")
    if not (models / "auraface_v1.onnx").exists():
        pytest.skip("Install official weights using scripts/fetch_models.py")
    engine = FaceEngine(models)
    assert engine.available, engine.error
    assessment = engine.assess(image)
    assert assessment["face_count"] == 0
    assert not assessment["quality_ok"]
    result = engine.compare(image, image)
    assert result["status"] == "insufficient_quality"
    assert result["liveness"] == "not_established"


def test_evaluation_reports_errors_and_quality_rejections_separately():
    from scripts.evaluate_faces import summarize

    result = summarize(
        [
            {"same_person": True, "score": 0.8},
            {"same_person": True, "score": None},
            {"same_person": False, "score": 0.9},
            {"same_person": False, "score": 0.1},
        ],
        0.5,
    )
    assert result["same_person_unassessable"] == 1
    assert result["false_match"]["rate"] == 0.5
    assert result["false_nonmatch"]["rate"] == 0


def test_quality_checks_face_region_instead_of_sharp_background(tmp_path):
    import numpy as np

    engine = FaceEngine(tmp_path)
    engine.detector = object()
    pixels = np.random.default_rng(42).integers(0, 256, (300, 300, 3), dtype=np.uint8)
    pixels[100:200, 100:200] = 128
    detected = np.array([[100, 100, 100, 100, *([0] * 10), 0.99]], dtype=np.float32)
    engine._faces = lambda _: (pixels, detected)
    result = engine.assess(b"fixture")
    assert result["quality_region"] == "face"
    assert result["brightness"] == 128
    assert result["sharpness"] == 0
    assert not result["quality_ok"]


def test_document_can_select_a_dominant_portrait_but_selfies_must_have_one_face():
    import numpy as np

    primary = np.array([0, 0, 200, 200, *([0] * 10), 0.99], dtype=np.float32)
    small = np.array([210, 0, 50, 50, *([0] * 10), 0.99], dtype=np.float32)
    faces = np.array([small, primary])
    assert FaceEngine.primary_portrait(faces) is None
    assert np.array_equal(FaceEngine.primary_portrait(faces, document=True), primary)
    ambiguous = np.array([primary, primary.copy()])
    assert FaceEngine.primary_portrait(ambiguous, document=True) is None


def test_live_comparison_requires_a_single_reference_face_and_keeps_inference_errors_advisory(
    tmp_path, monkeypatch
):
    engine = FaceEngine(tmp_path)

    def compare(reference, candidate, *, reference_document=True):
        assert reference_document is False
        raise ValueError("Invalid inference input")

    monkeypatch.setattr(engine, "compare", compare)
    result = engine.compare_live(b"reference", b"camera")
    assert result["status"] == "unavailable"
    assert result["requires_human_review"]


def test_arcface_alignment_preserves_reference_geometry():
    import numpy as np
    from proctix.vision import AuraFaceRecognizer

    pixels = np.random.default_rng(9).integers(0, 256, (112, 112, 3), dtype=np.uint8)
    landmarks = [38.2946, 51.6963, 73.5318, 51.5014, 56.0252, 71.7366, 41.5493, 92.3655, 70.7299, 92.2041]
    face = np.array([0, 0, 112, 112, *landmarks, 0.99])
    assert np.array_equal(AuraFaceRecognizer.alignCrop(pixels, face), pixels)
    with pytest.raises(ValueError):
        AuraFaceRecognizer.alignCrop(pixels, np.zeros(15))


def test_wrong_primary_weights_do_not_silently_select_another_model(tmp_path):
    from proctix.vision import MODELS

    # Even an installed legacy model cannot satisfy the required primary weights.
    (tmp_path / "face_recognition_sface_2021dec.onnx").write_bytes(b"legacy")
    for name in MODELS:
        (tmp_path / name).write_bytes(b"invalid primary weight")
    engine = FaceEngine(tmp_path)
    assert not engine.available
    assert "digest mismatch" in engine.error


def test_moderate_id_portrait_can_be_compared_without_claiming_ideal_capture(tmp_path):
    import numpy as np

    engine = FaceEngine(tmp_path)
    engine.detector = object()
    pixels = np.random.default_rng(23).integers(40, 210, (160, 160, 3), dtype=np.uint8)
    detected = np.array([[40, 40, 62, 62, *([0] * 10), 0.99]], dtype=np.float32)
    engine._faces = lambda _: (pixels, detected)
    result = engine.assess_document(b"synthetic")
    assert result["comparison_usable"]
    assert not result["quality_ok"]
    detected[0, 2:4] = 30
    assert not engine.assess_document(b"synthetic")["comparison_usable"]


def test_sharpness_normalization_does_not_reject_a_smooth_high_resolution_face(tmp_path):
    import numpy as np
    import cv2

    engine = FaceEngine(tmp_path)
    engine.detector = object()
    small = np.random.default_rng(12).integers(70, 185, (112, 112, 3), dtype=np.uint8)
    pixels = cv2.resize(small, (448, 448), interpolation=cv2.INTER_CUBIC)
    detected = np.array([[0, 0, 448, 448, *([0] * 10), 0.99]], dtype=np.float32)
    engine._faces = lambda _: (pixels, detected)
    result = engine.assess(b"synthetic")
    assert result["normalized_sharpness"] > result["sharpness"]
    assert result["comparison_usable"]
    pixels[:] = 128
    assert not engine.assess(b"synthetic")["comparison_usable"]
