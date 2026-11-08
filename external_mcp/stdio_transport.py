"""Bounded stdio transport with exact env replacement, no shell and no SDK monkeypatch.

The spawned process tree is contained (Windows Job Object / POSIX process group, see
process_containment.py): descendants are killed on cleanup. This is lifetime control, not a sandbox.
No stderr is persisted or logged. Importing this module does not spawn a process.
"""
import os
import subprocess
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar

import anyio

from external_mcp.limits import CLEANUP_TIMEOUT_S, ExternalError
from external_mcp.process_containment import create_containment

MAX_MESSAGE_BYTES = 2 * 1024 * 1024
READ_CHUNK_BYTES = 16384

# Internal operation-local deadline, not model input or persistent session state.
# Set by the owner immediately before session teardown, reset after transport exit.
cleanup_deadline_context = ContextVar('external_mcp_cleanup_deadline', default=None)


class LineFramer:
    """Bound each UTF-8 JSON line before decoding. SDK stdio uses newline framing."""
    def __init__(self, limit=MAX_MESSAGE_BYTES):
        self.limit = limit
        self.buffer = bytearray()

    def feed(self, chunk):
        if not isinstance(chunk, bytes):
            raise ExternalError('stdio_format')
        lines = []
        start = 0
        while start < len(chunk):
            end = chunk.find(b'\n', start)
            stop = len(chunk) if end < 0 else end
            if len(self.buffer) + stop - start > self.limit:
                raise ExternalError('message_limit')
            self.buffer.extend(chunk[start:stop])
            if end < 0:
                break
            line = bytes(self.buffer)
            self.buffer.clear()
            if line.endswith(b'\r'):
                line = line[:-1]
            if line:
                lines.append(line)
            start = end + 1
        return lines

    def finish(self):
        if self.buffer:
            raise ExternalError('stdio_incomplete_message')


def parse_line(line):
    """Parse exactly like MCP SDK 2.x stdio (jsonrpc_message_adapter); safe error only."""
    from mcp import types
    try:
        return types.jsonrpc_message_adapter.validate_json(line, by_name=False)
    except Exception:
        raise ExternalError('stdio_invalid_json') from None


async def stop_process(process, *, deadline=None):
    """One total cleanup deadline, including pipe and process handle close.

    The caller may supply an earlier shared deadline. Stage windows reserve
    time for escalation; none can restart the total budget. A timed-out close
    is a failure even if the direct process has already exited.
    """
    start = time.monotonic()
    deadline = min(deadline, start + CLEANUP_TIMEOUT_S) if deadline is not None else start + CLEANUP_TIMEOUT_S
    window = max(0, deadline - start) / 4
    failed = False
    with anyio.CancelScope(shield=True):
        if process.stdin is not None:
            with anyio.move_on_after(max(0, min(window, deadline - time.monotonic()))) as scope:
                try:
                    await process.stdin.aclose()
                except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
                    pass
                except Exception:
                    failed = True
            failed = failed or scope.cancel_called
        for action in (None, 'terminate', 'kill'):
            if process.returncode is not None:
                break
            if action:
                try:
                    getattr(process, action)()
                except ProcessLookupError:
                    pass
                except Exception:
                    failed = True
            remaining = max(0, deadline - time.monotonic())
            allowance = min(window, remaining) if action != 'kill' else remaining / 2
            with anyio.move_on_after(allowance):
                try:
                    await process.wait()
                except Exception:
                    failed = True
        if process.returncode is None:
            failed = True
        with anyio.move_on_after(max(0, deadline - time.monotonic())) as scope:
            try:
                await process.aclose()
            except Exception:
                failed = True
        failed = failed or scope.cancel_called
    if failed:
        raise ExternalError('stdio_cleanup_failed') from None


def _command(runtime):
    # Windows batch files may implicitly invoke cmd.exe even with shell=False.
    # v1 requires an executable (e.g. node.exe + JS entrypoint), not npx.cmd.
    if os.name == 'nt' and runtime.command.lower().endswith(('.cmd', '.bat')):
        raise ExternalError('stdio_batch_not_supported')
    return [runtime.command, *runtime.args]


@asynccontextmanager
async def bounded_stdio_client(runtime):
    """SDK-compatible streams. runtime.env replaces the process env, never merges it.

    UTF-8 only; unsupported SDK types fail safely. No raw parser exception is logged.
    Cleanup is shielded from caller cancellation. This is not a process sandbox.
    """
    from mcp.shared.message import SessionMessage

    containment = None
    process = None
    streams_to_close = []
    failure = None
    cancelled = None
    phase = 'stdio_containment_failed'
    try:
        containment = create_containment()
        phase = 'stdio_spawn_failed'
        command = _command(runtime)
        process = await anyio.open_process(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=runtime.cwd, env=dict(runtime.env),
            **containment.spawn_kwargs,
        )
        phase = 'stdio_containment_failed'
        containment.attach(process.pid)
        phase = 'stdio_stream_failed'
        received, read_stream = anyio.create_memory_object_stream(0)
        streams_to_close.extend((received, read_stream))
        write_stream, pending = anyio.create_memory_object_stream(0)
        streams_to_close.extend((write_stream, pending))
    except BaseException as exc:
        if isinstance(exc, anyio.get_cancelled_exc_class()):
            cancelled = exc
        else:
            failure = exc.code if isinstance(exc, ExternalError) else phase

    async def cleanup():
        # All paths, including failed __aenter__, use this one cleanup attempt.
        # Synchronous containment APIs must remain short OS calls; this is not
        # a bound on a malicious blocking injected factory or an OS hard hang.
        deadline = time.monotonic() + CLEANUP_TIMEOUT_S
        inherited_deadline = cleanup_deadline_context.get()
        if inherited_deadline is not None:
            deadline = min(deadline, inherited_deadline)
        code = None
        with anyio.CancelScope(shield=True):
            for stream in streams_to_close:
                try:
                    stream.close()
                except Exception:
                    code = 'stdio_cleanup_failed'
            if process is not None:
                if failure == 'stdio_containment_failed':
                    try:
                        process.kill()  # never leave an unattached server running
                    except ProcessLookupError:
                        pass
                    except Exception:
                        code = 'stdio_cleanup_failed'
                try:
                    await stop_process(process, deadline=deadline)
                except Exception:
                    code = 'stdio_cleanup_failed'
            if containment is not None:
                try:
                    if process is not None:
                        containment.kill_tree()
                except Exception:
                    code = 'stdio_containment_failed'
                finally:
                    try:
                        containment.close()
                    except Exception:
                        code = 'stdio_containment_failed'
        return code

    if failure is not None or cancelled is not None:
        cleanup_code = await cleanup()
        if cancelled is not None:
            raise cancelled
        error = ExternalError(cleanup_code or failure)
        if cleanup_code:
            error.cause_code = failure
        raise error from None

    async def reader():
        framer = LineFramer()
        async with received:
            try:
                while True:
                    try:
                        chunk = await process.stdout.receive(READ_CHUNK_BYTES)
                    except anyio.EndOfStream:
                        framer.finish()
                        return
                    if not chunk:
                        framer.finish()
                        return
                    for line in framer.feed(chunk):
                        await received.send(SessionMessage(parse_line(line)))
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                return
            except ExternalError as exc:
                await received.send(ExternalError(exc.code))
            except Exception:
                await received.send(ExternalError('stdio_read_failed'))

    async def writer():
        async with pending:
            try:
                async for item in pending:
                    try:
                        encoded = item.message.model_dump_json(by_alias=True, exclude_unset=True).encode('utf-8')
                    except Exception:
                        raise ExternalError('stdio_write_format') from None
                    if len(encoded) > MAX_MESSAGE_BYTES:
                        raise ExternalError('message_limit')
                    await process.stdin.send(encoded + b'\n')
            except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
                await received.aclose()
            except ExternalError:
                # Close the receive side: session sees a safe connection failure.
                await received.aclose()

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(reader)
            group.start_soon(writer)
            try:
                yield read_stream, write_stream
            finally:
                # End transport workers before process cleanup. They only own
                # memory streams and cancellable pipe I/O, not process handles.
                group.cancel_scope.cancel()
    except BaseException as exc:
        if isinstance(exc, anyio.get_cancelled_exc_class()):
            cancelled = exc
        else:
            from external_mcp.client import _leaves
            failure = next((leaf.code for leaf in _leaves(exc)
                            if isinstance(leaf, ExternalError)), 'stdio_failed')
    finally:
        cleanup_code = await cleanup()
    if cancelled is not None:
        raise cancelled
    if cleanup_code or failure:
        error = ExternalError(cleanup_code or failure)
        if cleanup_code and failure:
            error.cause_code = failure
        raise error from None
