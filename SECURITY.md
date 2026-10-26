# Security

## Reporting a vulnerability

Please email **hello@eigen.gr** with "aade-mydata-skills security" in the subject, or use GitHub's private vulnerability reporting on this repository. Don't open a public issue for security problems, and never include real myDATA credentials or taxpayer data in a report. We aim to reply within 5 working days.

## How the skill handles credentials and data

- Credentials are read only from the environment variables `MYDATA_USER_ID` and `MYDATA_SUBSCRIPTION_KEY`. The script never prints, logs or writes them, and removes them from any error text the API echoes back.
- The skill tells Claude never to ask for keys in the chat. If a key was pasted into a conversation, delete it in the AADE portal and create a new one.
- All calls go over HTTPS to `mydataapidev.aade.gr` (test) or `mydatapi.aade.gr` (production), and nowhere else.
- Read commands only use GET. The only write is `SendExpensesClassification`, which is a dry run unless `--send` is given; production also needs `--yes-production`.
- XML from the API is parsed with Python's standard library. Documents containing a `DOCTYPE` or `ENTITY` declaration are refused, which blocks entity-expansion and external-entity attacks.
- Output (invoices, AFMs, amounts) is printed to your terminal or files you choose. Treat saved JSON/CSV as confidential business data and keep it out of git (`.gitignore` covers `data/` and `*.mydata.json`).

## Supported versions

Only the latest release on `main` receives fixes.
