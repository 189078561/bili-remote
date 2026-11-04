"""Real ffmpeg regression: a valid request near EOF must find the last frame."""
import shutil
import subprocess
import pytest
from bili_remote.screenshots import ScreenshotService

@pytest.mark.parametrize('fps,duration,target', [(10, 3, 2.95), (1, 3, 2.95), (24, 1, .95)])
def test_last_frame_fallback(tmp_path, fps, duration, target):
    ffmpeg = shutil.which('ffmpeg')
    assert ffmpeg, 'ffmpeg is required for media acceptance'
    source = tmp_path / 'synthetic.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
        f'testsrc2=size=320x180:rate={fps}:duration={duration}', '-c:v', 'mpeg4',
        '-pix_fmt', 'yuv420p', str(source)], check=True)
    service = ScreenshotService.__new__(ScreenshotService)
    actual, output = service._extract_frame_with_retries((ffmpeg,), source, target,
        duration, 0, 0, tmp_path / 'frame.jpg', 240, {'fps': fps}, lambda: False)
    assert output.is_file() and output.stat().st_size > 0
    assert 0 <= actual <= duration - 1 / fps + .001
