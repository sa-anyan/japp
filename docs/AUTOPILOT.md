# One-command supervised execution

From the connected local checkout:

```sh
cd /Users/user/Documents/ChatGPT/JAPP
.venv/bin/python main.py start --watch
```

Save and close Excel first. Select Approved + Start for new companies. The watcher
polls private GitHub and the saved workbook every 60 seconds, advancing only when
either changes. Ctrl-C stops it; `main.py stop` requests a stop after the current
cycle. If a STOP file is present, explicitly remove it when resuming.

`main.py start` performs one bounded pass and exits. `start --plan` reads approvals
without network, writes or login. `start --no-mail` avoids Outlook authentication.
The old background engine is now `start --services`; existing exchange/batch
commands remain available.

## What runs without AI calls

Git sync and research export, first-time input packaging, exact current-input-hash
and document validation, browser-result imports, contact caching, LinkedIn queue
refresh, real Outlook Drafts/Sent Items reconciliation, private batch previews and
duplicate-safe Outlook draft creation. Multiple old pending batches do not block
new eligible companies. Completed/sent contacts are not recycled. Missing drafts
and uncertain writes pause for review instead of producing duplicates.

Each cycle emits `data/generated/autopilot/status.json`, a stable
`chatgpt_handoff.txt` when needed, and a compact `browser_queue.json`. These are
private ignored local artifacts. No credentials are stored in them. New inputs
use the current sole modular writing prompt; old packages remain immutable.

## What still requires a handoff

ChatGPT research/writing still takes model usage. The user presses Send; JAPP does
not automate ChatGPT submission or response extraction. Codex can prepare the
composer from the generated handoff. Committed outputs resume the watcher.

Apollo browser matching remains attended through supported browser controls, not
an unattended scraper bundled into Python. The generated queue gives Codex exact
names/profiles, outcome fields, a default maximum of three new reveal attempts
per run and recovery rules. Increase deliberately with `--max-reveals N`; this is
an attempt budget, not a guaranteed credit cost. The controller itself never
calls paid Apollo enrichment. Existing API/CSV integrations remain available.

Login, MFA, CAPTCHAs/security challenges, ambiguous employers, and uncertain
credit-consuming requests need human review. Authentication errors are never
recorded as missing contacts. Browser outcomes are imported from
`browser_results.json`, bound to an approved researched LinkedIn identity, cached
and committed to the private repository before contact readiness is updated.

This cuts repeated model reasoning and duplicate enrichment. It does not make
research free, remove Apollo credit charges or promise unattended completion.
The controller is draft-only and has no send/delete action. Read/write-only
Outlook scopes are used; sending still belongs to the separately authorised
legacy send workflow.

## Recovery and validation

Do not rerun uncertain sends/reveals. Use the status report and resolve the stated
blocker, then rerun start. Dirty Git checkouts are never reset. Open Excel locks
are refused. Authentication/transport exceptions stop watch mode to prevent
repeated prompts or ambiguous writes. Tokens remain only in process memory, so a
new terminal process may need sign-in again.

Tests use mock Git publication, workbooks and HTTP/mail providers. No live mailbox
or production workbook is touched by the test suite. Browser navigation itself
is intentionally outside those mocks and needs the attended browser session.
