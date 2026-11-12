import asyncio
from copy import deepcopy
import shutil
import subprocess

import pytest

from bili_remote.v2.naming.probe import MediaProbeAdapter, probe_from_payload


def test_hdr_depth_exact_rate_and_unknowns():
    payload = {"streams": [{"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080, "pix_fmt": "yuv420p10le", "avg_frame_rate": "24000/1001", "color_transfer": "smpte2084", "side_data_list": [{"side_data_type": "Mastering display metadata"}, {"side_data_type": "DOVI configuration record"}]}, {"codec_type": "audio", "codec_name": "aac", "channels": 2}], "format": {"format_name": "matroska", "duration": "1.5"}}
    result = probe_from_payload(payload)
    assert result["video_codec"]["value"] == "H265"
    assert result["frame_rate"]["value"] == {"numerator": 24000, "denominator": 1001}
    assert result["bit_depth"]["value"] == 10
    assert result["hdr"]["value"] == ["DV", "HDR10"]
    empty = probe_from_payload({"streams": [{"codec_type": "video"}]})
    assert empty["resolution"]["value"] is None
    assert empty["bit_depth"]["value"] is None
    assert empty["hdr"]["value"] is None


def test_real_synthetic_video_each_file_and_failure(settings, tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe not installed")
    settings.ffprobe_command = (ffprobe,)
    paths = []
    for width, height in ((320, 240), (640, 360)):
        target = tmp_path / f"{height}.mkv"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=25", "-t", "0.12", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)], check=True, capture_output=True)
        paths.append(target)
    async def run():
        adapter = MediaProbeAdapter(settings)
        first = await adapter.probe(paths[0])
        second = await adapter.probe(paths[1])
        assert first["height"]["value"] == 240
        assert second["height"]["value"] == 360
        assert first["audio_codec"]["value"] is None
        assert first["bit_depth"]["value"] == 8
        assert first["probe_version"]
        paths[0].write_bytes(b"invalid media")
        failure = await adapter.probe(paths[0])
        assert failure["probe_error"]
        assert failure["height"]["value"] is None
        manual = deepcopy(second)
        manual["resolution"].update(value="manual", manual=True, source="manual")
        locked = await adapter.probe(paths[1], source_ref="file", previous=manual)
        assert locked["resolution"]["value"] == "manual"
        assert adapter.evidence_for("file")["resolution"]["value"] == "360p"
    asyncio.run(run())
