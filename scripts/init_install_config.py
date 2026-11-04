#!/usr/bin/env python3
"""Initialize a fresh Windows install without importing the application runtime.

Existing config is checked, never rewritten. No databases, roots or trust are
created. Run explicitly using the installation's local venv interpreter.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def read_config(path: Path, *, example: bool = False) -> dict:
    # Runtime uses utf-8 (without BOM support); reject BOM in an existing config.
    encoding = "utf-8-sig" if example else "utf-8"
    with path.open("r", encoding=encoding) as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object.")
    section = data.get("work_sessions", {"enabled": False})
    if (type(section) is not dict or set(section) - {"enabled"}
            or type(section.get("enabled", False)) is not bool):
        raise ValueError("Invalid work_sessions configuration.")
    return data


def main() -> int:
    target = ROOT / "config.json"
    created = False
    try:
        if target.exists():
            data = read_config(target)
            print("[OK] Existing config.json preserved; no settings changed.")
        else:
            data = read_config(ROOT / "config.example.json", example=True)
            data["server"]["host"] = "127.0.0.1"
            data["panel"]["enabled"] = True
            data["panel"]["open_browser"] = True
            data["work_sessions"] = {"enabled": True}
            data["autopilot"] = {"enabled": False}
            data["roots"] = []
            # Complete serialization before opening the destination. Exclusive
            # creation prevents overwriting a config created concurrently.
            content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                created = True
                stream.write(content)
            print("[OK] Created config.json from config.example.json (UTF-8, no BOM).")
            print("MCP listens on localhost; sessions enabled; autopilot disabled; no folders.")
        if not data.get("work_sessions", {}).get("enabled", False):
            print("[NOTE] Sessions are disabled in the preserved config.")
            print('To use Start, set work_sessions.enabled to true and restart sklr.')
        print("Add your own existing project folder in the panel before Start.")
        print("This check is not full application/configuration validation.")
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        if created:
            try:
                target.unlink()
            except OSError:
                print("[ERROR] Could not remove incomplete new config.json; inspect it manually.")
        # Do not print file contents or exception text that may include secrets.
        print("[ERROR] Could not read/check/create config.json; existing config was not overwritten.")
        print("Check file permissions and valid JSON (object, UTF-8 without BOM).")
        print("Check config.example.json too. No application was started.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
