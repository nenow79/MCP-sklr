"""Application data limits, without any real MCP calls."""
import math

import pytest

from external_mcp.limits import (
    ExternalError, arguments_view, bounded_json, catalog_view,
    result_view, timeout_seconds,
)


@pytest.mark.parametrize('value', [True, None, '60', 0, 301, math.inf, math.nan])
def test_invalid_timeout(value):
    with pytest.raises(ExternalError):
        timeout_seconds(value)


def test_timeout_defaults_and_boundaries():
    assert timeout_seconds() == 60
    assert timeout_seconds(1) == 1
    assert timeout_seconds(300) == 300


@pytest.mark.parametrize('value', [{1: 'x'}, {'x': math.nan}, {'x': object()}, {'x': ('tuple',)}])
def test_non_json_values(value):
    with pytest.raises(ExternalError):
        arguments_view(value)


def test_cyclic_and_deep_json():
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(ExternalError):
        bounded_json(cyclic, 1000, 'limit')
    deep = {}
    for _ in range(34):
        deep = {'x': deep}
    with pytest.raises(ExternalError):
        arguments_view(deep)


def test_size_and_unicode_limits():
    assert bounded_json({'x': 'ą'}, 20, 'limit') == {'x': 'ą'}
    with pytest.raises(ExternalError):
        bounded_json({'x': 'ą' * 20}, 20, 'limit')
    with pytest.raises(ExternalError):
        arguments_view({'x': '\ud800'})
    with pytest.raises(ExternalError):
        arguments_view({'x': 'a' * 65536})


def test_catalog_preserves_schema_and_annotations():
    schema = {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']}
    tool = {'name': 'lookup', 'description': 'untrusted data', 'inputSchema': schema,
            'annotations': {'readOnlyHint': True}}
    result = catalog_view([tool])
    assert result[0]['inputSchema'] == schema
    result[0]['inputSchema']['required'].append('other')
    assert schema['required'] == ['name']
    assert result[0]['annotations'] == tool['annotations']


@pytest.mark.parametrize('tools', [
    [{'name': 'x', 'inputSchema': {}}] * 2,
    [{'name': 'x', 'inputSchema': []}],
    [{'name': 'x', 'inputSchema': {'description': 'a' * 65536}}],
    [{'name': str(i), 'inputSchema': {}} for i in range(201)],
])
def test_invalid_catalog_is_rejected_not_truncated(tools):
    with pytest.raises(ExternalError):
        catalog_view(tools)


def test_typed_result_preserved_and_links_not_fetched():
    result = {'content': [
        {'type': 'text', 'text': 'ok'},
        {'type': 'image', 'data': 'AAAA', 'mimeType': 'image/png'},
        {'type': 'resource_link', 'uri': 'https://example.test/private', 'name': 'link'},
    ], 'structuredContent': {'answer': 42}, 'isError': True}
    assert result_view(result) == result


@pytest.mark.parametrize('result', [None, {'content': 'text'},
    {'content': [], 'isError': 'false'}, {'content': [{'text': 'missing type'}]}])
def test_malformed_result(result):
    with pytest.raises(ExternalError):
        result_view(result)


def test_application_limit_is_documented_as_post_decode():
    assert 'post-decode' in bounded_json.__doc__
