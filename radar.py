#!/usr/bin/env python3
"""Personal Summer 2027 enterprise-tech internship radar.

Fetches configured public JSON feeds, normalizes common schemas, scores roles against
config.json, suppresses already-applied roles, and writes LATEST.md + data/matches.json.
Uses only the Python standard library.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
APPLIED_PATH = ROOT / "data" / "known_applied.json"
MATCHES_PATH = ROOT / "data" / "matches.json"
LATEST_PATH = ROOT / "LATEST.md"
USER_AGENT = "Ian5555-internship-radar/1.0"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def first(item: dict[str, Any], keys: list[str], default: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            if isinstance(value, list):
                return ", ".join(str(v) for v in value)
            if isinstance(value, dict):
                return ", ".join(str(v) for v in value.values())
            return str(value)
    return default


def find_url(item: dict[str, Any]) -> str:
    direct = first(item, ["url", "apply_url", "application_url", "link", "job_url", "external_url"])
    if direct.startswith("http"):
        return direct
    for value in item.values():
        if isinstance(value, str) and value.startswith("http"):
            return value
    return ""


def flatten_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ["internships", "jobs", "listings", "data", "results", "postings"]:
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        # Some feeds group listings by category/company.
        records: list[dict[str, Any]] = []
        for value in payload.values():
            if isinstance(value, list):
                records.extend(x for x in value if isinstance(x, dict))
        return records
    return []


def normalize(item: dict[str, Any], source: str) -> dict[str, str]:
    company = first(item, ["company", "company_name", "employer", "organization", "name"])
    title = first(item, ["title", "role", "position", "job_title", "internship_title"])
    location = first(item, ["location", "locations", "city", "office", "work_location"])
    date = first(item, ["date_posted", "posted", "posted_date", "date", "created_at", "updated_at"])
    url = find_url(item)
    description = first(item, ["description", "summary", "details", "category"])
    return {
        "company": company.strip(),
        "title": title.strip(),
        "location": location.strip(),
        "date": date.strip(),
        "url": url.strip(),
        "description": description.strip(),
        "source": source,
    }


def canon(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def contains(text: str, keyword: str) -> bool:
    return canon(keyword) in canon(text)


def applied(job: dict[str, str], known: list[dict[str, str]]) -> bool:
    jc = canon(job["company"])
    jt = canon(job["title"])
    for entry in known:
        ec = canon(str(entry.get("company", "")))
        er = canon(str(entry.get("role", "")))
        company_match = ec and (ec in jc or jc in ec)
        role_match = not er or er in jt or jt in er
        if company_match and role_match:
            return True
    return False


def score(job: dict[str, str], cfg: dict[str, Any]) -> tuple[int, list[str], str]:
    haystack = " ".join([job["title"], job["location"], job["description"]]).lower()
    title = job["title"].lower()
    score_value = 0
    reasons: list[str] = []
    category = "Other"

    best_hits = 0
    for family, keywords in cfg["target_keywords"].items():
        hits = sum(1 for k in keywords if contains(haystack, k))
        if hits:
            score_value += cfg["scores"]["target_keyword"] + min(10, (hits - 1) * 3)
            if hits > best_hits:
                best_hits = hits
                category = family.replace("_", " ").title()
            reasons.append(f"{family.replace('_', ' ')} match")

    loc = job["location"].lower()
    if any(k in loc for k in cfg["location_priority"]["dfw"]):
        score_value += cfg["scores"]["dfw"]
        reasons.append("DFW / North Texas")
    elif any(k in loc for k in cfg["location_priority"]["texas"]):
        score_value += cfg["scores"]["texas"]
        reasons.append("Texas")
    elif any(k in loc for k in cfg["location_priority"]["remote"]):
        score_value += cfg["scores"]["remote"]
        reasons.append("Remote")

    if "2027" in haystack or "2027" in job["date"]:
        score_value += cfg["scores"]["summer_2027"]
        reasons.append("2027")

    if any(contains(title, k) for k in cfg["swe_keywords"]):
        # A QA/product/etc. title can still survive if it has strong positive matches.
        score_value += cfg["scores"]["swe_penalty"]
        reasons.append("SWE-heavy penalty")

    if any(contains(haystack, k) for k in cfg["negative_keywords"]):
        score_value += cfg["scores"]["negative_penalty"]
        reasons.append("excluded-specialty penalty")

    return score_value, reasons, category


def dedupe(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    output = []
    for job in jobs:
        key = (canon(job["company"]), canon(job["title"]), canon(job["location"]))
        if key in seen:
            continue
        seen.add(key)
        output.append(job)
    return output


def md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(matches: list[dict[str, Any]], errors: list[str]) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Latest Internship Radar",
        "",
        f"Last refreshed: **{generated}**",
        "",
        "Sorted by personalized fit score. DFW / North Texas, Texas, and remote roles receive location boosts.",
        "",
        "| Score | Company | Role | Location | Category | Posted | Apply | Source |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for job in matches:
        link = f"[Apply]({job['url']})" if job["url"] else "—"
        lines.append(
            f"| **{job['score']}** | {md_escape(job['company']) or '—'} | "
            f"{md_escape(job['title']) or '—'} | {md_escape(job['location']) or '—'} | "
            f"{md_escape(job['category'])} | {md_escape(job['date']) or '—'} | {link} | "
            f"{md_escape(job['source'])} |"
        )
    if not matches:
        lines.extend(["", "No matches cleared the current score threshold on this run."])
    if errors:
        lines.extend(["", "## Source status", ""])
        lines.extend(f"- ⚠️ {e}" for e in errors)
    lines.extend([
        "",
        "> This is a discovery feed, not a guarantee that a posting is still open or that every requirement is met. Verify the employer posting before applying.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    cfg = load_json(CONFIG_PATH, {})
    known = load_json(APPLIED_PATH, [])
    all_jobs: list[dict[str, Any]] = []
    errors: list[str] = []

    for source in cfg.get("sources", []):
        try:
            payload = fetch_json(source["url"])
            records = flatten_records(payload)
            if not records:
                errors.append(f"{source['name']}: feed loaded but no recognizable records were found")
                continue
            for record in records:
                job = normalize(record, source["name"])
                if not job["title"] and not job["company"]:
                    continue
                value, reasons, category = score(job, cfg)
                job.update({"score": value, "reasons": reasons, "category": category})
                if value >= cfg.get("minimum_score", 20) and not applied(job, known):
                    all_jobs.append(job)
        except Exception as exc:
            errors.append(f"{source['name']}: {type(exc).__name__}: {exc}")

    matches = dedupe(sorted(all_jobs, key=lambda x: (-x["score"], x["company"].lower(), x["title"].lower())))
    ROOT.joinpath("data").mkdir(exist_ok=True)
    with MATCHES_PATH.open("w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "count": len(matches), "matches": matches, "errors": errors}, f, indent=2)
        f.write("\n")
    LATEST_PATH.write_text(render_markdown(matches, errors), encoding="utf-8")
    print(f"Wrote {len(matches)} matches to {MATCHES_PATH.relative_to(ROOT)} and LATEST.md")
    for error in errors:
        print(f"WARNING: {error}")


if __name__ == "__main__":
    main()
