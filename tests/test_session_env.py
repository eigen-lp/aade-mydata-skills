"""Tests for hooks/session_env.py, the SessionStart hook that exports the plugin's settings."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "session_env.py"

spec = importlib.util.spec_from_file_location("session_env", HOOK)
session_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session_env)

KEY = "not-a-real-key with 'quotes' $HOME;rm"


def options(**extra):
    env = {
        "CLAUDE_PLUGIN_OPTION_MYDATA_USER_ID": "fake-user",
        "CLAUDE_PLUGIN_OPTION_MYDATA_SUBSCRIPTION_KEY": KEY,
        "CLAUDE_PLUGIN_OPTION_MYDATA_ENV": "test",
    }
    env.update(extra)
    return env


def test_writes_quoted_exports_that_bash_reads_back(tmp_path):
    target = tmp_path / "env.sh"
    assert session_env.main(options(CLAUDE_ENV_FILE=str(target))) == 0
    out = subprocess.run(
        ["bash", "-c", f'. "{target}"; printf "%s\\n%s\\n%s" "$MYDATA_USER_ID" "$MYDATA_SUBSCRIPTION_KEY" "$MYDATA_ENV"'],
        capture_output=True, text=True, check=True, env={"PATH": os.environ["PATH"]},
    ).stdout
    assert out.split("\n") == ["fake-user", KEY, "test"]
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_shell_variables_win(tmp_path):
    target = tmp_path / "env.sh"
    session_env.main(options(CLAUDE_ENV_FILE=str(target), MYDATA_USER_ID="from-shell"))
    text = target.read_text()
    assert "MYDATA_USER_ID" not in text
    assert "MYDATA_SUBSCRIPTION_KEY" in text


def test_appends_and_skips_unset_options(tmp_path):
    target = tmp_path / "env.sh"
    target.write_text("export OTHER=1\n")
    session_env.main({"CLAUDE_ENV_FILE": str(target), "CLAUDE_PLUGIN_OPTION_MYDATA_USER_ID": "  "})
    assert target.read_text() == "export OTHER=1\n"


def test_no_env_file_is_a_no_op():
    assert session_env.main(options()) == 0


def test_prints_nothing_and_exits_zero(tmp_path):
    env = {"PATH": os.environ["PATH"], **options(CLAUDE_ENV_FILE=str(tmp_path / "missing" / "env.sh"))}
    run = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, env=env)
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")


def test_manifest_options_match_the_hook():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    names = {f"CLAUDE_PLUGIN_OPTION_{k.upper()}" for k in manifest["userConfig"]}
    assert names == set(session_env.OPTIONS.values())
    assert manifest["userConfig"]["mydata_subscription_key"]["sensitive"] is True
