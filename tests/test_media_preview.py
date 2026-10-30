import subprocess

from imageio_ffmpeg import get_ffmpeg_exe

from backend.app import media


def test_video_preview_works_without_system_ffmpeg(monkeypatch, tmp_path):
    source = tmp_path / "test.mp4"
    subprocess.run([get_ffmpeg_exe(), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "color=blue:size=64x64:duration=0.1", "-c:v", "mpeg4", "-y", str(source)],
                   check=True, capture_output=True)
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    preview = tmp_path / "preview.jpg"
    media.make_preview(source, preview)
    with media.Image.open(preview) as image:
        assert image.format == "JPEG"
        assert image.width <= 480 and image.height <= 480
