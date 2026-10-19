"""Manual diagnostic (not collected by pytest): runs the SDK stdio path WITHOUT error masking.

Local temporary FastMCP server only; no network, no secrets. Prints full tracebacks,
which production code intentionally hides. Run: python tests\\diag_external_mcp_stdio.py
"""
import inspect
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anyio

from external_mcp.runtime_config import RuntimeConfig
from external_mcp.stdio_transport import bounded_stdio_client

SERVER = '''
from fastmcp import FastMCP
mcp = FastMCP("diag")

@mcp.tool
def echo(text: str) -> str:
    """Echo."""
    return text

if __name__ == "__main__":
    mcp.run()
'''


def leaves(exc, depth=0):
    inner = getattr(exc, 'exceptions', None)
    if inner is None:
        print('  ' * depth + f'LEAF {type(exc).__module__}.{type(exc).__name__}: {exc}')
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        return
    print('  ' * depth + f'GROUP {type(exc).__name__}')
    for item in inner:
        leaves(item, depth + 1)


async def run(runtime):
    from mcp import ClientSession
    with anyio.fail_after(25):
        async with bounded_stdio_client(runtime) as streams:
            print('transport opened')
            async with ClientSession(streams[0], streams[1], read_timeout_seconds=20) as session:
                print('session entered')
                init = await session.initialize()
                print('initialized:', type(init).__name__)
                tools = await session.list_tools()
                print('tools:', [tool.name for tool in tools.tools])


def main():
    import mcp.client.stdio as stdio
    print('python', sys.version.split()[0], sys.executable)
    print('--- SDK _parse_line ---')
    try:
        print(inspect.getsource(stdio._parse_line))
    except Exception as exc:
        print('unavailable:', type(exc).__name__)
    with tempfile.TemporaryDirectory() as folder:
        script = Path(folder) / 'server.py'
        script.write_text(SERVER, encoding='utf-8')
        runtime = RuntimeConfig(transport='stdio', command=sys.executable, args=[str(script)],
                                cwd=folder, env={'PATH': '', 'SYSTEMROOT': ''})
        # Reuse the interpreter environment keys needed on Windows, but no secrets.
        import os
        runtime.env = {k: v for k, v in os.environ.items()
                       if k.upper() in ('PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC',
                                        'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA')}
        try:
            anyio.run(run, runtime)
            print('RESULT: OK')
        except BaseException as exc:
            print('RESULT: FAILED')
            leaves(exc)


if __name__ == '__main__':
    main()
