from bili_remote.v2.contracts import RenderRequest
from bili_remote.v2.workbench.release_title import ReleaseTitleBuilder


def test_filename_title_omits_unknown_year_and_season():
    request = RenderRequest.model_validate({
        "context": {
            "context_id": "filename-only",
            "revision": 1,
            "resources": [{"root_key": "downloads", "relative_path": "盗妖行.E01.mp4", "kind": "file"}],
            "selected_title": {"value": None, "source": "default"},
            "metadata": {"year": {"value": None, "source": "default"}},
            "episode": {
                "season": {"value": None, "source": "default"},
                "episode": {"value": 1, "source": "filename"},
            },
            "effective_rules": {"naming_rule": {"template_id": "series"}},
        },
        "rule": {"template": "{Title} {Year} S{Season:00}E{Episode:00}"},
    })

    result = ReleaseTitleBuilder().build(request.context.model_dump(exclude_none=True), request.rule)

    assert result["text"] == "盗妖行.E01"
    assert result["context_revision"] == 1
