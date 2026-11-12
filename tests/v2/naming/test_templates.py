import pytest

from bili_remote.v2.naming.templates import render_filename, validate_template, season_common_probe
from bili_remote.v2.naming.errors import NamingError


def fixture(media_type="series"):
    context = {"identity": {"media_type": media_type}, "selected_title": {"value": "The Last of Us"}, "metadata": {"year": {"value": 2023}}}
    file = {"ref": {"relative_path": "original.mkv"}, "episode": {"season": {"value": 1}, "episode": {"value": 1}}, "probe": {key: {"value": value, "confirmed": True} for key, value in {"resolution": "1080p", "video_codec": "H265", "audio_codec": "AAC"}.items()}}
    return context, file


@pytest.mark.parametrize("kind,expected", [("series", "The.Last.of.Us.2023.S01E01.1080p.WEB-DL.H265.AAC-HDVWEB.mkv"), ("anime", "The.Last.of.Us.2023.S01E01.1080p.WEB-DL.H265.AAC-HDVWEB.mkv"), ("movie", "The.Last.of.Us.2023.1080p.WEB-DL.H265.AAC-HDVWEB.mkv")])
def test_official_templates(kind, expected):
    assert render_filename(*fixture(kind))["filename"] == expected


def test_unknown_technical_parameters_absent():
    context, file = fixture("movie")
    file["probe"] = {}
    result = render_filename(context, file)
    assert result["filename"] == "The.Last.of.Us.2023.WEB-DL-HDVWEB.mkv"
    assert set(result["missing_fields"]) == {"Resolution", "VideoCodec", "AudioCodec"}
    assert "x265" not in result["filename"]


@pytest.mark.parametrize("template", ["{Title.__class__}", "{Title!r}", "{Season:02d}", "{Resolution:00}", "../{Title}", "{Unknown}", "{Title", "{Title}" * 100])
def test_template_rejects_unsafe_interpolation(template):
    with pytest.raises(NamingError):
        validate_template(template)


def test_candidates_custom_optional_fields_and_large_episode():
    context, file = fixture()
    context["titles"] = [{"id": "ja", "text": "原題"}, {"id": "hant", "text": "繁體名稱"}]
    file["episode"]["episode"]["value"] = 123
    file["probe"].update(hdr={"value": ["HDR10"]}, bit_depth={"value": 10}, frame_rate={"value": {"numerator": 24000, "denominator": 1001}})
    result = render_filename(context, file, {"selected_title_id": "hant", "include_hdr": True, "include_frame_rate": True, "include_bit_depth": True})
    assert result["filename"].startswith("繁體名稱.2023.S01E123")
    assert "HDR10.23.976.10.mkv" in result["filename"]
    assert render_filename(context, file, {"custom_title": "My title"})["filename"].startswith("My.title.")


def test_mixed_season_omits_unshared_values():
    context, first = fixture()
    _, second = fixture()
    second["probe"]["video_codec"]["value"] = "H264"
    common, warnings = season_common_probe([first, second])
    assert common["video_codec"] is None
    assert common["resolution"]["value"] == "1080p"
    first["probe"] = common
    result = render_filename(context, first, template_id="season")
    assert "H265" not in result["filename"]
    assert warnings
