# Outreach email — JAPP

The exact supplied cv_tailoring_prompt.md is the authority for writing. Run its optional
modules O1, O2 and O3: the JAPP workflow explicitly requests an email. Where the guidance
below differs in wording, salutation, sign-off or structure, follow that exact modular
specification. The JSON transport and verified-recipient requirements below still apply.

Use the master CV, tailored CV, company research and contacts.json to write a concise,
recipient-centred email in natural UK English. This workflow explicitly requests the
email; do not wait for another request. Candidate facts and metrics come only from the
master CV. Examples illustrate structure, never facts to copy.

Understand the company's business model, differentiation and a plausible contribution.
Use supplied competitor evidence if useful; do not invent competitors or recipient
interests. Respect the reader's autonomy, show practical relevance and make replying easy.
Specific recognition of documented work is better than generic praise or assumed growth.

## Structure

Use a short subject connecting the relevant field/contribution to Samuel Anyan.
Start “Dear [verified recipient name],” then use two or three compact paragraphs:

1. Lead with relevant experience and its connection to the firm's documented work:
   “My work in [evidenced area] may be relevant to [company activity].” Vary naturally;
   do not copy this sentence mechanically or open with empty flattery.
2. Evidence one coherent capability using one or two concrete employment, university
   or personal-project examples from the master CV. Connect methods and achievements
   to relevance. Position Samuel as entry-level; never imply unearned seniority,
   years of experience, a current job or guaranteed business outcomes.
3. Invite a brief conversation without pressure. For speculative outreach, mention
   the attached sample proposal and the contribution it explores, plus the CV.
   For advertised work, reference the exact role and attached cover letter/CV.
   Mention attachments only when they are part of the completed package.

Sign off “Yours sincerely,” with the master-CV name and complete https:// portfolio URL.
JAPP sends plain text: use a full URL that mail clients can recognise, not an HTML anchor.
Avoid disconnected skills, inflated claims, demands, copied sample metrics and fabricated
personalisation. Do not imply a vacancy in speculative outreach. Use supplied recipient
names and emails exactly; preserve any dual-company/email-domain qualification in the
separate audit and never infer an address.

## Output

Write outputs/outreach.json as {"messages":[{"recipient":"exact verified work email",
"subject":"final subject", "body":"final plain-text email with newline paragraphs"}]}.
Use only verified, unsuppressed contacts.json recipients. No labels, placeholders or
instructions in final content. If contacts are absent, prepare documents and wait;
do not invent a recipient or mark the manifest ready. Never send emails.

Runtime inputs: {company}, {role}, {recipient_name}, {recipient_title}, {research_summary},
{job_description}, {cv_evidence}, {linkedin_profile_url}, {portfolio_url}.
