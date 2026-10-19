"""Bounded stdio transport with exact env replacement, no shell and no SDK monkeypatch.

The spawned process tree is contained (Windows Job Object / POSIX process group, see
process_containment.py): descendants are killed on cleanup. This is lifetime control, not a sandbox.
No stderr is persisted or logged. Importing this module does not spawn a process.
"""
import os
import subprocess
from contextlib import asynccontextmanager

import anyio

from external_mcp.limits import CLEANUP_TIMEOUT_S, ExternalError
from external_mcp.process_containment import create_containment

MAX_MESSAGE_BYTES = 2 * 1024 * 1024
READ_CHUNK_BYTES = 16384


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


async def stop_process(process):
    """Bounded graceful wait -> terminate -> kill. Report survivors rather than hide them."""
    with anyio.CancelScope(shield=True):
        if process.stdin is not None:
            try:
                await process.stdin.aclose()
            except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
                pass
        for action in (None, 'terminate', 'kill'):
            if process.returncode is not None:
                break
            if action:
                try:
                    getattr(process, action)()
                except ProcessLookupError:
                    break
            with anyio.move_on_after(CLEANUP_TIMEOUT_S / 3):
                await process.wait()
        if process.returncode is None:
            raise ExternalError('stdio_cleanup_failed')
        with anyio.move_on_after(1):
            await process.aclose()


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

    containment = create_containment()
    process = None
    try:
        process = await anyio.open_process(
            _command(runtime), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=runtime.cwd, env=dict(runtime.env),
            **containment.spawn_kwargs,
        )
    except Exception:
        containment.close()
        raise ExternalError('stdio_spawn_failed') from None
    try:
        containment.attach(process.pid)  # Windows: assign suspended process to job, then resume
    except Exception:
        # Fail closed: never run an uncontained server.
        with anyio.CancelScope(shield=True):
            try:
                process.kill()
            except ProcessLookupError:
                pass
            containment.close()
            try:
                await stop_process(process)
            except ExternalError:
                pass
        raise ExternalError('stdio_containment_failed') from None

    received, read_stream = anyio.create_memory_object_stream(0)
    write_stream, pending = anyio.create_memory_object_stream(0)

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
                with anyio.CancelScope(shield=True):
                    read_stream.close()
                    write_stream.close()
                    await stop_process(process)
                group.cancel_scope.cancel()
    finally:
        with anyio.CancelScope(shield=True):
            # Also runs when task-group construction/startup or cleanup raises.
            try:
                if process.returncode is None:
                    await stop_process(process)
            finally:
                try:
                    containment.kill_tree()
                finally:
                    containment.close()
                    received.close()
                    read_stream.close()
                    write_stream.close()
                    pending.close()
