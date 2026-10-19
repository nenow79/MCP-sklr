"""Pure external MCP validation: no subprocesses or real servers."""
import copy

import pytest

from external_mcp.profiles import ProfileError, validate_profile


def http(**values):
    return dict(alias='example', transport='http', url='https://example.test/mcp', **values)


def test_defaults_and_detached_values():
    proposal = http(headers_env={'X-Api-Key': 'REMOTE_KEY'})
    row = validate_profile(proposal)
    assert row['enabled'] is False and row['description'] == ''
    assert row['bearer_env'] is None and row['ca_file'] is None
    row['headers_env']['X-Api-Key'] = 'CHANGED'
    assert proposal['headers_env']['X-Api-Key'] == 'REMOTE_KEY'


@pytest.mark.parametrize('transport', ['http', 'sse'])
def test_network_transports(transport):
    proposal = http(bearer_env='REMOTE_TOKEN', ca_file=r'C:\Cert Files\corp.pem')
    proposal['transport'] = transport
    assert validate_profile(proposal)['transport'] == transport


def test_stdio_windows_spaces_and_no_command_splitting():
    row = validate_profile(dict(alias='local', transport='stdio',
        command=r'C:\Program Files\Python\python.exe',
        args=['server.py', '--name', 'with spaces', ''],
        cwd=r'C:\My Servers', env_refs={'API_TOKEN': 'REMOTE_TOKEN'}))
    assert row['command'] == r'C:\Program Files\Python\python.exe'
    assert row['args'][2:] == ['with spaces', '']


@pytest.mark.parametrize('url', [
    'https://user:pass@example.test/mcp', 'https://example.test/mcp?token=x',
    'https://example.test/mcp?', 'https://example.test/mcp#x',
    'ftp://example.test/mcp', 'https://example.test:wrong/mcp',
    'https://example.test:0/mcp', 'https://example.test:65536/mcp',
    ' https://example.test/mcp',
    'https://example.test/mcp\n', 'https://example.test/mcp%0a',
    'https://example.test\\@other.test/mcp', 'https:///mcp',
])
def test_unsafe_urls(url):
    proposal = http()
    proposal['url'] = url
    with pytest.raises(ProfileError):
        validate_profile(proposal)


@pytest.mark.parametrize('host', ['localhost', 'LOCALHOST.', '127.0.0.1', '127.5.1.2',
                                  '[::1]', '[::ffff:127.0.0.1]', '0.0.0.0'])
def test_local_self_and_panel_blocked(host):
    proposal = http()
    for port in (8101, 8102):
        proposal['url'] = f'http://{host}:{port}/mcp'
        with pytest.raises(ProfileError, match='self_or_panel_endpoint'):
            validate_profile(proposal, blocked_ports=(8101, 8102))
    proposal['url'] = f'http://{host}:9000/mcp'
    assert validate_profile(proposal, blocked_ports=(8101, 8102))


@pytest.mark.parametrize('patch', [
    {'alias': 'Bad Name'}, {'enabled': 1}, {'description': '\u202eunsafe'},
    {'token': 'do-not-echo'}, {'headers': {'Authorization': 'do-not-echo'}},
    {'verify': False}, {'bearer_env': 'not a variable'},
    {'headers_env': {'Host': 'HOST_VAR'}},
    {'headers_env': {'Cookie': 'COOKIE_VAR'}},
    {'headers_env': {'X-Test': 'VAR_A', 'x-test': 'VAR_B'}},
    {'headers_env': {'Authorization': 'VAR_A'}, 'bearer_env': 'VAR_B'},
    {'ca_file': 'relative.pem'}, {'description': []}, {'transport': []},
])
def test_invalid_network_fields_and_safe_errors(patch):
    proposal = http()
    proposal.update(patch)
    with pytest.raises(ProfileError) as raised:
        validate_profile(proposal)
    assert 'do-not-echo' not in str(raised.value)


@pytest.mark.parametrize('patch', [
    {'args': 'single command'}, {'args': [1]}, {'args': ['x\x00y']},
    {'args': ['x'] * 101}, {'cwd': 'C:relative'}, {'cwd': '\\relative'},
    {'cwd': r'C:\folder:stream'}, {'env_refs': {'TOKEN': 'literal secret'}},
    {'env_refs': {'Var': 'ONE', 'VAR': 'TWO'}}, {'shell': True},
])
def test_invalid_stdio_fields(patch):
    proposal = dict(alias='local', transport='stdio', command='python')
    proposal.update(patch)
    with pytest.raises(ProfileError):
        validate_profile(proposal)


def test_profile_size_limit():
    proposal = dict(alias='local', transport='stdio', command='python', args=['x' * 4096] * 10)
    with pytest.raises(ProfileError, match='profile_too_large'):
        validate_profile(proposal)


def test_proposal_is_not_modified():
    proposal = http()
    before = copy.deepcopy(proposal)
    validate_profile(proposal)
    assert proposal == before
