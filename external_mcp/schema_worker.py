"""Private JSON Schema worker. Input via stdin, only a fixed status code on stdout.

Launched by an owned process supervisor (7 s including startup). This module alone
cannot interrupt synchronous regex/validation. It is not a sandbox or memory cap.
No argument values, schemas or exception messages are emitted. Import does not run
validation or spawn. Standalone entry point supports Python -I and an absolute path.
"""
import json
import sys

MAX_INPUT_BYTES = 196608
MAX_DEPTH = 32
MAX_NODES = 50000
DIALECT = 'https://json-schema.org/draft/2020-12/schema'
CODES = frozenset({
    'ok', 'schema_invalid', 'schema_dialect', 'schema_reference_blocked',
    'arguments_schema', 'schema_validation_failed', 'schema_worker_input',
})


def check_policy(schema):
    """Conservative v1: document-local JSON Pointers, no resource IDs or anchors.

    Walks all JSON nodes, including annotations, intentionally fail-closed. It may
    reject annotation/example data containing reserved schema keys. No implicit
    external resources or dynamic-reference semantics are accepted.
    """
    if not isinstance(schema, dict):
        return 'schema_invalid'
    pending = [(schema, 0)]
    nodes = 0
    while pending:
        node, depth = pending.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            return 'schema_invalid'
        if isinstance(node, dict):
            if '$schema' in node and node['$schema'] not in (DIALECT, DIALECT + '#'):
                return 'schema_dialect'
            if any(key in node for key in ('$id', '$anchor', '$dynamicAnchor',
                                           '$dynamicRef', '$recursiveRef', '$recursiveAnchor')):
                return 'schema_reference_blocked'
            if '$ref' in node:
                ref = node['$ref']
                if not isinstance(ref, str) or not ref.startswith('#/'):
                    return 'schema_reference_blocked'
            pending.extend((value, depth + 1) for value in node.values())
        elif isinstance(node, list):
            pending.extend((value, depth + 1) for value in node)
    return None


def validate_payload(schema, arguments):
    """No coercion/default insertion; format is annotation, not an assertion."""
    code = check_policy(schema)
    if code:
        return code
    if not isinstance(arguments, dict):
        return 'schema_worker_input'
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry

        def deny_resource(uri):
            # Never fetch a URL or file, even if the preflight missed a reference.
            raise RuntimeError('resource retrieval disabled')

        validator = Draft202012Validator(schema, registry=Registry(retrieve=deny_resource))
        try:
            Draft202012Validator.check_schema(schema)
        except Exception:
            return 'schema_invalid'
        # Stop at the first error; never serialize the error, path or instance.
        return 'arguments_schema' if next(validator.iter_errors(arguments), None) is not None else 'ok'
    except Exception:
        return 'schema_validation_failed'


def main():
    code = 'schema_worker_input'
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) <= MAX_INPUT_BYTES:
            payload = json.loads(raw)
            if isinstance(payload, dict) and set(payload) == {'schema', 'arguments'}:
                code = validate_payload(payload['schema'], payload['arguments'])
    except Exception:
        pass
    if code not in CODES:
        code = 'schema_validation_failed'
    sys.stdout.buffer.write(code.encode('ascii') + b'\n')
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
