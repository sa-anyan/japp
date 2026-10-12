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


## Hiring Intelligence — hiring posts and direct outreach leads

Run this **separately** from V8.1 job-board discovery. It helps find announcements
such as `#hiring` + `Energy Analyst` + `Graduate`, identifies junior vacancies
even when the author is a senior manager, and tracks likely contacts for outreach.

The configurable search dimensions are in `hiring_intelligence_config.json`:

- **Hiring signals:** `#hiring`, `I'm hiring`, `We're hiring`, `Join my team`, etc.
- **Keywords:** several role/sector/skills phrases, each matched independently.
- **Role seniority:** internship, graduate, entry-level, junior, analyst, associate.
- **Context:** UK, freshness window, search query budget and minimum match score.

Seniority filtering applies to **the vacancy, never the author's job title**.
Where a post is vague, its role, post date, employer, contact and sponsorship
status are left unverified for human review. Roles explicitly excluding
sponsorship or clearly senior-only are normally filtered out.

### First run in PyCharm / Terminal

From the cloned `japp` folder:

```bash
git pull origin main
python3 -m pip install -r requirements.txt
python3 japp_hiring_intelligence.py --print-queries
```

The last command prints targeted searches with browser links so you can find
posts manually without providing login credentials or API keys.

### Add a hiring post to your current application tracker

```bash
python3 japp_hiring_intelligence.py \
  --add-post "https://www.linkedin.com/posts/EXAMPLE" \
  --title "Junior Energy Analyst" \
  --snippet "#hiring Junior Energy Analyst in London" \
  --workbook "/PATH/TO/JAPPS/tracker.xlsx"
```

Replace `EXAMPLE` with a **real post URL** and adjust the title/snippet to
the public information you observed. The module adds a `Hiring Leads` tab
while preserving the existing tracker tabs, contact statuses, notes and
follow-up dates. If you omit `--workbook`, it creates `Hiring_Leads.xlsx`
in your current working directory.

### Import several manually gathered posts

Create a CSV with columns such as:

```csv
post_url,title,snippet,poster,poster_role,post_date,company,location,job_url
```

Then run:

```bash
python3 japp_hiring_intelligence.py \
  --import-csv "/PATH/TO/posts.csv" \
  --workbook "/PATH/TO/JAPPS/tracker.xlsx"
```

JSON lists are supported using `--import-json`.

### Optional automated public search (Brave Search API)

If you have a Brave Search API subscription, set `BRAVE_SEARCH_API_KEY` as a
local environment variable (never commit it), and run:

```bash
python3 japp_hiring_intelligence.py \
  --search-brave --max-queries 5 \
  --workbook "/PATH/TO/JAPPS/tracker.xlsx"
```

Increase `--max-queries` after inspecting results and API usage. Results are
limited to public search-index snippets linking to LinkedIn posts; the tool
**does not visit LinkedIn, log in, scrape profiles, bypass restrictions or send
messages**. A search-index freshness filter is **not** proof of the original
post date, so dates remain unverified until independently checked. Actual
discovery depends on your search provider's index and API access.

### Output: Hiring Leads tab

Includes original post link, role, company, potential contact, contact
confidence, role seniority, matched keywords, recency, visa-sponsorship status,
match score, reason, job URL, outreach status and follow-up notes. Existing
leads are deduplicated by URL so a second search doesn't erase your outreach
history. The score is an **internal prioritisation heuristic**, not a verified
probability of being hired.

### Tests

```bash
python3 -m unittest discover -s tests -p "test_hiring_intelligence.py" -v
```

Do not commit real application trackers, post contact lists or API keys.
