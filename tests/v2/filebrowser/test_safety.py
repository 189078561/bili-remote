from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from bili_remote.web import create_app
from test_file_service import login


@pytest.mark.parametrize('path', ['../outside.txt', '/etc/passwd', 'folder/../../outside.txt', '\x00bad', 'escape.txt'])
def test_read_boundaries_are_enforced_for_every_preview(settings, tmp_path, path):
    settings.ensure_directories()
    outside = tmp_path / 'outside.txt'
    outside.write_text('outside secret')
    (settings.download_root / 'escape.txt').symlink_to(outside)
    with TestClient(create_app(settings)) as client:
        login(client)
        for endpoint in ['browse', 'meta', 'text', 'image']:
            response = client.get(f'/api/files/{endpoint}', params={'root_key': 'downloads', 'path': path})
            assert response.status_code in {403, 404, 422}, (endpoint, response.text)
            assert 'outside secret' not in response.text
    assert outside.read_text() == 'outside secret'


def test_operations_cannot_move_directory_into_itself_or_modify_root(settings):
    settings.ensure_directories()
    (settings.download_root / 'folder/child').mkdir(parents=True)
    (settings.download_root / 'folder/file.txt').write_text('preserved')
    with TestClient(create_app(settings)) as client:
        login(client)
        for operation, source, target in [
            ('move', 'folder', {'root_key': 'downloads', 'relative_path': 'folder/child'}),
            ('delete', '', None),
        ]:
            response = client.post('/api/files/operations/preview', json={
                'operation': operation,
                'sources': [{'root_key': 'downloads', 'relative_path': source}],
                'target': target, 'conflict_policy': 'abort'})
            assert response.status_code == 200
            assert response.json()['summary']['blocked'] == 1
    assert (settings.download_root / 'folder/file.txt').read_text() == 'preserved'


def test_read_only_file_cannot_be_saved(settings, monkeypatch):
    import os
    from pathlib import Path
    import bili_remote.file_service as module

    settings.ensure_directories()
    target = settings.download_root / 'readonly.txt'
    target.write_text('preserved')
    original_access = os.access
    with TestClient(create_app(settings)) as client:
        login(client)
        # Model the OS denying writes even when tests run as root.
        monkeypatch.setattr(module.os, 'access', lambda path, mode, *args, **kwargs:
                            False if Path(path) == target and mode & os.W_OK
                            else original_access(path, mode, *args, **kwargs))
        document = client.get('/api/files/text', params={'root_key': 'downloads', 'path': target.name}).json()
        assert document['editable'] is False
        response = client.put('/api/files/text', json={
            'root_key': 'downloads', 'relative_path': target.name,
            'content': 'forbidden', 'fingerprint': document['fingerprint']})
        assert response.status_code == 422
        assert '不可写' in response.json()['detail']
    assert target.read_text() == 'preserved'
