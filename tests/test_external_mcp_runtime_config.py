"""Runtime secrets and stdio environment; no real connections or child processes."""
from uuid import uuid4
import ssl

import pytest

from external_mcp.limits import ExternalError
from external_mcp.profiles import validate_profile
from external_mcp.runtime_config import prepare_runtime


def profile(transport='http', **values):
    proposal = {'alias': 'remote', 'transport': transport}
    proposal.update({'command': 'python'} if transport == 'stdio' else {'url': 'https://example.test/mcp'})
    proposal.update(values)
    result = validate_profile(proposal)
    result.update(id=str(uuid4()), revision=str(uuid4()))
    return result


def test_bearer_and_header_references_resolved_only_at_runtime():
    row = profile(bearer_env='REMOTE_TOKEN', headers_env={'X-Api-Key': 'REMOTE_KEY'})
    runtime = prepare_runtime(row, environ={'REMOTE_TOKEN': 'private-token', 'REMOTE_KEY': 'private-key'})
    assert runtime.headers == {'Authorization': 'Bearer private-token', 'X-Api-Key': 'private-key'}
    assert 'private-token' not in repr(runtime)
    assert row['bearer_env'] == 'REMOTE_TOKEN'
    assert runtime.tls.verify_mode == ssl.CERT_REQUIRED
    assert runtime.tls.check_hostname
    runtime.clear()
    assert runtime.headers == {}


@pytest.mark.parametrize('environ', [{}, {'TOKEN': ''}, {'TOKEN': 'bad\r\nheader'},
                                     {'TOKEN': 'a' * 8193}, {'TOKEN': '\ud800'}])
def test_missing_or_invalid_secrets_never_echo_values(environ):
    with pytest.raises(ExternalError) as raised:
        prepare_runtime(profile(bearer_env='TOKEN'), environ=environ)
    assert str(raised.value).startswith('Niepoprawna konfiguracja MCP: ')
    assert 'header' not in str(raised.value) or raised.value.code == 'header_invalid'
    assert 'bad' not in str(raised.value)


def test_stdio_does_not_inherit_arbitrary_parent_environment(tmp_path):
    runtime = prepare_runtime(profile('stdio', cwd=str(tmp_path),
        env_refs={'API_TOKEN': 'REMOTE_TOKEN'}), environ={
        'PATH': 'system-path', 'TEMP': str(tmp_path), 'REMOTE_TOKEN': 'private-token',
        'PANEL_TOKEN': 'panel-private', 'PYTHONPATH': 'injected',
        'NODE_OPTIONS': 'injected', 'HTTPS_PROXY': 'proxy-private',
    })
    assert runtime.env == {'PATH': 'system-path', 'TEMP': str(tmp_path), 'API_TOKEN': 'private-token'}
    assert runtime.cwd == str(tmp_path)
    runtime.clear()
    assert runtime.env == {}


def test_environment_reference_overrides_case_insensitively():
    runtime = prepare_runtime(profile('stdio', env_refs={'Path': 'CUSTOM_PATH'}),
                              environ={'PATH': 'old-path', 'CUSTOM_PATH': 'new-path'})
    assert runtime.env == {'Path': 'new-path'}


def test_missing_cwd_and_bad_ca_are_safe(tmp_path):
    with pytest.raises(ExternalError, match='cwd_unavailable'):
        prepare_runtime(profile('stdio', cwd=str(tmp_path / 'missing')), environ={})
    with pytest.raises(ExternalError, match='tls_configuration'):
        prepare_runtime(profile(ca_file=str(tmp_path / 'missing.pem')), environ={})


def test_invalid_tls_file(tmp_path):
    ca = tmp_path / 'invalid.pem'
    ca.write_text('not a certificate', encoding='utf-8')
    with pytest.raises(ExternalError, match='tls_configuration'):
        prepare_runtime(profile(ca_file=str(ca)), environ={})


def test_self_endpoint_checked_again_before_runtime():
    row = profile(url='http://localhost:8101/mcp')
    with pytest.raises(ValueError, match='self_or_panel_endpoint'):
        prepare_runtime(row, environ={}, blocked_ports=(8101, 8102))
