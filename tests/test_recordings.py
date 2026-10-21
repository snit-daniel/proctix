import json
import shutil
import subprocess
from uuid import uuid4

import pytest
from .conftest import started, upload, client_for


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for combined recording")
def test_full_recording_combines_restarts_and_keeps_review_media_scoped(
    application, admin, student, image, tmp_path
):
    sid = started(admin, student, image)
    sources = []
    for index, size in enumerate(("320x240", "640x360")):
        path = tmp_path / f"source-{index}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={'blue' if index else 'red'}:s={size}:r=15:d=0.6",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.6",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        sources.append(path.read_bytes())
    for payload in sources:
        stream = str(uuid4())
        student.post(
            f"/api/sessions/{sid}/events",
            json={
                "event_id": str(uuid4()),
                "kind": "recording_started",
                "stream_id": stream,
            },
        )
        upload(student, sid, payload, "recording", stream=stream, mime="video/mp4")
    media = admin.get(f"/api/sessions/{sid}/review-media").json()
    assert media["student"]["name"] == "student"
    assert all(media["identity"].values())
    assert len(media["recording_segments"]) == 2
    response = admin.get(f"/api/sessions/{sid}/recording")
    assert response.status_code == 200, response.text[:200] if response.status_code != 200 else ""
    assert response.headers["content-type"] == "video/mp4"
    timeline = json.loads(response.headers["x-recording-timeline"])
    assert len(timeline) == 2
    assert timeline[0]["offset"] == 0
    assert timeline[1]["offset"] == timeline[0]["duration"]
    output = tmp_path / "combined.mp4"
    output.write_bytes(response.content)
    info = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(output),
            ],
            check=True,
            capture_output=True,
        ).stdout
    )
    assert float(info["format"]["duration"]) >= 1.1
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert student.get(f"/api/sessions/{sid}/recording").status_code == 403
    with client_for(application, "admin", institution="beta") as other:
        assert other.get(f"/api/sessions/{sid}/review-media").status_code == 404
        assert other.get(f"/api/sessions/{sid}/recording").status_code == 404


def test_review_media_does_not_invent_recordings_and_attaches_nearby_images(admin, student, image):
    sid = started(admin, student, image)
    assert admin.get(f"/api/sessions/{sid}/recording").status_code == 404
    camera = upload(student, sid, image, "camera")
    event = student.post(
        f"/api/sessions/{sid}/events",
        json={
            "event_id": str(uuid4()),
            "kind": "focus_lost",
        },
    ).json()
    rows = admin.get(f"/api/sessions/{sid}/observations").json()
    incident = next(row for row in rows if row["id"] == event["id"])
    assert incident["images"][0]["id"] == camera["id"]
