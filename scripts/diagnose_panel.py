#!/usr/bin/env python3
"""Read-only Windows panel diagnostics; run with the project's existing Python.

Starts two short-lived loopback servers, sequentially, without importing main.py,
opening a browser, exchanging tokens, or accessing application databases.
Each probe has its own process so a stuck startup cannot hang the whole report.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]


def say(message):
    print(message, flush=True)


def probe(kind, port):
    # This child never imports main or loads the real runtime/configuration.
    sys.path.insert(0, str(ROOT))
    import logging

    logging.basicConfig(level=logging.WARNING)
    started = time.monotonic()
    say(f"PROBE={kind} port={port}; importing dependencies")
    from panel.server import PanelServer

    if kind == "basic":
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route

        async def index(request):
            return PlainTextResponse("diagnostic")

        app = Starlette(routes=[Route("/", index)])
    else:
        from panel.app import create_panel_app
        from panel.auth import PanelAuth

        # Only GET / and static files are requested. Task endpoints are unused;
        # no TaskManager, session, roots, skills or external MCP are constructed.
        app = create_panel_app(None, None, PanelAuth(port=port))

    say(f"Application constructed in {time.monotonic() - started:.3f}s")
    server = PanelServer(app, port)
    started = time.monotonic()
    try:
        ready = server.start(timeout=5)
        elapsed = time.monotonic() - started
        say(f"Original 5s startup: ready={ready}; elapsed={elapsed:.3f}s; "
            f"thread_alive={server._thread.is_alive()}; "
            f"thread_finished={server._failed.is_set()}")
        missed_deadline = not ready
        if not ready and server._thread.is_alive():
            say("Waiting up to 25s more to distinguish slow startup from failure")
            deadline = started + 30
            while (time.monotonic() < deadline and server._thread.is_alive()
                   and not server.running):
                time.sleep(0.05)
            ready = server.running
            say(f"Extended startup: ready={ready}; "
                f"elapsed={time.monotonic() - started:.3f}s")
        if not ready:
            say("RESULT=START_FAILED (see stderr/traceback above)")
            return 1

        # Direct loopback HTTP avoids corporate proxy settings.
        from http.client import HTTPConnection

        paths = ["/"] if kind == "basic" else ["/", "/static/app.js", "/api/tasks"]
        success = True
        for path in paths:
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                expected = 401 if path == "/api/tasks" else 200
                say(f"GET {path}: HTTP {response.status}; expected={expected}; "
                    f"Server-Timing={response.getheader('Server-Timing')!r}")
                response.read()
                success = success and response.status == expected
            finally:
                connection.close()
        outcome = "SLOW_START" if missed_deadline else "OK"
        say("RESULT=" + (outcome if success else "HTTP_FAILED"))
        return 0 if success else 1
    finally:
        server.stop(timeout=5)
        say(f"Cleanup: thread_alive={server._thread.is_alive()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, help="Override configured panel port")
    parser.add_argument("--probe", choices=("basic", "panel"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.probe:
        try:
            return probe(args.probe, args.port or 8102)
        except BaseException:
            traceback.print_exc()
            return 1

    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    report_path = log_dir / f"diagnose-panel-{stamp}.log"
    with report_path.open("w", encoding="utf-8") as report:
        def record(message):
            say(message)
            report.write(message + "\n")
            report.flush()

        record("Panel diagnostics (no main.py, runtime databases or browser)")
        record(f"Report: {report_path}")
        record(f"Python: {sys.version}")
        record(f"Platform: {sys.platform}")
        for package in ("fastmcp", "uvicorn", "starlette", "anyio", "h11", "httptools", "uvloop"):
            try:
                installed = version(package)
            except PackageNotFoundError:
                installed = "not installed"
            record(f"{package}: {installed}")
        try:
            result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                    capture_output=True, text=True, timeout=5)
            record(f"Git commit: {result.stdout.strip() if result.returncode == 0 else 'unavailable'}")
        except (OSError, subprocess.TimeoutExpired):
            record("Git commit: unavailable")

        port = args.port or 8102
        config_path = ROOT / "config.json"
        if config_path.is_file():
            try:
                panel = json.loads(config_path.read_text(encoding="utf-8-sig")).get("panel", {})
                configured_port = panel.get("port", 8102)
                if args.port is None and type(configured_port) is int and configured_port > 0:
                    port = configured_port
                # Do not dump config, tokens, roots or external MCP settings.
                record(f"Panel enabled: {bool(panel.get('enabled', True))}; "
                       f"open_browser: {bool(panel.get('open_browser', True))}")
            except Exception as exc:
                record(f"Config read failed: {type(exc).__name__} (contents omitted)")
        else:
            record("No local config.json; default port unless overridden")
        if not 1 <= port <= 65535:
            record("RESULT=INVALID_CONFIG_PORT")
            return 1
        record(f"Testing 127.0.0.1:{port}. Stop the normal MCP before this test.")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", port))
                listener.listen(1)
            record("Bind/listen: OK")
        except OSError as exc:
            record(f"RESULT=BIND_FAILED; errno={exc.errno}; "
                   f"winerror={getattr(exc, 'winerror', None)}; {exc}")
            return 1

        failed = False
        for kind in ("basic", "panel"):
            record(f"\n--- {kind} (isolated child process, maximum 45s) ---")
            command = [sys.executable, "-X", "utf8", "-u", str(Path(__file__).resolve()),
                       "--probe", kind, "--port", str(port)]
            try:
                result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, encoding="utf-8",
                                        errors="replace", timeout=45)
                record(result.stdout.rstrip())
                record(f"Child exit code: {result.returncode}")
                failed = failed or result.returncode != 0
            except subprocess.TimeoutExpired as exc:
                output = exc.output or b""
                if isinstance(output, bytes):
                    output = output.decode("utf-8", "replace")
                record(output.rstrip())
                record("RESULT=CHILD_TIMEOUT; child terminated")
                failed = True
        record("\nOVERALL=" + ("FAILED" if failed else "OK"))
        record("If both probes pass: full runtime startup remains untested; "
               "this does not confirm that main.py starts successfully.")
        record(f"Report saved: {report_path}")
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
