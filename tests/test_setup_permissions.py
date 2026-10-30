from html.parser import HTMLParser

import pytest

from backend.app.community import templates


class PermissionFields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('type') == 'password':
            self.fields[attrs.get('aria-label')] = attrs.get('aria-describedby')


@pytest.mark.parametrize('settings_mode', [False, True])
def test_provider_permissions_are_visible_and_attached_to_token_inputs(settings_mode):
    page = templates.get_template('setup.html').render(status={}, profiles={}, settings_mode=settings_mode)
    fields = PermissionFields()
    fields.feed(page)
    assert fields.fields['RunPod credential'] == 'runpod-permissions'
    assert fields.fields['Hugging Face credential'] == 'huggingface-permissions'
    assert 'GraphQL Read/Write' in page
    assert 'api.runpod.io/graphql' in page
    assert 'LoRA/model uploads need write access to the destination repository' in page
    assert 'Public, ungated downloads need no token' in page
    assert 'connection check does not verify write permission' in page
    assert 'connection check does not verify upload permission' in page


@pytest.mark.parametrize('settings_mode', [False, True])
def test_token_entry_explains_local_storage_and_provider_worker_use(settings_mode):
    page = templates.get_template('setup.html').render(status={}, profiles={}, settings_mode=settings_mode)
    assert 'stored on the computer running Commander' in page
    assert 'sends them to the relevant provider' in page
    assert 'transferred over SSH to your rented worker' in page
    assert page.index('id="credential-privacy"') < page.index('aria-label="RunPod credential"')
    assert 'Credentials stay on this machine' not in page
