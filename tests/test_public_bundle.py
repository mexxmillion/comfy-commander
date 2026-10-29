import zipfile

import pytest

from runtime.h3.build_community import extract_source, source_archive


def test_sources_require_immutable_public_coordinates():
    url, checksum, strip = source_archive({'url': 'https://github.com/owner/repo.git', 'revision': 'a' * 40})
    assert url.endswith('/owner/repo/zip/' + 'a' * 40) and checksum is None and strip
    for item in [{'url': 'https://github.com/owner/repo', 'revision': 'main'},
                 {'url': 'https://private.invalid/owner/repo', 'revision': 'a' * 40},
                 {'type': 'registry', 'archive_url': 'https://cdn.comfy.org/node.zip'}]:
        with pytest.raises(RuntimeError):
            source_archive(item)


@pytest.mark.parametrize('name', ['../escape.py', '/absolute.py', 'C:/private.py', 'root/../escape.py'])
def test_source_archive_cannot_escape_staging(tmp_path, name):
    archive = tmp_path / 'source.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr(name, 'untrusted')
    with pytest.raises(RuntimeError):
        extract_source(archive, tmp_path / 'stage', True)


def test_source_archive_keeps_license_and_excludes_git(tmp_path):
    archive = tmp_path / 'source.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr('repo/LICENSE', 'MIT')
        package.writestr('repo/__init__.py', '# public node')
        package.writestr('repo/.git/config', 'private metadata')
    extract_source(archive, tmp_path / 'stage', True)
    assert (tmp_path / 'stage/LICENSE').read_text() == 'MIT'
    assert not (tmp_path / 'stage/.git').exists()
