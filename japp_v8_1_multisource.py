#!/usr/bin/env python3
"""
JAPP v8.1 — UK multi-source job discovery pipeline.

Collector foundation:
- python-jobspy for Indeed and LinkedIn
- direct public-page adapters for Reed, Totaljobs, CV-Library and GOV.UK Find a Job
- parser architecture informed by public open-source job-board projects

JAPP adds:
- broad single-keyword discovery across UK job sources
- last-7-days collection
- deduplication before classification
- relevance filtering for energy, oil & gas, LNG, commodities, investment,
  project economics, modelling and adjacent analytical roles
- seniority and obvious unrelated-occupation rejection
- relevant-jobs-only Excel output
- optional debug workbook and per-keyword checkpointing
- a lower, separately configurable LinkedIn result cap

This script does NOT automate applications and does NOT attempt to defeat
CAPTCHAs or anti-bot challenges.
"""

import argparse
import asyncio
import json
from urllib.parse import quote_plus, urljoin
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import httpx
from bs4 import BeautifulSoup
from jobspy import scrape_jobs


DISCOVERY_TERMS = [
    "analyst", "energy", "gas", "oil", "LNG", "petroleum",
    "commodity", "commodities", "trading", "market", "investment",
    "finance", "economics", "CAPEX", "OPEX", "techno-economic",
    "modelling", "commercial", "infrastructure", "renewable",
    "hydrogen", "carbon", "project", "reservoir", "upstream", "power",
    "oil and gas",
]

# UK sources JobSpy supports well enough for this first integration.
# ZipRecruiter is omitted because JobSpy documents it as US/Canada only.
DEFAULT_SITES = ["indeed"]

# Sector vocabulary. Used in context, not as an automatic pass.
SECTOR = re.compile(
    r"\b(energy|oil|gas|lng|petroleum|crude|refin(?:e|ery|ing)|commodity|commodities|"
    r"electricity|renewable|solar|wind|hydrogen|carbon|decarbon|low.?carbon|"
    r"energy transition|upstream|downstream|midstream|reservoir|subsurface|biofuel|"
    r"emissions?|utilities|natural resources?|"
    r"power market|power markets|power trading|power generation|power sector|"
    r"power system|power systems|power infrastructure|power network|power networks|"
    r"energy infrastructure)\b", re.I
)

# Work Samuel actually wants to do.
TARGET_WORK = re.compile(
    r"\b(analyst|analysis|analytics|model(?:ling|ing)?|forecast(?:ing)?|economics?|"
    r"economic evaluation|techno.?economic|valuation|investment|project finance|"
    r"financial model(?:ling|ing)?|commercial analysis|market fundamentals?|"
    r"supply.{0,8}demand|trading|trade operations|risk|exposure|capex|opex|"
    r"feasibility|scenario|sensitivity|due diligence|asset analysis|project development|"
    r"business planning|reservoir economics|field development|portfolio|strategy|"
    r"commercial|project controls?|cost engineer(?:ing)?|cost analysis)\b", re.I
)

# Strong phrases can qualify unusual titles when the actual work is clearly relevant.
STRONG_DOMAIN_WORK = re.compile(
    r"\b("
    r"(?:energy|oil|gas|lng|petroleum|commodity|commodities|electricity|renewable|hydrogen|carbon|"
    r"upstream|downstream|midstream|reservoir|infrastructure).{0,45}"
    r"(?:analysis|analytics|modelling|modeling|forecast|economics|valuation|investment|trading|"
    r"risk|commercial|capex|opex|feasibility|project development|project finance|market)"
    r"|"
    r"(?:analysis|analytics|modelling|modeling|forecast|economics|valuation|investment|trading|"
    r"risk|commercial|capex|opex|feasibility|project development|project finance|market).{0,45}"
    r"(?:energy|oil|gas|lng|petroleum|commodity|commodities|electricity|renewable|hydrogen|carbon|"
    r"upstream|downstream|midstream|reservoir|infrastructure)"
    r")\b", re.I | re.S
)

TARGET_TITLE = re.compile(
    r"\b(analyst|economist|commercial|trading|trader|investment|finance|financial|"
    r"project development|development analyst|asset analyst|market|strategy|modelling|modeling|"
    r"project controls|cost engineer|cost analyst|reservoir|subsurface|petroleum|upstream)\b", re.I
)

ADJACENT_TITLE = re.compile(
    r"\b(project engineer|process engineer|operations engineer|operations analyst|"
    r"business analyst|data analyst|pricing analyst|graduate engineer|energy engineer)\b", re.I
)

SENIOR = re.compile(
    r"\b(senior|sr\.?|lead|principal|manager|director|head|vice president|vp|chief|partner)\b", re.I
)

# Explicitly unrelated occupation families observed in the V7 test workbook.
UNRELATED_TITLE = re.compile(
    r"\b(chef|cook|kitchen porter|porter|waiter|waitress|barista|reception(?:ist| supervisor)?|"
    r"sales assistant|retail assistant|shop assistant|customer service assistant|"
    r"office administrator|administrator|admin assistant|general labourer|labourer|"
    r"warehouse|driver|security officer|care assistant|support worker|nurse|teacher|"
    r"software developer|software engineer|it support|helpdesk|diesel engineer|mechanic|"
    r"commercial gas engineer|gas engineer|heating engineer|boiler engineer|service engineer|"
    r"field service engineer|maintenance engineer|installation engineer|gas safe|plumber|"
    r"plumbing|hvac|electrician|technician|"
    r"gas trainer|responsible person|domain architect|sales specialist|sales consultant|"
    r"account manager|business development manager)\b", re.I
)

EXPERIENCE_5PLUS = re.compile(
    r"\b(?:minimum\s+of\s+|at least\s+|minimum\s+)?([5-9]|1\d)\+?\s*(?:years?|yrs?)"
    r"(?:\s+of)?\s+(?:relevant\s+)?experience\b", re.I
)


def clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def canonical_url(url):
    try:
        p = urlsplit(clean(url))
        return urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), "", ""))
    except Exception:
        return clean(url)


def google_term(keyword):
    return f'{keyword} jobs in United Kingdom since last week'


def normalise(raw, keyword):
    rows = []
    if raw is None or raw.empty:
        return rows

    for _, r in raw.iterrows():
        d = r.to_dict()
        rows.append({
            "Title": clean(d.get("title")),
            "Company": clean(d.get("company")),
            "Location": clean(d.get("location")),
            "Date Posted": clean(d.get("date_posted")),
            "Description": clean(d.get("description")),
            "Source": clean(d.get("site")),
            "Job URL": clean(d.get("job_url")),
            "Direct URL": clean(d.get("job_url_direct")),
            "Job Type": clean(d.get("job_type")),
            "Job Level": clean(d.get("job_level")),
            "Company Industry": clean(d.get("company_industry")),
            "Is Remote": clean(d.get("is_remote")),
            "Min Salary": d.get("min_amount"),
            "Max Salary": d.get("max_amount"),
            "Currency": clean(d.get("currency")),
            "Search Keyword": keyword,
        })
    return rows



# ---------------------------------------------------------------------
# Direct UK job-board collectors
# Based on the public parsing architecture in Doggo457/apify-jobwebsite-scraper.
# This version intentionally uses normal HTTP requests only: no stealth,
# CAPTCHA bypass, proxy rotation, or anti-bot evasion.
# ---------------------------------------------------------------------

DIRECT_UK_SOURCES = {"reed", "totaljobs", "cvlibrary", "findajob"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

def _text(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""

def _extract_jsonld_jobs(html, source, base_url):
    soup = BeautifulSoup(html, "html.parser")
    out = []

    def walk(obj):
        if isinstance(obj, list):
            for x in obj:
                walk(x)
            return
        if not isinstance(obj, dict):
            return
        if obj.get("@type") == "JobPosting":
            title = clean(obj.get("title"))
            org = obj.get("hiringOrganization") or {}
            company = clean(org.get("name") if isinstance(org, dict) else org)
            loc = ""
            jl = obj.get("jobLocation")
            if isinstance(jl, list) and jl:
                jl = jl[0]
            if isinstance(jl, dict):
                addr = jl.get("address") or {}
                if isinstance(addr, dict):
                    bits = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
                    loc = ", ".join(clean(x) for x in bits if clean(x))
            desc = clean(BeautifulSoup(str(obj.get("description") or ""), "html.parser").get_text(" "))
            url = clean(obj.get("url"))
            if url:
                url = urljoin(base_url, url)
            out.append({
                "Title": title,
                "Company": company,
                "Location": loc,
                "Date Posted": clean(obj.get("datePosted")),
                "Description": desc,
                "Source": source,
                "Job URL": url,
                "Direct URL": url,
                "Job Type": clean(obj.get("employmentType")),
                "Job Level": "",
                "Company Industry": clean(obj.get("industry")),
                "Is Remote": "",
                "Min Amount": "",
                "Max Amount": "",
                "Currency": "GBP",
                "Interval": "",
            })
        for v in obj.values():
            walk(v)

    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        try:
            walk(json.loads(raw))
        except Exception:
            continue
    return out

async def _get_html(client, url):
    try:
        r = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=30)
        if r.status_code == 200:
            return r.text
        print(f"    HTTP {r.status_code}: {url}")
    except Exception as e:
        print(f"    fetch failed: {type(e).__name__}: {e}")
    return ""

async def _fetch_detail(client, row):
    url = row.get("Job URL") or row.get("Direct URL")
    if not url:
        return row
    html = await _get_html(client, url)
    if not html:
        return row

    js = _extract_jsonld_jobs(html, row.get("Source",""), url)
    if js:
        d = js[0]
        for k, v in d.items():
            if v and not row.get(k):
                row[k] = v
        if d.get("Description"):
            row["Description"] = d["Description"]
        return row

    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        '[itemprop="description"]',
        '[class*="job-description"]',
        '[class*="vacancy-description"]',
        '#job-description',
        '#main-content',
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el and len(_text(el)) > 80:
            row["Description"] = _text(el)
            break
    return row

async def collect_reed(keyword, max_results=50, fetch_details=True):
    source, base_url = "reed.co.uk", "https://www.reed.co.uk"
    jobs = []
    async with httpx.AsyncClient() as client:
        page = 1
        while len(jobs) < max_results and page <= 5:
            url = f"{base_url}/jobs/{quote_plus(keyword)}-jobs-in-united-kingdom?sortby=DisplayDate"
            if page > 1:
                url += f"&pageno={page}"
            html = await _get_html(client, url)
            if not html:
                break

            parsed = _extract_jsonld_jobs(html, source, base_url)
            if not parsed:
                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select('article[data-qa="job-card"]') or soup.select("article")
                for card in cards:
                    a = card.select_one('a[data-qa="job-card-title"]') or card.select_one("h2 a") or card.select_one("h3 a")
                    if not a:
                        continue
                    row = {
                        "Title": _text(a),
                        "Company": _text(card.select_one('[data-qa="job-card-company"]') or card.select_one('[class*="company"]')),
                        "Location": _text(card.select_one('[data-qa="job-card-location"]') or card.select_one('[class*="location"]')),
                        "Date Posted": _text(card.select_one("time") or card.select_one('[class*="date"]')),
                        "Description": _text(card.select_one('[data-qa="job-card-description"]') or card.select_one("p")),
                        "Source": source,
                        "Job URL": urljoin(base_url, a.get("href","")),
                        "Direct URL": urljoin(base_url, a.get("href","")),
                        "Job Type": "",
                        "Job Level": "",
                        "Company Industry": "",
                        "Is Remote": "",
                        "Min Amount": "",
                        "Max Amount": "",
                        "Currency": "GBP",
                        "Interval": "",
                    }
                    parsed.append(row)
            if not parsed:
                break
            for row in parsed:
                row["Search Keyword"] = keyword
                if fetch_details and len(jobs) < max_results:
                    row = await _fetch_detail(client, row)
                    await asyncio.sleep(0.8)
                jobs.append(row)
                if len(jobs) >= max_results:
                    break
            page += 1
            await asyncio.sleep(1.2)
    return jobs

async def collect_cvlibrary(keyword, max_results=50, fetch_details=True):
    source, base_url = "cv-library.co.uk", "https://www.cv-library.co.uk"
    jobs = []
    async with httpx.AsyncClient() as client:
        page = 1
        while len(jobs) < max_results and page <= 5:
            url = f"{base_url}/search-jobs?q={quote_plus(keyword)}&geo={quote_plus('United Kingdom')}&us=1"
            if page > 1:
                url += f"&page={page}"
            html = await _get_html(client, url)
            if not html:
                break

            parsed = _extract_jsonld_jobs(html, source, base_url)
            if not parsed:
                soup = BeautifulSoup(html, "html.parser")
                cards = (
                    soup.select('li[class*="search-result"]')
                    or soup.select('div[class*="job-result"]')
                    or soup.select('article[class*="job"]')
                    or soup.select('li[data-job-id]')
                    or soup.select('div[data-job-id]')
                )
                for card in cards:
                    a = card.select_one('a[class*="job-title"]') or card.select_one("h2 a") or card.select_one("h3 a") or card.select_one('a[href*="/job/"]')
                    if not a:
                        continue
                    parsed.append({
                        "Title": _text(a),
                        "Company": _text(card.select_one('[class*="company"]') or card.select_one('a[href*="/list-jobs/"]')),
                        "Location": _text(card.select_one('[class*="location"]')),
                        "Date Posted": _text(card.select_one("time") or card.select_one('[class*="date"]')),
                        "Description": _text(card.select_one('[class*="description"]') or card.select_one("p")),
                        "Source": source,
                        "Job URL": urljoin(base_url, a.get("href","")),
                        "Direct URL": urljoin(base_url, a.get("href","")),
                        "Job Type": "",
                        "Job Level": "",
                        "Company Industry": "",
                        "Is Remote": "",
                        "Min Amount": "",
                        "Max Amount": "",
                        "Currency": "GBP",
                        "Interval": "",
                    })
            if not parsed:
                break
            for row in parsed:
                row["Search Keyword"] = keyword
                if fetch_details and len(jobs) < max_results:
                    row = await _fetch_detail(client, row)
                    await asyncio.sleep(0.8)
                jobs.append(row)
                if len(jobs) >= max_results:
                    break
            page += 1
            await asyncio.sleep(1.2)
    return jobs

async def collect_findajob(keyword, max_results=50, fetch_details=True):
    source, base_url = "findajob.dwp.gov.uk", "https://findajob.dwp.gov.uk"
    jobs = []
    async with httpx.AsyncClient() as client:
        page = 1
        while len(jobs) < max_results and page <= 5:
            url = f"{base_url}/search?q={quote_plus(keyword)}&w={quote_plus('United Kingdom')}&pp=25&sort=dt.rv"
            if page > 1:
                url += f"&pg={page}"
            html = await _get_html(client, url)
            if not html:
                break
            parsed = _extract_jsonld_jobs(html, source, base_url)
            if not parsed:
                soup = BeautifulSoup(html, "html.parser")
                links = soup.select('a[href*="/details/"]')
                seen = set()
                for a in links:
                    href = a.get("href","")
                    full = urljoin(base_url, href)
                    if not _text(a) or full in seen:
                        continue
                    seen.add(full)
                    card = a.find_parent("div") or a.find_parent("li") or a.parent
                    parsed.append({
                        "Title": _text(a),
                        "Company": _text(card.select_one('[class*="company"]') if card else None),
                        "Location": _text(card.select_one('[class*="location"]') if card else None),
                        "Date Posted": _text(card.select_one("time") if card else None),
                        "Description": _text((card.select_one('[class*="description"]') or card.select_one("p")) if card else None),
                        "Source": source,
                        "Job URL": full,
                        "Direct URL": full,
                        "Job Type": "",
                        "Job Level": "",
                        "Company Industry": "",
                        "Is Remote": "",
                        "Min Amount": "",
                        "Max Amount": "",
                        "Currency": "GBP",
                        "Interval": "",
                    })
            if not parsed:
                break
            for row in parsed:
                row["Search Keyword"] = keyword
                if fetch_details and len(jobs) < max_results:
                    row = await _fetch_detail(client, row)
                    await asyncio.sleep(0.6)
                jobs.append(row)
                if len(jobs) >= max_results:
                    break
            page += 1
            await asyncio.sleep(1.0)
    return jobs

def _find_job_arrays(data, source, base_url, depth=0):
    if depth > 6:
        return []
    if isinstance(data, dict):
        for v in data.values():
            found = _find_job_arrays(v, source, base_url, depth + 1)
            if found:
                return found
    elif isinstance(data, list) and data:
        sample = data[0]
        if isinstance(sample, dict) and any(k in sample for k in ("title","jobTitle","name")):
            jobs = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                title = clean(item.get("title") or item.get("jobTitle") or item.get("name"))
                if not title:
                    continue
                u = clean(item.get("url") or item.get("jobUrl"))
                jobs.append({
                    "Title": title,
                    "Company": clean(item.get("company") or item.get("companyName") or item.get("advertiserName")),
                    "Location": clean(item.get("location") or item.get("locationName")),
                    "Date Posted": clean(item.get("datePosted") or item.get("postedDate")),
                    "Description": clean(item.get("description") or item.get("snippet")),
                    "Source": source,
                    "Job URL": urljoin(base_url, u),
                    "Direct URL": urljoin(base_url, u),
                    "Job Type": "",
                    "Job Level": "",
                    "Company Industry": "",
                    "Is Remote": "",
                    "Min Amount": "",
                    "Max Amount": "",
                    "Currency": "GBP",
                    "Interval": "",
                })
            return jobs
        for x in data:
            found = _find_job_arrays(x, source, base_url, depth + 1)
            if found:
                return found
    return []

async def collect_totaljobs(keyword, max_results=50, fetch_details=True):
    source, base_url = "totaljobs.com", "https://www.totaljobs.com"
    jobs = []
    async with httpx.AsyncClient() as client:
        page = 1
        while len(jobs) < max_results and page <= 5:
            url = f"{base_url}/jobs/{quote_plus(keyword)}/in-united-kingdom?sortby=Date"
            if page > 1:
                url += f"&page={page}"
            html = await _get_html(client, url)
            if not html:
                break

            parsed = _extract_jsonld_jobs(html, source, base_url)
            if not parsed:
                soup = BeautifulSoup(html, "html.parser")
                nd = soup.select_one("script#__NEXT_DATA__")
                if nd:
                    try:
                        parsed = _find_job_arrays(json.loads(nd.string or nd.get_text()), source, base_url)
                    except Exception:
                        parsed = []
            if not parsed:
                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select('[data-testid="job-card"]') or soup.select('article') or soup.select('[class*="job-card"]')
                for card in cards:
                    a = card.select_one("h2 a") or card.select_one("h3 a") or card.select_one('a[href*="/job/"]')
                    if not a:
                        continue
                    parsed.append({
                        "Title": _text(a),
                        "Company": _text(card.select_one('[class*="company"]')),
                        "Location": _text(card.select_one('[class*="location"]')),
                        "Date Posted": _text(card.select_one("time") or card.select_one('[class*="date"]')),
                        "Description": _text(card.select_one('[class*="description"]') or card.select_one("p")),
                        "Source": source,
                        "Job URL": urljoin(base_url, a.get("href","")),
                        "Direct URL": urljoin(base_url, a.get("href","")),
                        "Job Type": "",
                        "Job Level": "",
                        "Company Industry": "",
                        "Is Remote": "",
                        "Min Amount": "",
                        "Max Amount": "",
                        "Currency": "GBP",
                        "Interval": "",
                    })
            if not parsed:
                break
            for row in parsed:
                row["Search Keyword"] = keyword
                if fetch_details and len(jobs) < max_results:
                    row = await _fetch_detail(client, row)
                    await asyncio.sleep(0.8)
                jobs.append(row)
                if len(jobs) >= max_results:
                    break
            page += 1
            await asyncio.sleep(1.2)
    return jobs

async def scrape_direct_source(keyword, source, results):
    if source == "reed":
        return await collect_reed(keyword, results, True)
    if source == "totaljobs":
        return await collect_totaljobs(keyword, results, True)
    if source == "cvlibrary":
        return await collect_cvlibrary(keyword, results, True)
    if source == "findajob":
        return await collect_findajob(keyword, results, True)
    return []

def collect_direct_sync(keyword, sources, results):
    rows = []
    for source in sources:
        print(f"    [{source}]")
        try:
            got = asyncio.run(scrape_direct_source(keyword, source, results))
            print(f"      collected {len(got)}")
            rows.extend(got)
        except Exception as exc:
            print(f"      failed: {type(exc).__name__}: {exc}")
    return rows

def classify(row):
    """Return (bucket, reason).

    Buckets:
      STRONG   = directly aligned role + sector/work context
      POSSIBLE = adjacent/unusual title but description contains strong domain-work evidence
      REJECT   = unrelated, senior, excessive experience, or weak contextual match
    """
    title = clean(row.get("Title"))
    desc = clean(row.get("Description"))
    industry = clean(row.get("Company Industry"))
    job_level = clean(row.get("Job Level"))
    source = clean(row.get("Source")).lower()
    loc = clean(row.get("Location"))

    # LinkedIn sometimes returns empty descriptions even when requested.
    # Use company industry/job level as secondary context rather than rejecting blindly.
    role_context = " ".join(x for x in [title, desc, industry] if x)
    title_desc = role_context

    if not title:
        return "REJECT", "missing title"
    if UNRELATED_TITLE.search(title):
        return "REJECT", "unrelated occupation family"
    if SENIOR.search(title):
        return "REJECT", "senior title"
    if EXPERIENCE_5PLUS.search(desc):
        return "REJECT", "5+ years explicitly required"

    if loc and re.search(r"\b(united states|usa|canada|australia|india|singapore)\b", loc, re.I):
        return "REJECT", "non-UK location"

    sector_in_title = bool(SECTOR.search(title))
    sector_anywhere = bool(SECTOR.search(title_desc))
    work_in_title = bool(TARGET_TITLE.search(title))
    work_anywhere = bool(TARGET_WORK.search(title_desc))
    strong_context = bool(STRONG_DOMAIN_WORK.search(title_desc))

    # Strong: the title itself carries target work and the vacancy is genuinely
    # in the target sector, OR the title itself is sector-specific and analytical.
    if work_in_title and sector_anywhere and strong_context:
        return "STRONG", "target role + target sector + role-context evidence"

    if sector_in_title and work_anywhere and strong_context:
        return "STRONG", "sector-specific title + target analytical/commercial work"

    # Possible: preserve unusual titles such as Business Analyst where the
    # description itself clearly joins domain + target work.
    if (ADJACENT_TITLE.search(title) or work_in_title) and strong_context:
        return "POSSIBLE", "adjacent/unusual title with strong domain-work context"

    # LinkedIn fallback: descriptions are sometimes unavailable. Do not throw away
    # clearly relevant titles if LinkedIn's own industry metadata supports the sector.
    if source == "linkedin" and not desc:
        industry_sector = bool(SECTOR.search(industry))
        title_sector = bool(SECTOR.search(title))
        title_work = bool(TARGET_TITLE.search(title) or ADJACENT_TITLE.search(title))

        if title_work and (title_sector or industry_sector):
            return "POSSIBLE", "LinkedIn title + industry match; description unavailable"

    return "REJECT", "insufficient role-level evidence"


def dedupe(rows):
    out, urls, pairs = [], set(), set()
    for r in rows:
        u = canonical_url(r.get("Direct URL") or r.get("Job URL"))
        pair = (
            re.sub(r"\W+", " ", clean(r.get("Title")).lower()).strip(),
            re.sub(r"\W+", " ", clean(r.get("Company")).lower()).strip(),
        )
        if u and u in urls:
            continue
        if all(pair) and pair in pairs:
            continue
        if u:
            urls.add(u)
        if all(pair):
            pairs.add(pair)
        out.append(r)
    return out


def load_checkpoint(path):
    """Resume state from the optional debug workbook.

    The user-facing workbook intentionally stores relevant jobs only.
    """
    debug_path = path.with_name(path.stem + "_debug.xlsx")
    if not debug_path.exists():
        return [], [], [], [], [], []
    try:
        raw = pd.read_excel(debug_path, sheet_name="Raw").fillna("").to_dict("records")
        strong = pd.read_excel(debug_path, sheet_name="Strong Match").fillna("").to_dict("records")
        possible = pd.read_excel(debug_path, sheet_name="Possible Match").fillna("").to_dict("records")
        rejected = pd.read_excel(debug_path, sheet_name="Rejected").fillna("").to_dict("records")
        progress = pd.read_excel(debug_path, sheet_name="Progress").fillna("").to_dict("records")
        source_stats = pd.read_excel(debug_path, sheet_name="Source Stats").fillna("").to_dict("records")
        return raw, strong, possible, rejected, progress, source_stats
    except Exception:
        return [], [], [], [], [], []


def save_checkpoint(path, raw_rows, strong, possible, rejected, progress, source_stats, debug=False):
    """Save only relevant jobs to the main workbook.

    Main workbook:
      Jobs = Strong + Possible matches only

    Optional debug workbook (--debug):
      Raw / Strong Match / Possible Match / Rejected / Progress / Source Stats / Keywords
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    relevant = []
    for row in strong:
        r = dict(row)
        r["JAPP Bucket"] = "STRONG"
        relevant.append(r)
    for row in possible:
        r = dict(row)
        r["JAPP Bucket"] = "POSSIBLE"
        relevant.append(r)

    relevant = dedupe(relevant)

    tmp = path.with_name(path.stem + ".tmp.xlsx")
    with pd.ExcelWriter(tmp, engine="openpyxl") as w:
        pd.DataFrame(relevant).to_excel(w, sheet_name="Jobs", index=False)
    tmp.replace(path)

    if debug:
        debug_path = path.with_name(path.stem + "_debug.xlsx")
        debug_tmp = debug_path.with_name(debug_path.stem + ".tmp.xlsx")
        with pd.ExcelWriter(debug_tmp, engine="openpyxl") as w:
            pd.DataFrame(dedupe(raw_rows)).to_excel(w, sheet_name="Raw", index=False)
            pd.DataFrame(dedupe(strong)).to_excel(w, sheet_name="Strong Match", index=False)
            pd.DataFrame(dedupe(possible)).to_excel(w, sheet_name="Possible Match", index=False)
            pd.DataFrame(dedupe(rejected)).to_excel(w, sheet_name="Rejected", index=False)
            pd.DataFrame(progress).to_excel(w, sheet_name="Progress", index=False)
            pd.DataFrame(source_stats).to_excel(w, sheet_name="Source Stats", index=False)
            pd.DataFrame({"Discovery Keyword": DISCOVERY_TERMS}).to_excel(
                w, sheet_name="Keywords", index=False
            )
        debug_tmp.replace(debug_path)


def scrape_term(keyword, sites, results_per_site, hours_old):
    kwargs = dict(
        site_name=sites,
        search_term=keyword,
        google_search_term=google_term(keyword),
        location="United Kingdom",
        results_wanted=results_per_site,
        hours_old=hours_old,
        country_indeed="UK",
        description_format="markdown",
        verbose=1,
    )

    # LinkedIn's default listing scrape often omits the job description.
    # This asks JobSpy to fetch each LinkedIn job page for the full description
    # and direct employer URL. It is intentionally enabled only for LinkedIn runs.
    if "linkedin" in [s.lower() for s in sites]:
        kwargs["linkedin_fetch_description"] = True

    return scrape_jobs(**kwargs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="job_search_output_v8_1/japp_jobs.xlsx")
    ap.add_argument("--keyword-limit", type=int, default=0)
    ap.add_argument("--results", type=int, default=50, help="Results wanted per non-LinkedIn source per keyword.")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument(
        "--sites",
        default="indeed,reed,totaljobs,cvlibrary,findajob",
        help=(
            "Comma-separated sources. Supported direct UK sources: "
            "reed,totaljobs,cvlibrary,findajob. JobSpy sources: indeed,linkedin,glassdoor,google."
        ),
    )
    ap.add_argument("--sleep", type=float, default=3.0)
    ap.add_argument("--linkedin-results", type=int, default=20,
                    help="Lower per-keyword cap for LinkedIn.")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument(
        "--debug",
        action="store_true",
        help="Also save a separate debug workbook with raw/rejected/progress data.",
    )
    args = ap.parse_args()

    path = Path(args.out)
    sites = [x.strip() for x in args.sites.split(",") if x.strip()]
    terms = DISCOVERY_TERMS[:args.keyword_limit] if args.keyword_limit else DISCOVERY_TERMS
    hours_old = args.days * 24

    if args.fresh:
        raw_rows, strong, possible, rejected, progress, source_stats = [], [], [], [], [], []
    else:
        raw_rows, strong, possible, rejected, progress, source_stats = load_checkpoint(path)

    done = {clean(x.get("Keyword")) for x in progress if clean(x.get("Status")) == "DONE"}

    print(f"JAPP v8.1 | UK multi-source + tighter role filter | UK | last {args.days} days")
    print(f"Sources: {', '.join(sites)}")
    print(f"Keywords this run: {len(terms)}")
    print("UK BOARDS + JOBSPY -> DEDUPE -> CLASSIFY -> SAVE RELEVANT JOBS ONLY")
    print()

    for keyword in terms:
        if keyword in done:
            print(f"✓ already done: {keyword}")
            continue

        print(f"- {keyword}")
        try:
            direct_sites = [s for s in sites if s in DIRECT_UK_SOURCES]
            jobspy_sites = [s for s in sites if s not in DIRECT_UK_SOURCES]

            rows = []

            # Stable/direct UK boards can do the heavier collection.
            if direct_sites:
                rows.extend(collect_direct_sync(keyword, direct_sites, args.results))

            # JobSpy handles Indeed and LinkedIn. Keep LinkedIn deliberately smaller.
            if jobspy_sites:
                non_linkedin = [s for s in jobspy_sites if s != "linkedin"]
                if non_linkedin:
                    df = scrape_term(keyword, non_linkedin, args.results, hours_old)
                    rows.extend(normalise(df, keyword))

                if "linkedin" in jobspy_sites:
                    df_li = scrape_term(keyword, ["linkedin"], args.linkedin_results, hours_old)
                    rows.extend(normalise(df_li, keyword))

            # Dedupe the current search itself first.
            rows_unique_this_search = dedupe(rows)

            # Compare against jobs already stored BEFORE adding anything.
            existing_keys = set()
            for r in raw_rows:
                u = canonical_url(r.get("Direct URL") or r.get("Job URL"))
                pair = (
                    re.sub(r"\\W+", " ", clean(r.get("Title")).lower()).strip(),
                    re.sub(r"\\W+", " ", clean(r.get("Company")).lower()).strip(),
                )
                if u:
                    existing_keys.add(("url", u))
                if all(pair):
                    existing_keys.add(("pair", pair))

            new_rows = []
            duplicate_count = 0
            for r in rows_unique_this_search:
                u = canonical_url(r.get("Direct URL") or r.get("Job URL"))
                pair = (
                    re.sub(r"\\W+", " ", clean(r.get("Title")).lower()).strip(),
                    re.sub(r"\\W+", " ", clean(r.get("Company")).lower()).strip(),
                )

                is_duplicate = False
                if u and ("url", u) in existing_keys:
                    is_duplicate = True
                if all(pair) and ("pair", pair) in existing_keys:
                    is_duplicate = True

                if is_duplicate:
                    duplicate_count += 1
                    continue

                new_rows.append(r)
                if u:
                    existing_keys.add(("url", u))
                if all(pair):
                    existing_keys.add(("pair", pair))

            # Include duplicates that occurred inside the same search result set.
            duplicate_count += len(rows) - len(rows_unique_this_search)

            # Only NEW vacancies enter the stored Raw set and classifier.
            raw_rows.extend(new_rows)

            # Record exactly what each source returned and how many new vacancies survived dedupe.
            if rows:
                counts = pd.Series([clean(r.get("Source")) or "unknown" for r in rows]).value_counts()
                for source, count in counts.items():
                    source_new = sum(
                        1 for r in new_rows
                        if (clean(r.get("Source")) or "unknown") == source
                    )
                    source_stats.append({
                        "Keyword": keyword,
                        "Source": source,
                        "Found": int(count),
                        "New Unique": int(source_new),
                        "Duplicates Skipped": int(count) - int(source_new),
                        "Run Time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
                    })
            else:
                source_stats.append({
                    "Keyword": keyword,
                    "Source": "none",
                    "Found": 0,
                    "New Unique": 0,
                    "Duplicates Skipped": 0,
                    "Run Time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
                })

            n_strong = n_possible = n_reject = 0
            for row in new_rows:
                bucket, reason = classify(row)
                row = dict(row)
                row["JAPP Bucket"] = bucket
                row["JAPP Reason"] = reason
                if bucket == "STRONG":
                    strong.append(row); n_strong += 1
                elif bucket == "POSSIBLE":
                    possible.append(row); n_possible += 1
                else:
                    rejected.append(row); n_reject += 1

            # Keep stored datasets unique as a final safety net.
            raw_rows = dedupe(raw_rows)
            strong = dedupe(strong)
            possible = dedupe(possible)
            rejected = dedupe(rejected)

            progress.append({
                "Keyword": keyword,
                "Status": "DONE",
                "Found This Search": len(rows),
                "New Unique This Search": len(new_rows),
                "Duplicates Skipped": duplicate_count,
                "Strong New": n_strong,
                "Possible New": n_possible,
                "Rejected New": n_reject,
                "Unique Raw Total": len(raw_rows),
                "Unique Strong Total": len(strong),
                "Unique Possible Total": len(possible),
                "Completed": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
                "Note": "",
            })

            save_checkpoint(path, raw_rows, strong, possible, rejected, progress, source_stats, debug=args.debug)

            print(
                f"  found {len(rows)} | new unique {len(new_rows)} | "
                f"duplicates skipped {duplicate_count}"
            )
            print(
                f"  new classified: strong {n_strong} | possible {n_possible} | rejected {n_reject}"
            )
            print(
                f"  stored totals: raw {len(raw_rows)} | strong {len(strong)} | possible {len(possible)}"
            )
            print("  ✓ checkpoint saved")

        except Exception as exc:
            progress.append({
                "Keyword": keyword,
                "Status": "FAILED",
                "Found This Search": 0,
                "New Unique This Search": 0,
                "Duplicates Skipped": 0,
                "Strong New": 0,
                "Possible New": 0,
                "Rejected New": 0,
                "Unique Raw Total": len(raw_rows),
                "Unique Strong Total": len(strong),
                "Unique Possible Total": len(possible),
                "Completed": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
                "Note": f"{type(exc).__name__}: {exc}"[:1000],
            })
            save_checkpoint(path, raw_rows, strong, possible, rejected, progress, source_stats, debug=args.debug)
            print(f"  ! {type(exc).__name__}: {exc}", file=sys.stderr)

        time.sleep(args.sleep)

    saved_relevant = len(dedupe(strong + possible))
    print(f"\nComplete: {path}")
    print(f"Relevant jobs saved to Excel: {saved_relevant}")
    print(f"Strong matches: {len(dedupe(strong))}")
    print(f"Possible matches: {len(dedupe(possible))}")
    if args.debug:
        print(f"Debug workbook: {path.with_name(path.stem + '_debug.xlsx')}")


if __name__ == "__main__":
    main()