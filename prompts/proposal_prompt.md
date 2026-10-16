# Proposal or cover letter — JAPP

The exact supplied cv_tailoring_prompt.md is the authority for writing. The JAPP workflow
explicitly requests its optional modules O1/O2 and O4 for speculative proposals or O5 for
advertised cover letters. Follow that exact specification over any differing narrative
or layout guidance below. Retain the DOCX/manifest transport requirements.

This workflow explicitly requests the document. Read input_package.json, research.json,
master_cv.docx and TASK.md. The master CV is the sole source of candidate facts and metrics;
research supplies company context only. Write natural UK English with confident, credible
entry-level positioning. Examples and hypotheses are not evidence of candidate achievements.

## Speculative approach: proposal

Maximum 250 words including title, addresses, salutation and sign-off. One clean A4 page.
Use this narrative, with the recipient's interests central:

1. Describe a documented company activity and why a specific contribution could matter.
   Distinguish facts from hypotheses. Do not assert unknown internal problems or tool gaps.
2. Offer one small, feasible engineering, modelling, analytics or automation project
   supported by Samuel's actual skills. Explain its business purpose in plain English.
3. Connect one or two evidenced master-CV achievements to the ability to deliver it.
4. Explain a simple achievable plan: validate the need and inputs, demonstrate a small
   prototype with synthetic or authorised data, then assess usefulness with agreed
   measures. Adapt the plan to the project rather than repeat a generic formula.
5. Invite a brief conversation respectfully. Do not presume employment or imply a vacancy.

Do not present planned functionality as completed work or promise ROI/savings. Use business
model, differentiation and competitor evidence where supplied and useful; never invent
competitors or recipient priorities. Show specific interest without exaggerated praise.

## Advertised vacancy: cover letter

Adapt the narrative to the verified role and responsibilities. Lead with relevant experience,
demonstrate fit with concrete evidence, explain the contribution and invite discussion.
Do not force an unsolicited project into every cover letter. Keep the same 250-word maximum
and one-page layout. Never invent a job description or unsupported requirements.

## Presentation and output

Produce a restrained professional DOCX: Arial, readable 10.5–11pt body, clear paragraph
spacing, consistent alignment, generous margins and an optional dark navy title. Avoid
clutter and decorative graphics. Use candidate contact details and clickable portfolio/
LinkedIn destinations from the master CV. Use supplied verified recipient details; omit
unknown postal addresses. Name the company and, for advertised work, the exact role.
Render and inspect page count, clipping and links before marking the manifest ready.

Save under outputs. For either branch, reference the file using the existing manifest
`cover_letter` field; JAPP applies the canonical filename. Do not change the schema.
Record word count and unresolved issues in outputs/cv_audit.md, separately from the letter.
Never send the document or an email.

Runtime inputs: {company}, {role}, {job_description}, {research_summary}.
