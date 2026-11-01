"""Local directory dialog, isolated from the panel event loop and permissions."""
import json
from pathlib import Path
import subprocess
import sys
import threading


class DirectoryPickerBusy(Exception):
    pass


class DirectoryPickerUnavailable(Exception):
    pass


class DirectoryPicker:
    def __init__(self):
        self._lock = threading.Lock()

    def pick(self):
        if not self._lock.acquire(blocking=False):
            raise DirectoryPickerBusy()
        try:
            try:
                result = subprocess.run(
                    [sys.executable, "-I", str(Path(__file__).with_name("directory_dialog.py"))],
                    capture_output=True, encoding="utf-8", timeout=300, check=False,
                )
                if result.returncode != 0:
                    raise DirectoryPickerUnavailable()
                data = json.loads(result.stdout)
                if not isinstance(data, dict) or set(data) != {"path"}:
                    raise DirectoryPickerUnavailable()
                selected = data["path"]
                if selected is None:
                    return None
                if (not isinstance(selected, str) or not selected
                        or not Path(selected).is_absolute() or not Path(selected).is_dir()):
                    raise DirectoryPickerUnavailable()
                return str(Path(selected))
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                raise DirectoryPickerUnavailable() from exc
        finally:
            self._lock.release()


DIRECTORY_PICKER = DirectoryPicker()
