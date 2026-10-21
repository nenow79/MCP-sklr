"""Lexical scopes only. No filesystem checks, resolution or access grants."""
import re
import unicodedata
from dataclasses import dataclass


class PathRuleError(ValueError):
    pass


def closed(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise PathRuleError('invalid_fields')


def text(value):
    if (type(value) is not str or not 1 <= len(value) <= 4096
            or any(unicodedata.category(ch) in {'Cc', 'Cf', 'Cs'} for ch in value)):
        raise PathRuleError('invalid_text')
    return value


def hex_value(value, length):
    if type(value) is not str or re.fullmatch('[0-9a-f]{%d}' % length, value) is None:
        raise PathRuleError('invalid_digest')
    return value


def _segment(value):
    text(value)
    if (value in {'.', '..'} or value.endswith(('.', ' '))
            or any(ch in value for ch in '/\\:<>"|?*')
            or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])',
                            value.split('.')[0].upper())):
        raise PathRuleError('invalid_path')


def relative_path(value, *, allow_root=False):
    text(value)
    if value == '.' and allow_root:
        return value
    for part in value.split('/'):
        _segment(part)
    return value


def absolute_path(platform, value):
    text(value)
    if type(platform) is not str:
        raise PathRuleError('invalid_absolute_path')
    if platform == 'posix' and value.startswith('/') and not value.startswith('//'):
        if value != '/':
            relative_path(value[1:])
        return value
    if platform == 'windows' and re.match(r'^[A-Za-z]:/', value):
        if len(value) > 3:
            relative_path(value[3:])
        return value[0].upper() + value[1:]
    raise PathRuleError('invalid_absolute_path')


@dataclass(frozen=True, slots=True, repr=False)
class RootRef:
    platform: str
    path: str
    mode: str
    epoch: str

    def __post_init__(self):
        if self.mode not in ('ro', 'rw') or type(self.mode) is not str:
            raise PathRuleError('invalid_mode')
        object.__setattr__(self, 'path', absolute_path(self.platform, self.path))
        hex_value(self.epoch, 32)

    @classmethod
    def from_dict(cls, value):
        closed(value, ('platform', 'path', 'mode', 'epoch'))
        return cls(**value)


@dataclass(frozen=True, slots=True, repr=False)
class Scope:
    kind: str
    path: str

    def __post_init__(self):
        if type(self.kind) is not str or self.kind not in ('file', 'tree'):
            raise PathRuleError('invalid_scope')
        relative_path(self.path, allow_root=self.kind == 'tree')

    @classmethod
    def from_dict(cls, value):
        closed(value, ('kind', 'path'))
        return cls(**value)


def relative_target(root, path):
    if type(root) is not RootRef:
        raise PathRuleError('invalid_root')
    path = absolute_path(root.platform, path)
    if path == root.path:
        return '.'
    prefix = root.path.rstrip('/') + '/'
    if not path.startswith(prefix):
        raise PathRuleError('outside_root')
    return relative_path(path[len(prefix):])


def contains(scope, path, *, directory=False, recursive=False):
    if type(scope) is not Scope or type(directory) is not bool or type(recursive) is not bool:
        raise PathRuleError('invalid_scope')
    relative_path(path, allow_root=True)
    if scope.kind == 'file':
        return not directory and not recursive and path == scope.path
    return scope.path == '.' or path == scope.path or path.startswith(scope.path + '/')
