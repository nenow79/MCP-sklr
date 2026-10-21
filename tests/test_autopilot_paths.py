"""Lexical boundaries, including Windows semantics on a POSIX developer host."""
import pytest

from autopilot.paths import PathRuleError, RootRef, Scope, contains, relative_target


def root(platform='posix', path='/work/demo'):
    return RootRef(platform, path, 'rw', 'a' * 32)


@pytest.mark.parametrize('path', ['', '/src', '../src', 'src/../x', 'src/./x', 'src//x',
                                  'src/', 'src\\x', 'src/*.py', 'src/a:b', 'src/a.',
                                  'src/a ', 'src/CON.txt', 'src/COM¹', 'src/NUL',
                                  'src/a\x00', 'src/\u202efile', 'src/\ud800'])
def test_invalid_relative_scope_is_rejected(path):
    with pytest.raises(ValueError):
        Scope('tree', path)


@pytest.mark.parametrize('path,allowed', [('src', True), ('src/a.py', True), ('src2/a.py', False),
                                         ('Src/a.py', False), ('.', False), ('a/src/a.py', False)])
def test_tree_uses_complete_segments(path, allowed):
    assert contains(Scope('tree', 'src'), path) is allowed


def test_file_scope_never_authorizes_directory_scan():
    scope = Scope('file', 'src/a.py')
    assert contains(scope, 'src/a.py')
    assert not contains(scope, 'src/a.py', directory=True)
    assert not contains(scope, 'src/a.py', recursive=True)
    assert not contains(scope, 'src/a.py/child')
    assert contains(Scope('tree', '.'), 'src', directory=True, recursive=True)
    with pytest.raises(ValueError):
        Scope('file', '.')


@pytest.mark.parametrize('platform,path', [
    ('posix', '//server/share'), ('posix', '/work/../other'), ('posix', '/work/'),
    ('windows', 'C:work/demo'), ('windows', 'C:\\work\\demo'),
    ('windows', '//server/share'), ('windows', '//?/C:/demo'),
    ('windows', 'C:/work/a:b'), ('windows', 'C:/work/AUX.txt'),
    ('unknown', '/work'),
])
def test_noncanonical_absolute_root_is_rejected(platform, path):
    with pytest.raises(PathRuleError):
        root(platform, path)


def test_absolute_targets_cannot_escape_to_other_prefix_or_drive():
    assert relative_target(root(), '/work/demo/src/a.py') == 'src/a.py'
    assert relative_target(root(), '/work/demo') == '.'
    with pytest.raises(PathRuleError):
        relative_target(root(), '/work/demo2/src/a.py')
    windows = root('windows', 'c:/work/Zażółć')
    assert windows.path == 'C:/work/Zażółć'
    assert relative_target(windows, 'c:/work/Zażółć/src/a.py') == 'src/a.py'
    for path in ('D:/work/Zażółć/src/a.py', 'C:/Work/Zażółć/src/a.py', 'C:/work/zażółć/src/a.py'):
        with pytest.raises(PathRuleError):
            relative_target(windows, path)


def test_root_and_scope_have_closed_fields_and_hidden_repr():
    with pytest.raises(ValueError):
        RootRef.from_dict({'platform': 'posix', 'path': '/PRIVATE', 'mode': 'rw',
                           'epoch': 'a' * 32, 'allow_all': True})
    with pytest.raises(ValueError):
        Scope.from_dict({'kind': 'tree', 'path': 'PRIVATE', 'recursive': True})
    assert 'PRIVATE' not in repr(Scope('tree', 'PRIVATE'))
