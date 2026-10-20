import os
import sys
import threading
import time

import pytest

from skills_engine import runner
from skills_engine.runner import (
    build_command,
    clean_env,
    resolve_script,
    run_command,
    truncate_stream,
    validate_timeout,
)


def _skill_dir(tmp_path):
    base = tmp_path / "demo"
    (base / "scripts").mkdir(parents=True)
    (base / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n", encoding="utf-8")
    return base


def _script(base, name, code):
    path = base / "scripts" / name
    path.write_text(code, encoding="utf-8")
    return path


# --- timeout ---
@pytest.mark.parametrize("value", [1, 60, 300])
def test_validate_timeout_ok(value):
    assert validate_timeout(value) is None


@pytest.mark.parametrize("value", [0, -1, 301, 10**6, True, "60", 1.5, None])
def test_validate_timeout_bad(value):
    assert validate_timeout(value) is not None


# --- resolve_script ---
def test_resolve_script_ok(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "run.py", "print(1)")
    assert resolve_script(base, "scripts/run.py") == script.resolve()


@pytest.mark.parametrize("bad", ["../SKILL.md", "scripts/../SKILL.md", "SKILL.md", "references/x.py"])
def test_resolve_script_must_be_in_scripts(tmp_path, bad):
    base = _skill_dir(tmp_path)
    (base / "references").mkdir()
    (base / "references" / "x.py").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_script(base, bad)


def test_resolve_script_absolute_blocked(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "run.py", "print(1)")
    with pytest.raises(ValueError):
        resolve_script(base, str(script))


def test_resolve_script_empty_and_missing(tmp_path):
    base = _skill_dir(tmp_path)
    with pytest.raises(ValueError):
        resolve_script(base, " ")
    with pytest.raises(FileNotFoundError):
        resolve_script(base, "scripts/nope.py")


def test_resolve_script_symlink_escape_blocked(tmp_path):
    base = _skill_dir(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("print('x')", encoding="utf-8")
    link = base / "scripts" / "link.py"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("Brak uprawnień do tworzenia symlinków")
    with pytest.raises(ValueError):
        resolve_script(base, "scripts/link.py")


# --- build_command ---
def test_build_command_python(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "run.py", "")
    command = build_command(script, ["--a", "b c"], "PYEXE")
    assert command == ["PYEXE", str(script), "--a", "b c"]


def test_build_command_unsupported_extension(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "run.exe", "")
    with pytest.raises(ValueError):
        build_command(script, [], "PYEXE")


def test_build_command_missing_interpreter(tmp_path, monkeypatch):
    base = _skill_dir(tmp_path)
    script = _script(base, "run.js", "")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    with pytest.raises(FileNotFoundError):
        build_command(script, [], "PYEXE")


def test_build_command_uses_which_for_node(tmp_path, monkeypatch):
    base = _skill_dir(tmp_path)
    script = _script(base, "run.js", "")
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    assert build_command(script, ["x"], "PYEXE")[0] == "/bin/node"


def test_build_command_powershell_prefers_pwsh(tmp_path, monkeypatch):
    base = _skill_dir(tmp_path)
    script = _script(base, "run.ps1", "")
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    command = build_command(script, ["-A", "b c"], "PYEXE")
    assert command == [
        "/bin/pwsh", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
        str(script), "-A", "b c",
    ]


def test_build_command_powershell_fallback_to_windows_powershell(tmp_path, monkeypatch):
    base = _skill_dir(tmp_path)
    script = _script(base, "run.ps1", "")
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/bin/powershell" if name == "powershell" else None)
    assert build_command(script, [], "PYEXE")[0] == "/bin/powershell"


def test_build_command_powershell_missing(tmp_path, monkeypatch):
    base = _skill_dir(tmp_path)
    script = _script(base, "run.ps1", "")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    with pytest.raises(FileNotFoundError):
        build_command(script, [], "PYEXE")


# --- clean_env ---
def test_clean_env_drops_secrets():
    env = clean_env({"PATH": "p", "SYSTEMROOT": "s", "API_TOKEN": "secret", "BEARER": "x", "path": "lower"})
    assert "API_TOKEN" not in env
    assert "BEARER" not in env
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["SYSTEMROOT"] == "s"


# --- explicit network opt-in (synthetic values only) ---
@pytest.mark.parametrize("mode", ["", "proxy-ca"])
def test_clean_env_network_scope(mode):
    network = {key: "synthetic" for key in runner._NETWORK_ENV_ALLOWLIST}
    source = dict(network, PATH="p", SYSTEMROOT="s", API_TOKEN="secret",
                  DATABRICKS_TOKEN="secret", DATABRICKS_SERVER_HOSTNAME="secret",
                  DATABRICKS_HTTP_PATH="secret", CONDA_PREFIX="unused",
                  PYTHONPATH="unused", PYTHONHOME="unused")
    before = dict(source)
    expected = {"PATH": "p", "SYSTEMROOT": "s",
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    if mode:
        expected.update(network)
    assert clean_env(source, network_env=mode) == expected
    assert source == before
    assert clean_env(source) == clean_env(source, network_env="")


def test_clean_env_opt_in_does_not_change_baseline():
    source = {key: "baseline" for key in runner._ENV_ALLOWLIST}
    source.update({key: "network" for key in runner._NETWORK_ENV_ALLOWLIST})
    expected = {key: "baseline" for key in runner._ENV_ALLOWLIST}
    expected.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    assert clean_env(source) == expected
    assert clean_env(source, network_env="proxy-ca") == dict(
        expected, **{key: "network" for key in runner._NETWORK_ENV_ALLOWLIST})


def test_clean_env_network_case_and_missing():
    assert clean_env({}, network_env="proxy-ca") == clean_env({})
    env = clean_env({"https_proxy": "synthetic", "Ssl_Cert_File": "synthetic",
                     "api_token": "secret"}, network_env="proxy-ca")
    assert env["https_proxy"] == env["Ssl_Cert_File"] == "synthetic"
    assert "api_token" not in env
    assert "https_proxy" not in clean_env({"https_proxy": "synthetic"})


@pytest.mark.parametrize("mode", [None, True, 1, [], {}, "all", "PROXY-CA", " proxy-ca", "SECRET"])
def test_clean_env_invalid_network_mode_fails_closed(mode):
    with pytest.raises(ValueError) as caught:
        clean_env({"HTTPS_PROXY": "SECRET_PROXY"}, network_env=mode)
    assert "SECRET" not in str(caught.value)


# --- truncate ---
def test_truncate_stream():
    short, cut = truncate_stream("abc", "stdout")
    assert (short, cut) == ("abc", False)
    long, cut = truncate_stream("x" * 20000, "stdout")
    assert cut is True
    assert len(long) < 7000
    assert "skrócony" in long


# --- run_command (prawdziwe procesy) ---
def test_run_command_success(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "ok.py", "import sys; print('out'); print('err', file=sys.stderr)")
    result = run_command([sys.executable, str(script)], base, 20, clean_env())
    assert result["started"] is True
    assert result["exit_code"] == 0
    assert result["stdout"] == "out"
    assert result["stderr"] == "err"
    assert result["timed_out"] is False


def test_run_command_nonzero_exit(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "fail.py", "import sys; sys.exit(3)")
    result = run_command([sys.executable, str(script)], base, 20, clean_env())
    assert result["exit_code"] == 3


def test_run_command_utf8_output(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "pl.py", "print('zażółć gęślą jaźń')")
    result = run_command([sys.executable, str(script)], base, 20, clean_env())
    assert result["stdout"] == "zażółć gęślą jaźń"


def test_run_command_cwd_is_skill_dir(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "cwd.py", "import os; print(os.getcwd())")
    result = run_command([sys.executable, str(script)], base, 20, clean_env())
    assert os.path.samefile(result["stdout"], base)


def test_run_command_stdin_is_closed(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "stdin.py", "import sys; print(repr(sys.stdin.read()))")
    result = run_command([sys.executable, str(script)], base, 20, clean_env())
    assert result["exit_code"] == 0
    assert result["stdout"] == "''"


def test_run_command_does_not_leak_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBECODER_SECRET_TOKEN", "topsecret")
    base = _skill_dir(tmp_path)
    script = _script(base, "env.py", "import os; print(os.environ.get('VIBECODER_SECRET_TOKEN', 'absent'))")
    result = run_command([sys.executable, str(script)], base, 20, clean_env())
    assert result["stdout"] == "absent"


def test_run_command_timeout_kills_and_returns_partial(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(
        base, "slow.py",
        "import sys, time\nprint('partial', flush=True)\ntime.sleep(30)\n",
    )
    started = time.monotonic()
    result = run_command([sys.executable, str(script)], base, 2, clean_env())
    elapsed = time.monotonic() - started
    assert result["timed_out"] is True
    assert elapsed < 15
    assert "partial" in result["stdout"]


def test_run_command_missing_executable(tmp_path):
    result = run_command([str(tmp_path / "no-such-binary")], tmp_path, 5, clean_env())
    assert result["started"] is False
    assert "error" in result


def test_run_command_truncates_big_output(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "big.py", "print('x' * 50000)")
    result = run_command([sys.executable, str(script)], base, 20, clean_env())
    assert result["stdout_truncated"] is True
    assert len(result["stdout"]) < 7000


# --- cancel_event ---
def test_run_command_cancel_event_not_set(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "ok.py", "print('out')")
    result = run_command([sys.executable, str(script)], base, 20, clean_env(), threading.Event())
    assert result["exit_code"] == 0
    assert result["stdout"] == "out"
    assert result["cancelled"] is False
    assert result["timed_out"] is False


def test_run_command_cancel_running_process(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(
        base, "slow.py",
        "import time\nprint('partial', flush=True)\ntime.sleep(30)\n",
    )
    event = threading.Event()
    timer = threading.Timer(1.0, event.set)
    timer.start()
    started = time.monotonic()
    try:
        result = run_command([sys.executable, str(script)], base, 60, clean_env(), event)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 15
    assert result["started"] is True
    assert result["cancelled"] is True
    assert result["timed_out"] is False
    assert "partial" in result["stdout"]


def test_run_command_cancel_before_start(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "ok.py", "print('out')")
    event = threading.Event()
    event.set()
    result = run_command([sys.executable, str(script)], base, 20, clean_env(), event)
    assert result["started"] is False
    assert result["cancelled"] is True


def test_run_command_timeout_with_unset_cancel_event(tmp_path):
    base = _skill_dir(tmp_path)
    script = _script(base, "slow.py", "import time\ntime.sleep(30)\n")
    started = time.monotonic()
    result = run_command([sys.executable, str(script)], base, 2, clean_env(), threading.Event())
    assert time.monotonic() - started < 15
    assert result["timed_out"] is True
    assert result["cancelled"] is False
