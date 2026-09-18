# JAPP — Job Application Processing Pipeline

JAPP is a Python automation for organising job application documents and maintaining an Excel application tracker.

It turns a repetitive application-admin workflow into a simple pipeline:

```text
Downloads
   ↓
Detect CV / Cover Letter / Job Description
   ↓
Parse company + role from filenames
   ↓
Normalise and match related documents
   ↓
Organise into JAPPS / Company / Role
   ↓
Scan the application archive
   ↓
Update the Excel tracker
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
