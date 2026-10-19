"""Async process-isolated JSON Schema validation, 7 s including worker startup.

Only fixed interpreter/worker paths are executed. Payload goes to pipes, never
argv/files/logs. Minimal env, isolated Python, no stderr capture. Lifetime control
is not a sandbox or memory limit. Dependencies must exist in sklr's interpreter.
Importing does not spawn. Cancellation is propagated after shielded cleanup.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import anyio

from external_mcp.limits import MAX_SCHEMA_BYTES, ExternalError, arguments_view, bounded_json
from external_mcp.process_containment import create_containment
from external_mcp.schema_worker import CODES, MAX_INPUT_BYTES
from external_mcp.stdio_transport import stop_process

VALIDATION_TIMEOUT_S = 7
MAX_OUTPUT_BYTES = 128
WORKER = Path(__file__).with_name('schema_worker.py').resolve()
WORKER_ENV_KEYS = frozenset({'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL', 'LC_CTYPE'})


def worker_env():
    # Exact replacement, not merging with the parent's credential-bearing env.
    return {key: value for key, value in os.environ.items() if key.upper() in WORKER_ENV_KEYS}


def encode_payload(schema, arguments):
    schema = bounded_json(schema, MAX_SCHEMA_BYTES, 'schema_limit')
    arguments = arguments_view(arguments)
    payload = json.dumps({'schema': schema, 'arguments': arguments},
                         ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_INPUT_BYTES:
        raise ExternalError('schema_worker_input')
    return payload


async def _dispose(process, containment):
    with anyio.CancelScope(shield=True):
        try:
            if process is not None:
                # Kill before waiting: a synchronous regex cannot cooperate.
                try:
                    containment.kill_tree()
                finally:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                    await stop_process(process)
        finally:
            containment.close()


async def validate_arguments(schema, arguments):
    """Return None when valid; raise only fixed ExternalError codes otherwise.

    The 7 s execution deadline is separate from bounded, shielded process cleanup.
    It also remains subordinate to the client's total operation deadline.
    """
    payload = encode_payload(schema, arguments)
    process, containment = None, None
    try:
        with anyio.fail_after(VALIDATION_TIMEOUT_S):
            containment = create_containment()
            # Shield only spawning/attachment so cancellation cannot strand a child
            # between receiving its PID and assigning it to the Windows job.
            with anyio.CancelScope(shield=True):
                process = await anyio.open_process(
                    [sys.executable, '-I', str(WORKER)], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    env=worker_env(), cwd=str(WORKER.parent),
                    **containment.spawn_kwargs,
                )
                containment.attach(process.pid)
            await process.stdin.send(payload)
            await process.stdin.aclose()
            output = bytearray()
            while True:
                try:
                    chunk = await process.stdout.receive(MAX_OUTPUT_BYTES + 1)
                except anyio.EndOfStream:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_OUTPUT_BYTES:
                    raise ExternalError('schema_validation_failed')
            await process.wait()
            if process.returncode != 0:
                raise ExternalError('schema_validation_failed')
            try:
                code = bytes(output).decode('ascii')
            except UnicodeError:
                raise ExternalError('schema_validation_failed') from None
            if code not in {value + '\n' for value in CODES}:
                raise ExternalError('schema_validation_failed')
            code = code[:-1]
            if code != 'ok':
                raise ExternalError(code)
    except TimeoutError:
        raise ExternalError('schema_validation_timeout') from None
    except ExternalError:
        raise
    except Exception:
        raise ExternalError('schema_validation_failed') from None
    finally:
        if containment is not None:
            try:
                await _dispose(process, containment)
            except Exception:
                raise ExternalError('schema_validation_cleanup_failed') from None
