# Setup

## 1. Get credentials

myDATA authenticates every call with two headers: `aade-user-id` (a username you choose) and `Ocp-Apim-Subscription-Key` (the "Κωδικός API" AADE gives you). Test and production are **separate registrations** with different keys.

**Test environment (start here).** Register at <https://mydata-dev-register.azurewebsites.net>. You get a username and subscription key for `https://mydataapidev.aade.gr/`. Test data is not real and is not visible in production.

**Production.** Log in with TAXISnet at <https://www1.aade.gr/saadeapps2/bookkeeper-web>, choose «Φόρμα εγγραφής στο myDATA REST API» → «Νέα εγγραφή χρήστη», enter a username (Latin letters and digits only) and «Προσθήκη». The key is shown in the «Κωδικός API» column. Source: official ERP technical description v2.0.2, section 4.1.1.

The API user acts for the AFM it was registered under. An accountant registered under their own AFM passes `--entity <client AFM>` (the `entityVatNumber` parameter) to act for a client they are authorised for.

## 2. Save them in the plugin's settings, not in the chat

Never paste keys into Claude. With the plugin installed in Claude Code, enter them when it asks for its settings: **myDATA username**, **myDATA subscription key** and **Environment** (`test` or `prod`). The key is masked and kept in your system's secure credential store (the macOS Keychain, for example), not in `settings.json`. Change them later in `/plugin` → aade-mydata → configure, or with `claude plugin configure aade-mydata@eigen-lp`. Start a new session after changing them.

A small SessionStart hook passes the saved values to the skill's commands as `MYDATA_USER_ID`, `MYDATA_SUBSCRIPTION_KEY` and `MYDATA_ENV`. A variable you already set in your shell takes precedence, and `--env-file` overrides both. The plugin settings hold one set of credentials; keep the other environment's in an `--env-file`.

**Alternatively**, set them in your shell, for example in `~/.zshrc` or `~/.bashrc`:

```bash
export MYDATA_USER_ID="your-username"
export MYDATA_SUBSCRIPTION_KEY="your-subscription-key"
export MYDATA_ENV="test"   # optional; "test" is the default
```

Then open a new terminal (or `source ~/.zshrc`) and start Claude Code from it.

Or keep them in a git-ignored file per environment and pass it to every command with `--env-file` (this is what the skill does, so commands stay single, pre-approved calls):

```bash
# .env.prod  (chmod 600, never committed)
MYDATA_ENV=prod
MYDATA_USER_ID=your-username
MYDATA_SUBSCRIPTION_KEY=your-subscription-key
```

`--env-file` only reads `MYDATA_USER_ID`, `MYDATA_SUBSCRIPTION_KEY` and `MYDATA_ENV`; other lines are ignored. Keep production keys out of shared machines and repos; if a key leaks, delete it in the portal and create a new one.

## 3. Check

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py check
```

This prints the environment, the base URL and whether each variable is set (never the values), then makes one read-only call (`RequestMyIncome` for today). Exit codes: `3` variables missing, `4` credentials rejected (HTTP 401/403), `5` other HTTP error.

## Using it in Cowork (or chat)

The plugin works in Claude Code, Cowork and claude.ai chat: a plugin you add under **Customize > Plugins** is saved to your account and loads in all three. Claude Code is where it's tested most; check these when you use Cowork:

1. **Install.** In the Claude desktop app open **Customize > Plugins**, then either **Add > Add marketplace** with `eigen-lp/aade-mydata-skills`, or **Add > Upload plugin** with the release zip (`tools/package_plugin.py` builds it).
2. **Network.** Cowork runs commands in a sandbox that reaches only allowed domains. Allow outbound access to `mydatapi.aade.gr` (production) and `mydataapidev.aade.gr` (test): in the desktop app's settings turn on code execution and network egress, then add both domains to the allowed list (or allow all domains). On Team/Enterprise plans an admin manages this list. Settings apply to tasks started after the change.
3. **Credentials.** Your shell's environment variables don't reach Cowork. If the plugin's settings don't reach the commands there either (`check` shows NOT SET), put a `.env.prod` / `.env.dev` file (format above) in a folder you share with the Cowork task and tell Claude its path; the skill passes it with `--env-file`. Each task starts on a clean machine, so nothing is stored between tasks. Don't paste keys into the chat.
4. **Confirmation.** Cowork has its own approval model, so you may not get the same per-command prompt as in Claude Code. The safeguards that don't depend on the app still apply: Claude must show you the dry-run table and get your yes, `send` refuses without the matching confirmation code, and production needs `--yes-production`.

Claude.ai chat additionally needs code execution with network access to the same domains, and has no folder to share, so Cowork or Claude Code are the better fit.

## Environments

| `--env` | Base URL | Source |
|---|---|---|
| `test` (default) | `https://mydataapidev.aade.gr/` | AADE "test URLs" list (aade.gr/sites/default/files/2022-12/test_urls_0.pdf) |
| `prod` | `https://mydatapi.aade.gr/myDATA/` | ERP technical description v2.0.2, section 4.2 |

`--env` overrides `MYDATA_ENV` (including one from `--env-file`). Writing to production also needs `--yes-production` on `send`.
