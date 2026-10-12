#!/usr/bin/env python3
"""JAPP Hiring Intelligence: hiring-post leads, not automated LinkedIn scraping.

Supported discovery:
* Generate editable search-engine queries for public hiring announcements.
* Optional Brave Search API results (set BRAVE_SEARCH_API_KEY).
* Import manually researched posts from CSV/JSON, or add a single post URL.
* Score and deduplicate leads into a Hiring Leads sheet in any Excel workbook.

Does not log into LinkedIn, crawl its pages, or send messages.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

CONFIG_PATH = Path(__file__).with_name("hiring_intelligence_config.json")
SHEET = "Hiring Leads"
HEADERS = [
    "Post URL", "Company", "Vacancy / Role", "Posted By", "Poster Job Title",
    "Post Date", "Location", "Hiring Signal", "Keywords Matched",
    "Target Seniority", "Seniority Confidence", "Contact Type",
    "Contact Confidence", "Sponsorship", "Match Score", "Review Status",
    "Why Matched", "Job URL", "Outreach Status", "Follow-up Date",
    "Source", "Post Excerpt", "Notes", "Last Seen",
]
WIDTHS = [49, 24, 37, 22, 29, 15, 19, 21, 38, 19, 22, 21, 21, 18, 15, 19, 48, 49, 19, 18, 15, 80, 50, 16]
INPUT_ALIASES = {
    "Post URL": ("post_url", "url", "linkedin_url", "linkedin post", "post link"),
    "Company": ("company", "employer"),
    "Vacancy / Role": ("position", "role", "job_title", "vacancy", "title"),
    "Posted By": ("poster", "posted_by", "author", "contact"),
    "Poster Job Title": ("poster_role", "poster_job_title", "author_role"),
    "Post Date": ("post_date", "date_posted", "published", "date"),
    "Location": ("location", "region"),
    "Job URL": ("job_url", "vacancy_url", "application_url"),
    "Post Excerpt": ("snippet", "description", "post_text", "text", "excerpt"),
    "Source": ("source",),
}
SENIOR_EXCLUDE = re.compile(
    r"\b(?:senior|sr\.?|lead|principal|manager|director|head of|vp|vice president|chief)\b",
    re.I,
)
VISA_NO = re.compile(
    r"\b(?:no|without|cannot|can't|unable to|not offering|not provide)\s+(?:visa\s+)?sponsorship\b"
    r"|\b(?:no|without)\s+(?:skilled worker\s+)?visa\s+sponsorship\b", re.I,
)
VISA_YES = re.compile(r"\b(?:visa\s+sponsorship\s+(?:available|offered|provided)"
                      r"|offer(?:s|ing)?\s+(?:visa\s+)?sponsorship)\b", re.I)


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def match(term, text):
    """Whole-term matching so 'AI' does not match 'paid'."""
    return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I))


def canonical(url):
    """Strip trackers to deduplicate the same public post across searches."""
    raw = clean(url)
    if not raw:
        return ""
    p = urlsplit(raw)
    if p.scheme not in ("https", "http") or not p.netloc:
        return ""
    host = p.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", p.path).rstrip("/")
    # LinkedIn feed URLs can contain a useful ID in the path; don't remove it.
    return urlunsplit(("https", host, path, "", ""))


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    for key in ("hiring_signals", "keywords", "seniority"):
        if not isinstance(config.get(key), list) or not config[key]:
            raise ValueError(f"Configuration must include a non-empty '{key}' list")
    return config


def generate_queries(config, limit=None):
    """Rotate through 3 search dimensions without generating their full product."""
    signals = [clean(s) for s in config["hiring_signals"] if clean(s)]
    keywords = [clean(k) for k in config["keywords"] if clean(k)]
    levels = [clean(s) for s in config["seniority"] if clean(s)]
    budget = int(limit if limit is not None else config.get("max_queries", 30))
    if budget < 0:
        raise ValueError("max_queries must be zero or positive")
    count = min(budget, len(signals) * len(keywords) * len(levels))
    country = clean(config.get("location", "UK"))
    # Enumerate round-robin by keyword, changing both signal and seniority.
    out, seen = [], set()
    i = 0
    while len(out) < count and i < len(signals) * len(keywords) * len(levels) * 3:
        keyword = keywords[i % len(keywords)]
        band = i // len(keywords)
        signal = signals[(band + i) % len(signals)]
        seniority = levels[(band + i // max(1, len(signals))) % len(levels)]
        key = (signal.lower(), keyword.lower(), seniority.lower())
        i += 1
        if key in seen:
            continue
        seen.add(key)
        q = f'site:linkedin.com/posts/ "{signal}" "{keyword}" "{seniority}" "{country}"'
        out.append(q)
    return out


def get_brave_results(query, api_key, recency_days=7, count=10):
    """Search public results via a supported API; never fetch LinkedIn pages."""
    freshness = "pd" if recency_days <= 1 else "pw" if recency_days <= 7 else "pm"
    params = urlencode({"q": query, "count": min(max(count, 1), 20), "freshness": freshness})
    request = Request(
        "https://api.search.brave.com/res/v1/web/search?" + params,
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
    )
    with urlopen(request, timeout=25) as response:
        data = json.load(response)
    leads = []
    for item in data.get("web", {}).get("results", []):
        url = item.get("url", "")
        # Web search may return unrelated domains. Only retain post URLs.
        if "linkedin.com" not in urlsplit(url).netloc.lower():
            continue
        if "/posts/" not in urlsplit(url).path.lower():
            continue
        leads.append({
            "post_url": url,
            "title": clean(item.get("title")),
            "snippet": clean(item.get("description")),
            "post_date": "",  # Search-index freshness is NOT an observed post date.
            "source": "Brave Search (public index)",
        })
    return leads


def import_records(path):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    if path.suffix.lower() == ".json":
        with path.open(encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            payload = payload.get("posts", payload.get("leads", []))
        if not isinstance(payload, list):
            raise ValueError("JSON input must contain a list of posts")
        return payload
    raise ValueError("Input must be a .csv or .json file")


def normalise(record):
    lookup = {clean(k).lower(): v for k, v in record.items()}
    out = {}
    for column, aliases in INPUT_ALIASES.items():
        choices = (column.lower(),) + aliases
        out[column] = next((clean(lookup[a]) for a in choices if clean(lookup.get(a))), "")
    out["Post URL"] = canonical(out["Post URL"])
    out["Job URL"] = canonical(out["Job URL"])
    return out


def observed_date(value):
    value = clean(value)
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value[:19] if "T" in fmt else value, fmt).date()
        except ValueError:
            continue
    return None


def classify(raw, config, today=None):
    """Missing details remain unverified, not hallucinated."""
    today = today or datetime.now(timezone.utc).date()
    row = normalise(raw)
    if not row["Post URL"]:
        return None
    role = row["Vacancy / Role"]
    excerpt = row["Post Excerpt"]
    # Poster job title is intentionally excluded: a senior director can post a graduate vacancy.
    vacancy_text = clean(role + " " + excerpt)
    signals = [x for x in config["hiring_signals"] if match(x, vacancy_text)]
    keywords = [x for x in config["keywords"] if match(x, vacancy_text)]
    seniority = [x for x in config["seniority"] if match(x, vacancy_text)]
    senior_title = bool(SENIOR_EXCLUDE.search(role))
    senior_context = bool(re.search(
        r"\b(?:hiring|seeking|recruiting|looking for|vacancy for)\s+(?:an?\s+)?"
        r"(?:experienced\s+)?(?:senior|lead|principal|manager|director)\b", vacancy_text, re.I,
    ))
    # The word 'manager' elsewhere in a post should not alone exclude the vacancy.
    excluded_seniority = senior_title or senior_context
    if seniority:
        level, level_conf = seniority[0], "Explicit"
    else:
        level, level_conf = "Unconfirmed", "Needs review"
    visa_text = vacancy_text
    sponsor = "No" if VISA_NO.search(visa_text) else "Yes" if VISA_YES.search(visa_text) else "Unknown"
    poster_role = row["Poster Job Title"]
    if re.search(r"\b(recruit(?:er|ing|ment)|talent acquisition|resourcing)\b", poster_role, re.I):
        contact, confidence = "Recruiter", "Likely"
    elif re.search(r"\b(head|director|lead|manager|partner)\b", poster_role, re.I) and re.search(
        r"\b(my team|our team|join us|we are hiring|we're hiring)\b", excerpt, re.I
    ):
        contact, confidence = "Possible hiring manager", "Unverified"
    else:
        contact, confidence = "Unverified", "Unverified"
    posted = observed_date(row["Post Date"])
    recent = posted is not None and 0 <= (today - posted).days <= int(config.get("posted_within_days", 7))
    stale = posted is not None and (today - posted).days > int(config.get("posted_within_days", 7))
    location = row["Location"]
    target_location = clean(config.get("location", "UK"))
    # Ambiguous locations are reviewed; never assume the post is in the UK.
    loc_match = bool(location and (match(target_location, location) or
                     (target_location.lower() == "uk" and re.search(
                         r"\b(uk|united kingdom|london|manchester|birmingham|glasgow|remote uk)\b",
                         location, re.I))))
    score = (
        (18 if signals else 0)
        + (22 if keywords else 0) + min(max(0, len(keywords) - 1) * 5, 10)
        + (18 if seniority else 7)
        + (9 if loc_match else 3)
        + (10 if recent else 2 if posted is None else 0)
        + (7 if contact != "Unverified" else 2)
        + (6 if sponsor == "Yes" else 3 if sponsor == "Unknown" else 0)
    )
    score = min(100, score)
    reasons = []
    if keywords:
        reasons.append("Matched " + ", ".join(keywords))
    if signals:
        reasons.append("hiring intent")
    if seniority:
        reasons.append("junior-level signal")
    if not posted:
        reasons.append("post date unverified")
    if sponsor == "Unknown":
        reasons.append("visa sponsorship unknown")
    if excluded_seniority:
        reasons.append("senior vacancy indicated")
    if stale:
        reasons.append("outside selected date range")
    if sponsor == "No":
        reasons.append("sponsorship excluded")
    status = "Exclude" if excluded_seniority or stale or not keywords or not signals or sponsor == "No" else (
        "Review" if score >= int(config.get("minimum_score", 45)) else "Low priority")
    row.update({
        "Hiring Signal": ", ".join(signals),
        "Keywords Matched": ", ".join(keywords),
        "Target Seniority": level,
        "Seniority Confidence": level_conf,
        "Contact Type": contact,
        "Contact Confidence": confidence,
        "Sponsorship": sponsor,
        "Match Score": score,
        "Review Status": status,
        "Why Matched": "; ".join(reasons),
        "Outreach Status": "Not contacted",
        "Follow-up Date": "",
        "Notes": "",
        "Last Seen": today.isoformat(),
    })
    return row


def save_leads(path, candidates, include_excluded=False):
    """Append/update one tab; preserve other sheets and manual outreach edits."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    book = load_workbook(path) if path.exists() else Workbook()
    if SHEET in book.sheetnames:
        sheet = book[SHEET]
    else:
        sheet = book.create_sheet(SHEET)
        sheet.append(HEADERS)
    # A fresh openpyxl Workbook has an empty Sheet1: keep it only when it has content.
    if "Sheet" in book.sheetnames and "Sheet" != SHEET and book["Sheet"].max_row == 1 and book["Sheet"]["A1"].value is None:
        del book["Sheet"]
    if [sheet.cell(1, col).value for col in range(1, len(HEADERS) + 1)] != HEADERS:
        raise ValueError("Existing 'Hiring Leads' sheet has different headers; back it up before updating")
    col = {name: i + 1 for i, name in enumerate(HEADERS)}
    existing = {}
    for r in range(2, sheet.max_row + 1):
        key = canonical(sheet.cell(r, col["Post URL"]).value)
        if key:
            existing[key] = r
    added = updated = skipped = 0
    for lead in candidates:
        if not lead:
            continue
        if lead["Review Status"] == "Exclude" and not include_excluded:
            skipped += 1
            continue
        key = canonical(lead["Post URL"])
        if not key:
            skipped += 1
            continue
        if key in existing:
            rownum = existing[key]
            # Keep user decisions, notes, contacts and all original content.
            for field in ("Keywords Matched", "Hiring Signal", "Match Score",
                          "Review Status", "Why Matched", "Last Seen",
                          "Target Seniority", "Seniority Confidence", "Sponsorship"):
                if lead.get(field) not in ("", None):
                    sheet.cell(rownum, col[field]).value = lead[field]
            for field in ("Company", "Vacancy / Role", "Posted By", "Poster Job Title",
                          "Post Date", "Location", "Job URL", "Post Excerpt", "Source",
                          "Contact Type", "Contact Confidence"):
                if lead.get(field) and not sheet.cell(rownum, col[field]).value:
                    sheet.cell(rownum, col[field]).value = lead[field]
            updated += 1
        else:
            sheet.append([lead.get(column, "") for column in HEADERS])
            rownum = sheet.max_row
            existing[key] = rownum
            added += 1
        for field in ("Post URL", "Job URL"):
            cell = sheet.cell(rownum, col[field])
            if cell.value and canonical(cell.value):
                cell.hyperlink = cell.value
                cell.font = Font(name="Aptos", size=10, color="0563C1", underline="single")
    navy = "16324F"
    for i, width in enumerate(WIDTHS, 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    for c in sheet[1]:
        c.fill = PatternFill("solid", fgColor=navy)
        c.font = Font(name="Aptos", size=10, bold=True, color="FFFFFF")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 28
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.freeze_panes = "D2"
    sheet.auto_filter.ref = f"A1:X{max(1, sheet.max_row)}"
    if not sheet.data_validations.dataValidation:
        dv = DataValidation(type="list", formula1='"Not contacted,Messaged,Replied,Follow-up,Closed"', allow_blank=True)
        sheet.add_data_validation(dv)
        dv.add(f"S2:S1048576")
    temporary = path.with_name(path.stem + ".tmp.xlsx")
    try:
        book.save(temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"added": added, "updated": updated, "skipped": skipped, "workbook": str(path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--workbook", default="Hiring_Leads.xlsx",
                        help="Existing tracker.xlsx or a new workbook; adds Hiring Leads only.")
    parser.add_argument("--print-queries", action="store_true", help="Print search strings and browser URLs.")
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--import-csv", help="Import manually gathered public post URLs and excerpts.")
    parser.add_argument("--import-json", help="Import a list of post objects.")
    parser.add_argument("--add-post", help="Add one public post URL for review.")
    parser.add_argument("--title", default="", help="Vacancy title for --add-post.")
    parser.add_argument("--snippet", default="", help="Public post excerpt for --add-post.")
    parser.add_argument("--search-brave", action="store_true", help="Use official Brave Search API.")
    parser.add_argument("--include-excluded", action="store_true")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    queries = generate_queries(config, args.max_queries)
    if args.print_queries:
        for query in queries:
            print(query)
            print("https://www.google.com/search?q=" + quote_plus(query))
            print()
    candidates = []
    for source in (args.import_csv, args.import_json):
        if source:
            candidates.extend(import_records(source))
    if args.add_post:
        candidates.append({"post_url": args.add_post, "title": args.title, "snippet": args.snippet,
                           "source": "Manual entry"})
    if args.search_brave:
        key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
        if not key:
            parser.error("Set BRAVE_SEARCH_API_KEY in your local environment before --search-brave")
        for index, query in enumerate(queries, 1):
            try:
                rows = get_brave_results(query, key, int(config.get("posted_within_days", 7)))
                candidates.extend(rows)
                print(f"[{index}/{len(queries)}] {len(rows)} public search results")
            except Exception as exc:
                print(f"[{index}/{len(queries)}] Search failed: {type(exc).__name__}: {exc}")
    if not candidates:
        if not args.print_queries:
            parser.error("Use --import-csv, --import-json, --add-post, --search-brave or --print-queries")
        return 0
    leads = [classify(row, config) for row in candidates]
    results = save_leads(args.workbook, leads, args.include_excluded)
    print(f"Hiring Leads: {results['added']} added, {results['updated']} refreshed, "
          f"{results['skipped']} filtered. Workbook: {results['workbook']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
