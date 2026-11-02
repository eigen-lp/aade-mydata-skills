# Privacy policy: aade-mydata plugin

Last updated: 2 November 2026

This policy covers the **aade-mydata** plugin and Agent Skill published by EIGEN LAB Ε.Ε. ("Eigen", "we") at <https://github.com/eigen-lp/aade-mydata-skills>. It does not cover Claude, Claude Code or Cowork (see [Anthropic's privacy policy](https://www.anthropic.com/legal/privacy)) or AADE's myDATA service (see [aade.gr](https://www.aade.gr)).

## Eigen receives nothing

The plugin runs entirely on your machine, or in the Cowork sandbox you start. It has no server of ours, no analytics, no telemetry and no tracking. Eigen never receives your credentials, your myDATA data or anything about how you use the plugin.

## What the plugin handles, and where it goes

| Data | Where it comes from | Where it goes |
|---|---|---|
| Your myDATA username and subscription key | The plugin's settings (the key is kept in your system's secure credential store), your environment variables, or an `--env-file` you name | Only to AADE's myDATA REST API, as the `aade-user-id` and `Ocp-Apim-Subscription-Key` headers: `mydataapidev.aade.gr` (test) or `mydatapi.aade.gr` (production). Never printed, never written to output files |
| Your myDATA books (invoices, counterparties' AFMs, amounts, classifications) | Downloaded from AADE when you ask | Shown to you in your Claude session, and saved only to files you name with `--out` (readable only by you) |
| Expense classifications you approve | Proposed by Claude, approved by you | Sent only to AADE's myDATA API, and only after you approve the exact dry-run table |
| ERP, accounting or bank CSV files | Files you give it to reconcile | Read locally; not sent anywhere by the plugin |

The plugin contacts no host other than the two AADE addresses above.

Whatever the plugin shows in your Claude session is processed by Anthropic as part of that conversation, under your agreement with Anthropic. The skill tells Claude to show summaries rather than full dumps of your books unless you ask.

## Retention

The plugin keeps nothing between sessions apart from the settings you saved in Claude Code and any files you chose to write. Delete them whenever you like: remove the files, and clear the settings with `/plugin configure aade-mydata@eigen-lp` or by uninstalling the plugin.

## Children

The plugin is a business accounting tool and is not intended for anyone under 18.

## Contact

EIGEN LAB Ε.Ε., Athens, Greece. Αρ. ΓΕΜΗ 197022703000. Email: [hello@eigen.gr](mailto:hello@eigen.gr). For security issues, see [SECURITY.md](SECURITY.md).
