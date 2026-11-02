#!/usr/bin/env python3
"""SessionStart hook: hand the plugin's saved myDATA settings to the skill's commands.

Claude Code gives hooks the plugin's userConfig values as CLAUDE_PLUGIN_OPTION_<KEY>, but
not the commands Claude runs with Bash. Lines appended to CLAUDE_ENV_FILE run before each
of those commands, so this turns the saved values into MYDATA_* variables there. A
variable already set in the user's shell wins, and --env-file still overrides both.

Prints nothing: SessionStart output is added to Claude's context, and the key must never
reach it. Always exits 0 so a problem here never blocks the session.
"""
import os
import shlex
import sys

OPTIONS = {
    "MYDATA_USER_ID": "CLAUDE_PLUGIN_OPTION_MYDATA_USER_ID",
    "MYDATA_SUBSCRIPTION_KEY": "CLAUDE_PLUGIN_OPTION_MYDATA_SUBSCRIPTION_KEY",
    "MYDATA_ENV": "CLAUDE_PLUGIN_OPTION_MYDATA_ENV",
}


def export_lines(environ):
    lines = []
    for name, option in OPTIONS.items():
        value = environ.get(option, "").strip()
        if value and not environ.get(name, "").strip():
            lines.append(f"export {name}={shlex.quote(value)}\n")
    return lines


def main(environ=os.environ):
    target = environ.get("CLAUDE_ENV_FILE")
    lines = export_lines(environ)
    if not target or not lines:
        return 0
    try:
        with open(target, "a", encoding="utf-8") as fh:
            fh.writelines(lines)
        os.chmod(target, 0o600)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
