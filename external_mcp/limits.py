"""Bounded JSON data crossing the external MCP boundary; no logging of payloads."""
import json
import math

from external_mcp.profiles import ProfileError

DEFAULT_TIMEOUT_S = 60
MAX_TIMEOUT_S = 300
CONNECT_TIMEOUT_S = 15
CLEANUP_TIMEOUT_S = 5
MAX_TOOLS = 200
MAX_SCHEMA_BYTES = 65536
MAX_CATALOG_BYTES = 1048576
MAX_ARGUMENT_BYTES = 65536
MAX_RESULT_BYTES = 1048576
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 50000


class ExternalError(ProfileError):
    """Safe machine-readable error; upstream exception text must never be attached."""


def timeout_seconds(value=DEFAULT_TIMEOUT_S):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExternalError('timeout')
    if not math.isfinite(value) or not 1 <= value <= MAX_TIMEOUT_S:
        raise ExternalError('timeout')
    return float(value)


def bounded_json(value, max_bytes, code):
    """Validate type/depth/node/size before encoding. Returns detached JSON data.

    This is a post-decode application limit, not a transport wire/memory limit.
    The adapter must also bound received messages; do not claim protection against
    unlimited upstream allocation based on this function alone.
    """
    nodes = 0
    estimate = 0
    active = set()

    def visit(item, depth):
        nonlocal nodes, estimate
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ExternalError(code)
        if isinstance(item, str):
            # UTF-8 takes at least one byte per character. Check before allocation.
            estimate += len(item) + 2
            if estimate > max_bytes:
                raise ExternalError(code)
            try:
                estimate += len(item.encode('utf-8')) - len(item)
            except UnicodeError:
                raise ExternalError(code) from None
        elif item is None or isinstance(item, bool):
            estimate += 4
        elif isinstance(item, int):
            if item.bit_length() > 4096:
                raise ExternalError(code)
            estimate += 1
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ExternalError(code)
            estimate += 1
        elif isinstance(item, (dict, list)):
            identity = id(item)
            if identity in active:
                raise ExternalError(code)
            active.add(identity)
            estimate += 2
            try:
                if isinstance(item, dict):
                    for key, child in item.items():
                        if not isinstance(key, str):
                            raise ExternalError(code)
                        visit(key, depth + 1)
                        visit(child, depth + 1)
                        estimate += 2
                else:
                    for child in item:
                        visit(child, depth + 1)
                        estimate += 1
            finally:
                active.remove(identity)
        else:
            raise ExternalError(code)
        if estimate > max_bytes:
            raise ExternalError(code)

    visit(value, 0)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ExternalError(code) from None
    if len(encoded) > max_bytes:
        raise ExternalError(code)
    return json.loads(encoded)


def arguments_view(arguments):
    if not isinstance(arguments, dict):
        raise ExternalError('arguments')
    return bounded_json(arguments, MAX_ARGUMENT_BYTES, 'arguments_limit')


def catalog_view(tools):
    """Complete inputSchema only; reject oversize rather than silently truncate."""
    if not isinstance(tools, list) or len(tools) > MAX_TOOLS:
        raise ExternalError('tools_limit')
    records, names = [], set()
    for tool in tools:
        if hasattr(tool, 'model_dump'):
            tool = tool.model_dump(mode='json', by_alias=True, exclude_none=True)
        if not isinstance(tool, dict):
            raise ExternalError('tool_format')
        name = tool.get('name')
        description = tool.get('description', '')
        schema = tool.get('inputSchema')
        if (not isinstance(name, str) or not name or len(name) > 256 or name in names or
                not isinstance(description, str) or not isinstance(schema, dict)):
            raise ExternalError('tool_format')
        names.add(name)
        schema = bounded_json(schema, MAX_SCHEMA_BYTES, 'schema_limit')
        record = {'name': name, 'description': description, 'inputSchema': schema}
        # Preserve optional metadata as data; never infer approval from annotations.
        for field in ('title', 'annotations', 'outputSchema'):
            if field in tool:
                record[field] = tool[field]
        records.append(record)
    return bounded_json(records, MAX_CATALOG_BYTES, 'catalog_limit')


def result_view(result):
    """Preserve MCP content blocks, structuredContent and isError without fetching links."""
    if hasattr(result, 'model_dump'):
        result = result.model_dump(mode='json', by_alias=True, exclude_none=True)
    if not isinstance(result, dict):
        raise ExternalError('result_format')
    if not isinstance(result.get('content'), list) or not isinstance(result.get('isError', False), bool):
        raise ExternalError('result_format')
    output = {'content': result['content'], 'isError': result.get('isError', False)}
    if 'structuredContent' in result:
        output['structuredContent'] = result['structuredContent']
    for block in output['content']:
        if not isinstance(block, dict) or not isinstance(block.get('type'), str):
            raise ExternalError('content_format')
    return bounded_json(output, MAX_RESULT_BYTES, 'result_limit')
