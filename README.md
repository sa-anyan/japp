# JAPP — Job Application Processing Pipeline

JAPP is a Python automation I built to handle the repetitive administration that follows an AI-assisted job application workflow. After tailored CVs, cover letters and job descriptions land in Downloads, JAPP identifies them, extracts the company and role from their filenames, matches related documents, organises them into a structured application archive, and automatically updates an Excel application tracker.

## Workflow

```text
Job Description
      ↓
AI-assisted CV / Cover Letter tailoring
      ↓
Application submitted
      ↓
Files land in Downloads
      ↓
JAPP
 ├─ Detects CV / Cover Letter / Job Description
 ├─ Identifies company + role
 ├─ Matches related documents
 ├─ Organises the application archive
 └─ Updates the Excel tracker
```

## What it does

JAPP:

- scans a Downloads folder for PDF and DOCX application documents;
- identifies CVs, cover letters and job descriptions from filenames;
- extracts company and role information;
- normalises role-name variations for matching;
- groups related application documents;
- preserves multiple downloaded versions rather than deleting them;
- organises files into company and role folders;
- scans the full application archive; and
- records applications and document availability in an Excel tracker.

## Filename convention

JAPP works best with predictable filenames.

```text
CV:
Your_Name_CV_COMPANY_ROLE.docx

Cover letter:
Your_Name_Cover_Letter_COMPANY_ROLE.docx

Job description:
COMPANY_ROLE_JD.pdf
```

Example:

```text
Your_Name_CV_Glencore_CommoditiesAnalyst.docx
Your_Name_Cover_Letter_Glencore_CommoditiesAnalyst.docx
Glencore_CommoditiesAnalyst_JD.pdf
```

## Tracker

The generated tracker records:

```text
Company
Job Title
Date Applied
CV
Cover Letter
Job Description
Contacted
Status
Job Link
Contact
Notes
```

## Tech

- Python
- pathlib
- regular expressions
- shutil
- openpyxl

## Privacy

The repository contains the automation code only. Personal CVs, cover letters, job descriptions, application trackers and application archives should not be committed.

## Status

Active development.


## Job discovery — V8.1

JAPP also includes a UK job-discovery pipeline in `japp_v8_1_multisource.py`.

The discovery workflow uses broad search terms to find vacancies quickly, deduplicates results, removes obvious unrelated or senior roles, and saves only the relevant shortlist for manual review.

Current sources include:

- Indeed via JobSpy;
- Reed;
- Totaljobs;
- LinkedIn via JobSpy with a separately configurable result cap;
- adapters for CV-Library and GOV.UK Find a Job, subject to source availability.

The discovery engine does not submit applications. The intended workflow is:

```text
Broad keyword sweep
      ↓
Multi-source vacancy collection
      ↓
Deduplication
      ↓
Relevance filtering
      ↓
Relevant-jobs-only Excel shortlist
      ↓
Manual JD review / CV tailoring / application
```

See `JAPP_V8.1_Bash_Commands.txt` for first-run and recurring commands.
