# JAPP 2.0 — Hiring Intelligence & Opportunity Engine
Architecture specification v0.1 | 2026-10-15

## Mission
Extend existing JAPP job discovery, hiring-post intelligence, file organisation and tracker with a company-first, relationship-first opportunity workflow. Never equate a discovered contact with a verified email or hiring authority. Prefer useful, personalised communication over bulk blasting.

## Existing compatibility
- Preserve japp_v8_1_multisource.py job discovery and existing hiring-post queries/imports.
- Preserve Excel tracker sheets and history; avoid destructive migrations.
- Existing user CV filenames, tailored documents and archive continue to work.
- LinkedIn public search/index data only through approved sources and manual import: no automated login, evasion, or scraping.

## Architecture
Sources -> staging/normalisation -> entity resolution -> evidence store -> company ranking -> hiring signals + people discovery -> enrichment gateway -> research -> profile/CV match -> prototype opportunity -> drafts -> human approval -> send / follow-ups -> CRM analytics.
A central SQLite database is the local source of truth; PostgreSQL is an optional later backend. Excel is an export, not the database. Workers must be idempotent and resumable.

## Module boundaries
1. ingest: xlsx/csv target companies; jobs; public hiring posts; company-owned websites and opted-in imports. Preserve source URI, observed_at and parser version.
2. companies: normalise name, domain and LinkedIn company URL; resolve duplicates by website domain and reviewed aliases; capture sector, office/role location, B-Corp status evidence, hiring location and relevance tags.
3. signals: vacancies, new offices, projects, funding, expansion, hiring announcements. Record date and original evidence; do not assert inferred hiring as a fact.
4. people: Apollo People Search when permitted; fallback to company/team pages and manually imported public professional profiles. Separate department manager, technical influencer and recruiter; store observed title/date, employer linkage and candidate-role relevance.
5. enrichment: Apollo People Match / Bulk Match gated on entitlement and budget; default no phone reveal/waterfall; save confidence/source and API-credit result. People Search never promises an email.
6. research: citations to company facts and short researched briefs. Missing evidence = unknown; never invent a business problem, recruiter email or quantified outcome.
7. matching: deterministic evidence-based comparison to user's master CV/projects and target role. Candidate experience must be traceable to supplied sources.
8. solution_studio: rank feasible demonstrations by value, confidence and effort. Research -> scoped problem statement -> smallest useful prototype -> tests -> verified results -> portfolio link. Do not build a custom app for every company.
9. outreach: draft one-to-one emails centred on company's actual priorities and relevant evidence. Human approve before sending. Explicit send opt-in, rate cap, suppression/unsubscribe and follow-up cutoffs. Separate job-application messages from direct-marketing classifications; review UK GDPR/PECR before enabling campaigns.
10. crm: attempts, messages, opens/replies where available, applications, interviews, objections, opt-outs, follow-up tasks and statuses.
11. analytics: conversion by sector, role, contact archetype, signal and outreach style; email verification yield and credit consumption.

## Database model (minimum tables)
- companies(id, legal_name, normalised_name, website_domain UNIQUE NULL, linkedin_url, location_city, location_country, sector_tags_json, size_band, bcorp_status, source_url, verified_at, rank_score, status)
- company_aliases(id, company_id FK, alias, source)
- jobs(id, company_id FK, role_title, seniority, location, url UNIQUE, source, posted_at, observed_at, status, match_score)
- hiring_signals(id, company_id FK, job_id FK NULL, type, description, source_url, event_date, observed_at, confidence)
- contacts(id, company_id FK, full_name, given_name, family_name, role_title, department, role_class, linkedin_url UNIQUE NULL, apollo_id NULL, employment_observed_at, decision_power_score, relevance_score, source_url)
- emails(id, contact_id FK, address ENCRYPTED, email_type, verification_status, source, last_verified_at, reveal_cost, lawful_basis_tag, suppressed_at)
- enrichments(id, contact_id FK, provider, request_fingerprint UNIQUE, attempted_at, status, credits_used, response_hash, error_code)
- research_sources(id, company_id FK, url, title, published_at, observed_at, excerpt, claim_type)
- company_briefs(id, company_id FK, version, generated_at, brief_path, evidence_refs_json, reviewed)
- opportunities(id, company_id FK, job_id FK NULL, contact_id FK NULL, type, evidence_summary, priority_score, state)
- artifacts(id, opportunity_id FK, type, path, input_hash, produced_at, reviewed_at)
- outreach(id, contact_id FK, opportunity_id FK, channel, subject, draft_path, approval_status, send_status, sent_at, next_followup_at, message_id, response_category)
- suppression(id, address_hash UNIQUE, reason, created_at, source)
- audit_events(id, entity_type, entity_id, action, at, input_hash, detail_json)
Indices on company_id, status, last_verified_at, next_followup_at, and source URLs. Unique composite keys / upserts to prevent duplicate runs.

## Ranking and gates
Use separate transparent 0-100 scores:
- company fit: sector 25; location/eligible roles 20; transferable chemical engineering value 25; verified hiring/project signal 20; demonstrated mission alignment 10.
- person relevance: role influence 35; functional relevance 30; company/position recency 20; contact-source confidence 15.
- opportunity priority: company fit 30%; vacancy/signal strength 25%; person relevance 20%; candidate-fit evidence 25%.
Unknown fields score zero rather than a fabricated match. Tune weights in configuration, retain component scores.

States:
company: imported -> validated -> researched -> prioritised -> archived.
contact: discovered -> affiliation_checked -> eligible_for_enrichment -> enriched_or_unavailable -> approved_for_outreach -> suppressed.
outreach: proposed -> drafted -> reviewed -> approved -> sent -> responded/followup_due -> closed. No send from drafted alone.
prototype: idea -> scoped -> reviewed -> built -> tested -> published.

## Security and compliance
Only necessary professional information. Do not collect private/personal emails or phones for outreach. Honour terms/robots of approved data providers. No secrets or real people exported to Git. Encrypt email/contact data at rest, use .env for API keys and secret managers when deployed, redact logs, support delete/export requests, expiry/retention, and dedupe suppressions globally. Do not automatically send a high volume of unsolicited messages. Record source and purpose for personal data, apply UK GDPR and PECR requirements and honour all objections and opt-outs.

## Apollo trial strategy
PRE-ACTIVATION: import and clean company list; source company domains; prepare search batches and role taxonomy; calibrate scoring with 20 representative organisations; set enrichment budget; review restrictions and endpoint eligibility; test mock clients and data exports.
DAY 1: activate; test API authentication, People Search, People Match and available credit/usage stats with 1 low-volume example; stop if unauthorized.
DAYS 2-4: bulk company-person discovery by priority; dedupe, validate employment relationship.
DAYS 5-8: enrich only approved high-priority records in bounded batches of at most 10; track charges, disable waterfall/phone unless explicitly approved.
DAYS 9-11: research, draft profiles, verify professional email records, export.
DAYS 12-13: reconcile missing identities and provenance; test backup/recovery; check suppression data.
DAY 14: verify exports, regenerate Excel/CSV snapshots, store local state and disable paid API calls in post-trial mode.
Trial entitlements and credits are variable and must be tested, not assumed. Never rely on continued premium access.

## Infrastructure, suggested layout
japp/
  intelligence/
    config.py
    models.py
    db.py
    ingestion/
    matching/
    providers/apollo.py
    providers/public_sources.py
    scoring.py
    research.py
    matching_cv.py
    solution_studio.py
    drafting.py
    outreach.py
    exporters.py
    workflows.py
  config/intelligence.example.yaml
  migrations/
  tests/test_intelligence_*.py
  docs/HIRING_INTELLIGENCE_ARCHITECTURE.md

Recommended dependencies: Python 3.11+, httpx, pydantic, SQLAlchemy, Alembic, tenacity, openpyxl, pytest; optional FastAPI and Streamlit for local UI. Providers behind interfaces to avoid coupling to Apollo. Rate limits, retries with exponential backoff and Retry-After, checkpointing, per-run budget and dead-letter reports are mandatory.

## CLI / dashboard contracts
japp intel import-companies --xlsx targets.xlsx --dry-run
japp intel discover --companies top-50 --dry-run
japp intel enrich --eligible-only --max-credits 40 --dry-run
japp intel research --priority high
japp intel export --xlsx intelligence.xlsx
japp intel draft --opportunity-id ID
japp intel send --approved-only (disabled by default, requires explicit setting and confirmation)
Dashboard: Overview, Companies, Contacts, Signals, Research, Prototype Ideas, Outreach Approval, Analytics, Runs & Credits.

## Export workbook
Sheets: Priority Companies, Decision Makers, Hiring Signals, Opportunities, Company Research, Prototype Backlog, Outreach CRM, Email Validation, Credits & Runs, Data Dictionary. Never include unverified contacts in a 'verified email' column. Explicit metadata columns: source URL, date checked, evidence strength, email provenance, contact status, suppression flag.

## Build & acceptance milestones
M0 baseline: existing JAPP tests pass and existing tracker rows unchanged.
M1 import 100 synthetic companies; dedupe by domain and review ambiguous matches; reversible migration.
M2 mock company and people sources; 3-5 diverse roles per company; correct recruiter/functional manager distinctions.
M3 Apollo adapter with mocked 403, 429, empty results, insufficient credits, partial/incorrect matches, retries, and checkpoint resumes.
M4 evidence-linked research with 'unknown' handling; no fabricated claims or unverifiable CV statements.
M5 human-reviewed outreach drafts, suppression and one-message-per-contact safety checks.
M6 Excel export and dashboard; round-trip consistency and preservation of prior history.
M7 dry run on a small real approved company cohort, followed by trial activation only after M0-M6 acceptance.

## Decision rules
- Evidence first; every claim linked to source and observed date.
- No automated sending by default.
- Human approval for sending, code/prototype publication and any spending.
- No fabricated addresses, titles, company problems, metrics or personal connections.
- Add real data only after dry-run acceptance; retain auditable inputs/outputs.
