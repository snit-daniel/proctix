"""Build a single seekable review video from independently recorded streams."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile


class RecordingError(ValueError):
    pass


def build_recording(streams, read):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RecordingError("Recording preparation requires FFmpeg on the server.")
    directory = tempfile.TemporaryDirectory(prefix="proctix-review-")
    root = Path(directory.name)
    timeline = []
    offset = 0.0
    try:
        for index, stream in enumerate(streams):
            source = root / f"source-{index}.media"
            with source.open("wb") as target:
                for identifier in stream["evidence_ids"]:
                    target.write(read(identifier))
            output = root / f"part-{index}.mp4"
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-of",
                    "json",
                    str(source),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            has_audio = any(item.get("codec_type") == "audio" for item in json.loads(probe.stdout)["streams"])
            audio_input = [] if has_audio else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            # Normalize dimensions, frame rate and audio layout across restarts.
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    *audio_input,
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0" if has_audio else "1:a:0",
                    "-shortest",
                    "-c:a",
                    "aac",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-vf",
                    "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=15,format=yuv420p",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "24",
                    "-threads",
                    "2",
                    str(output),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=600,
            )
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(output),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            duration = float(json.loads(probe.stdout)["format"]["duration"])
            timeline.append(
                {
                    "stream_id": stream["stream_id"],
                    "started_at": stream["started_at"],
                    "offset": offset,
                    "duration": duration,
                }
            )
            offset += duration
        listing = root / "parts.txt"
        listing.write_text("".join(f"file 'part-{i}.mp4'\n" for i in range(len(streams))))
        final = root / "assessment-recording.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(listing),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(final),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        return directory, final, timeline
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        directory.cleanup()
        raise RecordingError(
            "The recording could not be prepared. Some video data may be incomplete."
        ) from exc
