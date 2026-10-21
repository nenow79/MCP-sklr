"""Bounded immutable JSON and a closed selector language, without I/O."""
import ipaddress
import json
import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


class ArgumentRuleError(ValueError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class JsonValue:
    kind: str
    value: object


def freeze_json(value):
    nodes, size = 0, 0
    active = set()

    def visit(item, depth):
        nonlocal nodes, size
        nodes += 1
        if nodes > 10000 or depth > 16:
            raise ArgumentRuleError('json_limit')
        kind = type(item)
        if kind is str:
            if len(item) > 4096:
                raise ArgumentRuleError('json_limit')
            try:
                size += len(json.dumps(item, ensure_ascii=False).encode('utf-8'))
            except UnicodeError:
                raise ArgumentRuleError('invalid_json') from None
            result = JsonValue('string', item)
        elif item is None:
            size += 4
            result = JsonValue('null', None)
        elif kind is bool:
            size += 4 if item else 5
            result = JsonValue('boolean', item)
        elif kind is int:
            if not -(2 ** 63) <= item < 2 ** 63:
                raise ArgumentRuleError('invalid_json')
            size += len(str(item))
            result = JsonValue('integer', item)
        elif kind is float:
            if not math.isfinite(item):
                raise ArgumentRuleError('invalid_json')
            size += len(json.dumps(item, allow_nan=False))
            result = JsonValue('number', item)
        elif kind in (dict, list):
            identity = id(item)
            if identity in active:
                raise ArgumentRuleError('invalid_json')
            active.add(identity)
            size += 2 + max(0, len(item) - 1)
            try:
                if kind is list:
                    result = JsonValue('array', tuple(visit(child, depth + 1) for child in item))
                else:
                    if any(type(key) is not str for key in item):
                        raise ArgumentRuleError('invalid_json')
                    pairs = []
                    for key, child in item.items():
                        name = visit(key, depth + 1).value
                        size += 1
                        pairs.append((name, visit(child, depth + 1)))
                    result = JsonValue('object', tuple(sorted(pairs)))
            except RuntimeError:
                raise ArgumentRuleError('invalid_json') from None
            finally:
                active.remove(identity)
        else:
            raise ArgumentRuleError('invalid_json')
        if size > 128 * 1024:
            raise ArgumentRuleError('json_limit')
        return result

    return visit(value, 1)


def thaw_json(value):
    if value.kind == 'object':
        return {key: thaw_json(child) for key, child in value.value}
    if value.kind == 'array':
        return [thaw_json(child) for child in value.value]
    return value.value


def _fields(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ArgumentRuleError('invalid_selector')


def _host(value):
    if (type(value) is not str or not value or not value.isascii()
            or value != value.lower() or '%' in value):
        raise ArgumentRuleError('invalid_url_rule')
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if len(value) > 253 or any(
                re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) is None
                for part in value.split('.')):
            raise ArgumentRuleError('invalid_url_rule') from None
    else:
        if address.compressed != value:
            raise ArgumentRuleError('invalid_url_rule')
    return value


def _url_path(value):
    if (type(value) is not str or not value.startswith('/') or '//' in value
            or any(ch in value for ch in '\\%?#')
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in value)
            or any(part in ('.', '..') for part in value.split('/'))):
        raise ArgumentRuleError('invalid_url_rule')
    return value


@dataclass(frozen=True, slots=True, repr=False)
class Selector:
    kind: str
    data: tuple


def parse_selector(value):
    value = thaw_json(freeze_json(value))

    def parse(raw, depth):
        if depth > 8 or type(raw) is not dict or type(raw.get('type')) is not str:
            raise ArgumentRuleError('invalid_selector')
        kind = raw['type']
        if kind == 'literal':
            _fields(raw, ('type', 'value'))
            return Selector(kind, (freeze_json(raw['value']),))
        if kind == 'enum':
            _fields(raw, ('type', 'values'))
            values = raw['values']
            if type(values) is not list or not 1 <= len(values) <= 32:
                raise ArgumentRuleError('invalid_selector')
            frozen = tuple(freeze_json(item) for item in values)
            if len(set(frozen)) != len(frozen):
                raise ArgumentRuleError('invalid_selector')
            return Selector(kind, frozen)
        if kind == 'string':
            _fields(raw, ('type', 'max_length'))
            limit = raw['max_length']
            if type(limit) is not int or not 1 <= limit <= 4096:
                raise ArgumentRuleError('invalid_selector')
            return Selector(kind, (limit,))
        if kind in ('integer', 'number'):
            _fields(raw, ('type', 'minimum', 'maximum'))
            bounds = (raw['minimum'], raw['maximum'])
            types = (int,) if kind == 'integer' else (int, float)
            if any(type(bound) not in types for bound in bounds) or bounds[0] > bounds[1]:
                raise ArgumentRuleError('invalid_selector')
            return Selector(kind, bounds)
        if kind == 'boolean':
            _fields(raw, ('type',))
            return Selector(kind, ())
        if kind == 'object':
            _fields(raw, ('type', 'properties', 'required'))
            props, required = raw['properties'], raw['required']
            if (type(props) is not dict or type(required) is not list
                    or any(type(key) is not str for key in required)
                    or len(set(required)) != len(required) or set(required) - set(props)):
                raise ArgumentRuleError('invalid_selector')
            return Selector(kind, (tuple((key, parse(child, depth + 1)) for key, child in sorted(props.items())),
                                   tuple(sorted(required))))
        if kind == 'url':
            _fields(raw, ('type', 'schemes', 'hosts', 'ports', 'path'))
            for key in ('schemes', 'hosts', 'ports'):
                if type(raw[key]) is not list or not raw[key]:
                    raise ArgumentRuleError('invalid_url_rule')
            schemes, hosts, ports = raw['schemes'], raw['hosts'], raw['ports']
            if any(type(item) is not str or item not in ('http', 'https') for item in schemes):
                raise ArgumentRuleError('invalid_url_rule')
            hosts = tuple(_host(item) for item in hosts)
            if any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
                raise ArgumentRuleError('invalid_url_rule')
            if any(len(set(items)) != len(items) for items in (schemes, hosts, ports)):
                raise ArgumentRuleError('invalid_url_rule')
            _fields(raw['path'], ('kind', 'path'))
            if raw['path']['kind'] not in ('exact', 'tree'):
                raise ArgumentRuleError('invalid_url_rule')
            path = _url_path(raw['path']['path'])
            return Selector(kind, (tuple(schemes), hosts, tuple(ports), raw['path']['kind'], path))
        raise ArgumentRuleError('invalid_selector')

    return parse(value, 1)


def _matches_url(selector, value):
    if (type(value) is not str or any(ch in value for ch in '\\?#')
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in value)):
        return False
    try:
        parsed = urlsplit(value)
        if (parsed.username is not None or parsed.password is not None or not parsed.hostname
                or parsed.netloc.endswith(':')):
            return False
        host = _host(parsed.hostname)
        path = _url_path(parsed.path or '/')
        schemes, hosts, ports, scope_kind, scope_path = selector.data
        port = parsed.port if parsed.port is not None else {'http': 80, 'https': 443}.get(parsed.scheme)
        return (parsed.scheme in schemes and host in hosts and port in ports
                and (path == scope_path or scope_kind == 'tree'
                     and path.startswith(scope_path.rstrip('/') + '/')))
    except (ValueError, TypeError):
        return False


def matches(selector, value):
    if type(selector) is not Selector or type(value) is not JsonValue:
        raise ArgumentRuleError('invalid_match_input')
    kind, data = selector.kind, selector.data
    if kind == 'literal':
        return value == data[0]
    if kind == 'enum':
        return value in data
    if kind == 'string':
        return value.kind == 'string' and len(value.value) <= data[0]
    if kind in ('integer', 'number'):
        allowed = ('integer',) if kind == 'integer' else ('integer', 'number')
        return value.kind in allowed and data[0] <= value.value <= data[1]
    if kind == 'boolean':
        return value.kind == 'boolean'
    if kind == 'object':
        if value.kind != 'object':
            return False
        props, required = dict(data[0]), set(data[1])
        actual = dict(value.value)
        return (not set(actual) - set(props) and required <= set(actual)
                and all(matches(props[key], child) for key, child in actual.items()))
    if kind == 'url':
        return value.kind == 'string' and _matches_url(selector, value.value)
    return False
