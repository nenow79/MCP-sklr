#!/usr/bin/env python3
"""Uruchamianie skryptów skills: bez shella, oczyszczone środowisko, timeout z ubijaniem drzewa procesów."""
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

DEFAULT_TIMEOUT = 60
MIN_TIMEOUT = 1
MAX_TIMEOUT = 300           # tymczasowo; do ustalenia po pomiarze limitu harnessa
APPROVAL_TIMEOUT = 120      # czekanie na decyzję użytkownika (nie wlicza się do timeoutu skryptu)
STREAM_LIMIT = 6000
SCRIPTS_DIR = "scripts"
KILL_GRACE_SECONDS = 5
CANCEL_POLL_SECONDS = 0.5

INTERPRETERS = {".py": None, ".js": "node", ".sh": "bash", ".ps1": "pwsh"}  # None = interpreter przekazany z zewnątrz
POWERSHELL_CANDIDATES = ("pwsh", "powershell")  # preferowany PowerShell 7, fallback Windows PowerShell 5.1
POWERSHELL_ARGS = ("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File")

_ENV_ALLOWLIST = {
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "USERPROFILE", "HOME", "USERNAME", "USER",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "PROGRAMW6432", "COMMONPROGRAMFILES", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "OS", "LANG", "LC_ALL",
}


def validate_timeout(timeout: Any) -> Optional[str]:
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
        return f"timeout musi być liczbą całkowitą sekund od {MIN_TIMEOUT} do {MAX_TIMEOUT}."
    return None


def resolve_script(base_dir: Path, script: str) -> Path:
    """Zamienia ścieżkę względną od katalogu skilla na plik z jego scripts/.

    ValueError: pusta ścieżka, absolutna, wyjście poza scripts/ (.., symlink).
    FileNotFoundError: plik nie istnieje.
    """
    if not script or not script.strip():
        raise ValueError("Pusta ścieżka skryptu.")
    candidate = Path(script)
    if candidate.anchor:
        raise ValueError("Ścieżka skryptu musi być względna względem katalogu skilla (np. scripts/run.py).")
    base = base_dir.resolve()
    scripts_dir = (base / SCRIPTS_DIR).resolve()
    target = (base / candidate).resolve()
    if not target.is_relative_to(scripts_dir):
        raise ValueError(f"Skrypt musi leżeć w katalogu {SCRIPTS_DIR}/ skilla (Path Traversal zablokowany).")
    if not target.is_file():
        raise FileNotFoundError(f"Nie znaleziono skryptu: {script}")
    return target


def build_command(script_path: Path, args: List[str], python_exe: str) -> List[str]:
    """Buduje listę argumentów (bez shella) wg rozszerzenia skryptu."""
    suffix = script_path.suffix.lower()
    if suffix not in INTERPRETERS:
        raise ValueError(
            f"Nieobsługiwane rozszerzenie '{suffix}'. Dozwolone: {sorted(INTERPRETERS)}."
        )
    if suffix == ".ps1":
        executable = next((found for name in POWERSHELL_CANDIDATES if (found := shutil.which(name))), None)
        if not executable:
            raise FileNotFoundError(
                f"Nie znaleziono interpretera PowerShell ({' / '.join(POWERSHELL_CANDIDATES)}) w PATH."
            )
        return [executable, *POWERSHELL_ARGS, str(script_path), *args]
    interpreter = INTERPRETERS[suffix]
    if interpreter is None:
        executable = python_exe
    else:
        executable = shutil.which(interpreter)
        if not executable:
            raise FileNotFoundError(f"Nie znaleziono interpretera '{interpreter}' w PATH.")
    return [executable, str(script_path), *args]


# Opt-in only; proxy URLs may contain credentials. Never log these values.
_NETWORK_ENV_ALLOWLIST = frozenset({
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR",
})


def validate_network_env(value: Any) -> Optional[str]:
    if not isinstance(value, str) or value not in ("", "proxy-ca"):
        # Invalid input itself might contain a secret: do not echo it.
        return 'metadata.network-env musi być pominięte, puste albo równe "proxy-ca".'
    return None


def clean_env(environ: Optional[Mapping[str, str]] = None, *,
              network_env: str = "") -> Dict[str, str]:
    """Baseline unchanged; proxy/CA only with explicit opt-in.

    Values must not be serialized. An opted-in script can read proxy credentials.
    This is not a sandbox or a generic stdout/stderr redaction mechanism.
    """
    error = validate_network_env(network_env)
    if error:
        raise ValueError(error)
    source = os.environ if environ is None else environ
    env = {key: value for key, value in source.items() if key.upper() in _ENV_ALLOWLIST}
    if network_env == "proxy-ca":
        env.update({key: value for key, value in source.items()
                    if key.upper() in _NETWORK_ENV_ALLOWLIST})
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def truncate_stream(text: str, label: str, limit: int = STREAM_LIMIT) -> Tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    half = (limit - 80) // 2
    shortened = (
        text[:half]
        + f"\n\n[... {label} skrócony: pokazano {limit} z {len(text)} znaków ...]\n\n"
        + text[-half:]
    )
    return shortened, True


def _kill_process_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, timeout=KILL_GRACE_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def run_command(
    command: List[str],
    cwd: Path,
    timeout: int,
    env: Optional[Dict[str, str]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """Uruchamia komendę bez shella. Po timeoucie lub anulowaniu (cancel_event)
    ubija drzewo procesów i zwraca częściowy output."""
    if cancel_event is not None and cancel_event.is_set():
        return {"started": False, "cancelled": True, "error": "Anulowano przed startem."}

    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)

    started = time.monotonic()
    from autopilot.context import recheck_current
    recheck_current()
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
    except OSError as exc:
        return {"started": False, "error": f"Nie udało się uruchomić procesu: {exc}"}

    timed_out = False
    cancelled = False
    deadline = started + timeout
    stdout = stderr = ""
    while True:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
            else:
                try:
                    stdout, stderr = process.communicate(
                        timeout=min(CANCEL_POLL_SECONDS, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        break

    stdout_out, stdout_truncated = truncate_stream((stdout or "").strip(), "stdout")
    stderr_out, stderr_truncated = truncate_stream((stderr or "").strip(), "stderr")
    return {
        "started": True,
        "exit_code": process.returncode,
        "stdout": stdout_out,
        "stderr": stderr_out,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "duration_s": round(time.monotonic() - started, 2),
        "timed_out": timed_out,
        "cancelled": cancelled,
    }
