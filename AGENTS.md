# JAPP execution playbook

When the user says **start**, **run**, **next** or **done** in the established JAPP
workflow, prefer the deterministic private-exchange controller over bespoke scripts:

```sh
.venv/bin/python main.py start
```

Read its compact result and `data/generated/autopilot/status.json`. Do not reread
the entire conversation or run all legacy exchange commands blindly. Use
`start --plan` for a read-only approval check; `start --no-mail` to advance the
exchange without an Outlook login. `start --watch` watches workbook/GitHub changes
without AI calls. The original scheduler remains available as `start --services`.
No command in this controller sends emails or deletes drafts.

## Browser handoffs

- Read `data/generated/autopilot/browser_queue.json` only when contacts are pending.
  Use supported browser controls. Respect `max_new_reveal_attempts` (default 3).
  Match the researched LinkedIn `/in/` identity before one work-email reveal.
  Never reveal mobiles, personal emails or waterfall enrichment.
- Save observed outcomes to `data/generated/autopilot/browser_results.json` using
  the queue's schema; run `start` again to validate/cache/publish them. Merge with
  prior outcomes rather than discard uncertain attempts. Login/security failures
  are not no-match outcomes. A timeout after Access email is uncertain: never
  reveal again automatically. Ask for user review before another spend.
- If a person ID is already in SQLite's enrichment cache, read that result; do
  not reveal it again. Existing contact_checks and contacts.json also prevent
  duplicate searches. Preserve catch-all, role and subsidiary qualifications.
- Prepare ChatGPT's composer from `chatgpt_handoff.txt` using supported browser
  controls; user presses Send. Never submit automatically or extract responses.
  Inspect GitHub commits instead. Never overwrite a nonempty user composer.
- If Apollo reports invalid credentials, refresh once and hand off sign-in. If it
  reports a widget/security challenge, hand off; never bypass it. Zero results
  counts only after the completed search has no error. No query loops.

## Mail and documents

- Outlook draft automation uses User.Read + Mail.ReadWrite, excluding Mail.Send.
  Before creating drafts, reconcile real Drafts/Sent Items, not just local state.
  Already-sent recipients are blocked even if the subject changed. A missing
  previously reserved draft requires review, not automatic recreation.
- Use immutable batch IDs and idempotency keys. Do not work around failed or
  uncertain reservations. Existing sent history must remain intact.
- Do not change existing input packages solely because the master prompt changed;
  package the new prompt for new companies. Revisions need an explicit request.
- Render and review actual final document binaries before releasing new batches.
  Test with mock workbooks/providers; do not run live mail or overwrite the
  production workbook during development. Save and close Excel before execution.

## Recovery

Dirty private checkout: show the changed paths and stop; never reset/force-push.
Git sync failure: stop and resume on the next invocation, no publish retries.
Workbook owner lock: wait for the user to save/close. STOP file: honour it; remove
only when the user explicitly resumes. Store credentials only through existing
sign-in, never in the repositories. Never confuse a draft count with sent status.
