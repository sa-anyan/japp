# Private GitHub workflow

Continue using the existing engine and approvals. The new `exchange` commands are explicit
terminal stages: they do not run ChatGPT, submit its messages or extract its responses.
You direct ChatGPT to read/commit the private repository between stages. The earlier
attended browser helper is preserved; this repository workflow does not require it.

## Local setup

Run from your JAPP source checkout with the existing virtual environment:

```sh
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
gh auth status
```

`gh` (GitHub CLI) and `git` must be on PATH. Your GitHub login needs access to both
`sa-anyan/japp` (code) and `sa-anyan/japp_data` (private data), and Git pushes must work.
Give the ChatGPT workflow access to `japp_data` through an integration that can actually
read files and commit changes. A chat with read-only GitHub access cannot complete the
write-back stages. JAPP cannot grant that integration access or make its commits.

Set `paths.master_cv` in the ignored `automation/config.json` to your master DOCX.
The data checkout defaults to `data/private_repo`, which is ignored by the code repo.
JAPP checks GitHub privacy and the checkout's origin before publishing. It refuses public
repos, dirty checkouts, unexpected staged files and non-fast-forward changes. It never
force-pushes. It commits only the explicit files produced by the current stage.
If a push fails, resolve the checkout's Git conflict/connectivity and rerun the stage;
an already committed unpublished change is pushed on the next run.

## Excel Start control

```sh
.venv/bin/python main.py dashboard-copy --output data/local/JAPP_Workflow_Dashboard.xlsx --activate
```

This makes a separate workbook and installs a visible Start/Done/Not required dropdown
in `03 Execution` → `CV Tailoring`. It preserves existing values and other tabs.
It is a cell selector, not a VBA macro button; an `.xlsx` file cannot run Python on its own.
`--activate` selects the new workbook in your ignored local config.
The command refuses to overwrite the production workbook or an existing output file.

For every company to process: set `Status = Approved`, choose `CV Tailoring = Start`,
then save and close Excel. Both values are checked again at each stage and before email delivery.

## 1. Export the approved research queue

```sh
.venv/bin/python main.py exchange research
```

This clones/syncs the private repo, publishes `requests/<batch-id>/companies.json` and
a README output contract. Only current Approved + Start companies are exported.
After a successful push, the workbook's Pipeline Status becomes `Exported`.
Repeat exports of identical requests have the same batch ID.
Research is intentionally done by ChatGPT after this command, rather than by this command.

Tell ChatGPT: “Read the latest request in japp_data, follow its README contract,
research the listed companies, and commit research/companies/<ID>.json to main.”
The README names the exact schema already used by JAPP's research-to-Excel synchroniser.

## 2. Publish research, master CV and prompts together

After ChatGPT commits research:

```sh
.venv/bin/python main.py exchange package
```

Research ID/name pairs are validated. Missing research or missing user inputs are reported;
other ready companies can continue. The existing safe research synchroniser updates Excel
with a backup. Each ready company gets a portable private input folder:

```text
companies/<ID>_<Company>/
  inputs/
    input_package.json
    master_cv.docx
    research.json
    cv_tailoring_prompt.md
    proposal_prompt.md
    outreach_template.md
    TASK.md
```

SQLite advances to `AWAITING_AI_OUTPUT`; Excel shows `Researched`. Local absolute paths are removed from
the uploaded package. Inputs are hashed and the published hash is saved locally in SQLite.
The separate speculative branch uses company relevance and CV evidence without inventing
a job description or claiming an ATS/JD match score. Advertised roles use the verified JD.

Tell ChatGPT: “Use every approved company inputs folder and TASK.md to tailor the CV
and prepare the proposal DOCX. Contacts will follow; finalise outreach when they arrive.”

The three prompts in `prompts/` adapt the user's combined tailoring/writing specification:
CV evidence and coverage audits go into `outputs/cv_audit.md`, separate from the one-page
CV. Speculative work uses a relevance matrix without invented JD/ATS scores. Proposal
or cover-letter text is capped at 250 words; outreach uses the evidence-led entry-level
pitch. The existing `cover_letter` manifest field represents either letter type.
Prompt changes apply when new input packages are published. They do not revise existing
drafts automatically; downstream packages are hash-protected and must be archived/cancelled
before a new prompt version can replace their inputs.

## 3. Retrieve contacts while ChatGPT prepares documents

The research handoff also asks ChatGPT to write
`research/contact_candidates/<ID>.json` with one evidenced hiring contact, their current
role, public LinkedIn profile URL, official company domain and supporting sources.
These leads are not email recipients. Match them in Apollo's normal browser interface
using the profile URL where supported, otherwise name plus company/domain; confirm the
current employer before revealing a work email. Import the resulting Apollo CSV through
the existing contact importer. Only verified, unsuppressed contacts qualify for outreach.
This browser step is supervised; the terminal command below still requires API access
and does not yet consume ChatGPT's candidate files automatically.

Configure an Apollo API key in the terminal environment (`APOLLO_API_KEY`). The connected
Apollo app in a chat does not automatically provide API credentials to a terminal process.
Account/plan access to people search and enrichment is required.

```sh
.venv/bin/python main.py exchange contacts --reveal-emails --max-reveals 10
```

This searches the official domains from research for the configured target titles,
checks current-company identity, and enriches up to five prospects per company (the
existing Excel contact capacity). `--max-reveals` bounds new enrichment **calls** for the
whole run, not a guaranteed credit cost or guaranteed email count. The command uses the
supported API, not LinkedIn scraping. Broaden `execution.target_titles` locally for
managing directors, founders or department-specific decision makers as appropriate.

People search returns no email address; enrichment can consume credits. JAPP requests
work-email data only: no personal emails, phone reveals or waterfall enrichment. It
records each attempt before making the request and saves completed results. Reruns reuse
cached results rather than spend credits again. An interrupted/uncertain reveal stays
pending and is reported for investigation; it is never automatically retried.
References: [People search](https://docs.apollo.io/reference/people-api-search),
[People enrichment](https://docs.apollo.io/reference/people-enrichment).

Results go to SQLite, the existing `04 Connections` empty slots, and private
`companies/<ID>_<Company>/contacts.json`. Existing manual contact cells and suppressions
are preserved. Only verified, unsuppressed contacts present in Excel qualify for delivery.
If Apollo API access is unavailable, the existing authorised CSV import remains available;
the exchange contact command needs API access to automate discovery.

Tell ChatGPT to use these contact files to finish recipient-specific outreach, then commit
the completed documents and output manifests using the private README contract.

## 4. Collect, draft and send

```sh
.venv/bin/python main.py exchange deliver
```

This pulls committed files, ignores incomplete manifests, checks IDs/names/roles/input
versions, validates both DOCX files, renames and copies them into the local generated
company folders, updates SQLite/Excel, publishes an immutable batch preview to
`batches/<batch-id>.json`, and creates provider drafts. It does not send.
Original private output files are preserved. Local files with different content are never
overwritten: archive the prior versions and cancel any unsent batch before importing revisions.
Validation checks document readability and company/role content; ChatGPT/user review is
still responsible for final visual layout, factual accuracy and email suitability.

To collect only, use `main.py exchange collect`.

For your explicit final draft-and-send run:

```sh
.venv/bin/python main.py exchange deliver --send
```

This flag explicitly approves the current immutable batch after all drafts succeed and
sends it, subject to the existing safeguards. Select a live provider (`mail.provider` =
`outlook` or `gmail`), configure its OAuth environment settings and set
`mail.send_enabled = true` in local config first. The defaults remain **mock** and sending
disabled. Interactive OAuth sign-in may be necessary. Real provider delivery is not
exercised by the test suite. The flag cannot silently enable sending in config.

The final copy in `outputs/outreach.json` is used verbatim per recipient. The writing
prompt is never sent as the email. Attachment hashes, fresh workbook approvals, current
contact verification/suppression, daily send caps and delivery idempotency remain enforced.
If there is one existing pending batch, delivery resumes it; with multiple batches use
the existing `batch` commands to select one. Review the published batch's recipients and
content before using `--send`. A partial batch resumes without resending sent messages;
uncertain sends are not automatically retried.

## Automatic Pipeline Status column

`03 Execution` includes a Pipeline Status dropdown beside Status and CV Tailoring.
Existing blank ID rows initialise to `Not Started`; old internal state labels are migrated
to readable labels. The detailed internal states remain in SQLite.

| Successful stage | Excel Pipeline Status |
| --- | --- |
| Initial dashboard setup | Not Started |
| `exchange research` publishes the request | Exported |
| `exchange package` validates returned research | Researched |
| `exchange contacts` publishes verified, unsuppressed contacts | Contacts Ready |
| `exchange collect` or `deliver` validates and files final outputs | Documents Ready |
| Batch created, provider drafts not yet complete | Draft Pending |
| All provider drafts for a company succeed | Email Drafted |
| Batch approval | Email Queued |
| Some company emails successfully submitted | Sending |
| All company emails successfully submitted | Completed |

The dropdown also includes Awaiting User Inputs, Failed and Cancelled.
Earlier stages do not overwrite a later progress label when rerun. Completed is sticky
for automatic updates, and rows marked Completed are excluded from approval queues,
batch creation and fresh send eligibility checks, even if Approved + Start remain selected.
SQLite's completed-company and per-message send history independently block duplicate
delivery; changing an Excel label does not reset that history. A failed/uncertain or
partially sent batch is not marked Completed.

## Testing

```sh
.venv/bin/python -B -m pytest -q -p no:cacheprovider
```

Tests use temporary mock workbooks, DOCX files, fake GitHub/Apollo integrations and a
mock mail provider. No production workbook edits, credit-consuming calls or real emails.
