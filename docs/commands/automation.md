# JAPP Automation Commands

Run commands from the repository root. Install the runtime and test dependencies first:

```sh
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
```

Copy `automation/config.example.json` to the ignored `automation/config.json` to customize paths, poll intervals, provider selection, and pauses. Do not put passwords or access tokens in that file. Outlook client ID and Google OAuth client settings are read from environment variables when explicitly used.

Set `paths.master_cv` in the local config to the actual master CV DOCX. Until it exists, generated input packages remain `awaiting_user_inputs`; JAPP will not fabricate or substitute a CV.

## Read-only checks

```sh
.venv/bin/python main.py doctor
.venv/bin/python main.py sync --dry-run
.venv/bin/python main.py run --approved-only --dry-run
```

`sync --dry-run` reads local research JSON and the workbook but writes no workbook, sidecar, or backup. `run --dry-run` prints eligible company IDs and writes no database, output, or browser state.

## Research synchronization

```sh
.venv/bin/python main.py sync
.venv/bin/python main.py sync --pull-github
```

The optional pull fetches only `research/companies/*.json` from the configured remote/branch. It stops if those paths have local changes and never deletes local records. GitHub pulling is disabled by default; `--pull-github` cannot be combined with `--dry-run` because it updates local research files.

Sync matches `company_id` and normalized company name, using the workbook name when present and the local company registry when the workbook name is blank. Name conflicts are skipped. It fills empty cells or cells whose current value still matches the hash in the local sync manifest; formulas and manual edits are preserved. New non-empty ideas receive `Pending Review`, never `Approved` or `Start`. Before a write, JAPP creates a timestamped backup and uses a temporary workbook replacement.

## Master engine

```sh
.venv/bin/python main.py start
.venv/bin/python main.py stop
.venv/bin/python main.py status
```

`start` performs startup sync and supervises research sync, approval queue, attended document handoff, and Apollo CSV import monitors. `stop` creates the local `STOP` control file; the engine exits after its current step. A process lock prevents duplicate engine instances. Service failures are logged separately and do not stop other scheduled services.

Excel is the approval interface. A company enters the queue only when the same `03 Execution` row has `Status = Approved` and `CV Tailoring = Start`. JAPP re-reads approval before each company. Revoked pending work is cancelled. Execution is sequential and capped at 15 companies per day by default.

## Documents and AI handoff

```sh
.venv/bin/python main.py run --approved-only
.venv/bin/python main.py run --approved-only --limit 3
.venv/bin/python main.py run --approved-only --only 2
```

JAPP creates `data/generated/<ID>_<Company>/input_package.json`. Fill in your own content in `prompts/cv_tailoring_prompt.md` and `prompts/proposal_prompt.md`; JAPP does not generate or fabricate prompt content. ChatGPT Plus remains an attended workflow: JAPP does not script consumer ChatGPT submission or extract responses. Save the returned DOCX files and `introduction.md` into the generated company folder. DOCX validation checks file readability, company name, and optional role before advancing.

## Contacts

Export an Apollo CSV through the authorised UI and import it locally:

```sh
.venv/bin/python main.py contacts import-csv automation/imports/apollo/contacts.csv
.venv/bin/python main.py contacts suppress 12
.venv/bin/python main.py contacts restore 12
```

The importer validates company identity against the local registry, ranks titles deterministically, skips duplicates, and fills only empty slots in `04 Connections`. Unknown/unverified email addresses are not eligible for email. Apollo API access is not implemented; the adapter is intentionally disabled. `apollo.reveal_emails` defaults to `false`.

## Email batches

```sh
.venv/bin/python main.py batch create
.venv/bin/python main.py batch draft <batch-id>
.venv/bin/python main.py batch approve <batch-id>
.venv/bin/python main.py batch cancel <batch-id>
.venv/bin/python main.py batch send <batch-id> --send
```

A batch snapshot contains recipients, content, and attachment hashes. Any changed attachment requires a new batch and approval. Sending is blocked unless the batch is approved, `mail.send_enabled` is `true`, and `--send` is explicitly supplied. The default provider is mock and the default daily send limit is 20. The state database prevents a second message intent for the same contact; uncertain provider responses are not retried automatically. Suppress a contact before further processing if a reply or bounce requires it.

Cancel an unsent batch before changing an approved draft. JAPP deletes the corresponding unsent provider drafts, marks the batch cancelled, and permits a new snapshot. Batches with a sent, in-flight, or uncertain message cannot be cancelled.

Outlook uses Microsoft Graph delegated device-code auth; Gmail uses its REST provider and configured OAuth client environment variables. OAuth access tokens are held in memory, not written to the repo, and a long-running process may request sign-in again after restart. Provider/API calls and real sending were not exercised during development. Configure a supported OAuth application and consent to the required scopes before selecting a live provider. Consumer ChatGPT and Apollo UI automation are intentionally not supported.

## Local data and troubleshooting

`automation/config.json`, `data/state/`, `data/generated/`, `data/contacts/`, `outputs/`, `logs/`, `backups/`, the SQLite database, `.japp-browser-profile/`, and `STOP` are gitignored. Personal CVs, documents, contact records, tokens, logs, and tracker workbooks must not be committed.

- If sync reports a company-name mismatch, verify the ID/name pair in `data/reference/JAPP_company_registry.csv` and `01 Companies`; JAPP skips conflicts rather than guessing.
- If the workbook is open or locked, close it and rerun sync. A backup is created before non-dry-run workbook writes.
- If an AI handoff is pending, fill the prompt placeholders and save the generated files into the company output folder.
- If a provider UI or API is unavailable, the company remains at its last completed state; no task is marked complete without a result.
- Run `.venv/bin/python -m pytest -q` to execute the mock-only test suite.
