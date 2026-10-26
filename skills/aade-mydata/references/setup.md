# Setup

## 1. Get credentials

myDATA authenticates every call with two headers: `aade-user-id` (a username you choose) and `Ocp-Apim-Subscription-Key` (the "Κωδικός API" AADE gives you). Test and production are **separate registrations** with different keys.

**Test environment (start here).** Register at <https://mydata-dev-register.azurewebsites.net>. You get a username and subscription key for `https://mydataapidev.aade.gr/`. Test data is not real and is not visible in production.

**Production.** Log in with TAXISnet at <https://www1.aade.gr/saadeapps2/bookkeeper-web>, choose «Φόρμα εγγραφής στο myDATA REST API» → «Νέα εγγραφή χρήστη», enter a username (Latin letters and digits only) and «Προσθήκη». The key is shown in the «Κωδικός API» column. Source: official ERP technical description v2.0.2, section 4.1.1.

The API user acts for the AFM it was registered under. An accountant registered under their own AFM passes `--entity <client AFM>` (the `entityVatNumber` parameter) to act for a client they are authorised for.

## 2. Put them in the environment, not in the chat

Never paste keys into Claude. Set them in your shell, for example in `~/.zshrc` or `~/.bashrc`:

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

## Environments

| `--env` | Base URL | Source |
|---|---|---|
| `test` (default) | `https://mydataapidev.aade.gr/` | AADE "test URLs" list (aade.gr/sites/default/files/2022-12/test_urls_0.pdf) |
| `prod` | `https://mydatapi.aade.gr/myDATA/` | ERP technical description v2.0.2, section 4.2 |

`--env` overrides `MYDATA_ENV` (including one from `--env-file`). Writing to production also needs `--yes-production` on `send`.
