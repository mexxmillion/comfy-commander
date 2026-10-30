from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app import media
from backend.app.h3_media import h3_input_paths


@pytest.fixture
def inputs(monkeypatch, tmp_path):
    root = tmp_path / 'input'
    root.mkdir()
    monkeypatch.setattr(media, 'get_settings', lambda: SimpleNamespace(
        comfyui_input_dir=root, cloud_output_dir=tmp_path / 'outputs'))
    monkeypatch.setattr(media, 'require_admin', lambda *args, **kwargs: None)
    return root


@pytest.mark.parametrize('extension,kind', [('png', 'image'), ('mp4', 'video'), ('wav', 'audio')])
def test_source_is_copied_to_valid_managed_reference(inputs, extension, kind):
    original = inputs / 'existing' / f'reference.{extension}'
    original.parent.mkdir()
    original.write_bytes(b'original-source-content')
    payload = media.SourceImport(file=f'existing/reference.{extension}')
    request = Request({'type': 'http'})
    result = media.import_source(payload, request)
    assert result['kind'] == kind
    assert result['file'].startswith('h3_mobile/imported/')
    assert (inputs / result['file']).read_bytes() == original.read_bytes()
    assert original.exists()
    assert media.import_source(payload, request)['file'] == result['file']
    graph = {'1': {'class_type': 'MiniMaxH3ReferencePack', 'inputs': {
        'references_json': __import__('json').dumps({'references': [{'file': result['file']}]})}}}
    assert h3_input_paths(graph) == {result['file']}


@pytest.mark.parametrize('path', ['../outside.png', '/outside.png', 'C:/private/file.png', '..\\outside.png'])
def test_source_import_rejects_paths_outside_library(inputs, path):
    with pytest.raises(HTTPException):
        media.import_source(media.SourceImport(file=path), Request({'type': 'http'}))
    assert not (inputs / 'h3_mobile').exists()


def test_managed_source_is_reused_without_copy(inputs):
    source = inputs / 'h3_mobile' / 'original.png'
    source.parent.mkdir()
    source.write_bytes(b'existing-upload')
    result = media.import_source(media.SourceImport(file='h3_mobile/original.png'), Request({'type': 'http'}))
    assert result['file'] == 'h3_mobile/original.png'
    assert not (source.parent / 'imported').exists()
