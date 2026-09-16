#!/usr/bin/env python3
"""Personal Summer 2027 enterprise-tech internship radar.

Fetches configured public JSON feeds, normalizes common schemas, rejects obvious
out-of-season/non-target postings, scores roles against config.json, suppresses
already-applied roles, and writes LATEST.md + data/matches.json.
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
USER_AGENT = "Ian5555-internship-radar/3.0"


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
        "date": normalize_date(date.strip()),
        "url": url.strip(),
        "description": description.strip(),
        "source": source,
    }


def canon(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def contains(text: str, keyword: str) -> bool:
    return canon(keyword) in canon(text)


def normalize_date(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip()
    if re.fullmatch(r"\d{10}", s):
        try:
            return datetime.fromtimestamp(int(s), timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return s
    if re.fullmatch(r"\d{13}", s):
        try:
            return datetime.fromtimestamp(int(s) / 1000, timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return s
    iso = re.match(r"(20\d{2}-\d{2}-\d{2})", s)
    if iso:
        return iso.group(1)
    return s


def parse_date(date_text: str) -> datetime | None:
    try:
        return datetime.strptime(date_text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


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


def has_explicit_summer_2027(job: dict[str, str]) -> bool:
    full = " ".join([job["title"], job["description"], job["url"]]).lower()
    patterns = [
        r"\bsummer[-\s_/]*2027\b",
        r"\b2027[-\s_/]*summer\b",
        r"\bsummer[-\s_/]*intern(?:ship)?[-\s_/]*2027\b",
        r"\b2027[-\s_/]*intern(?:ship)?\b",
    ]
    return any(re.search(p, full) for p in patterns)


def hard_reject(job: dict[str, str], cfg: dict[str, Any]) -> bool:
    title = job["title"].lower()
    full = " ".join([job["title"], job["description"], job["url"]]).lower()

    if any(contains(title, k) for k in cfg.get("hard_reject_title_keywords", [])):
        return True

    # Reject explicit conflicting terms anywhere in title/description/URL.
    if re.search(r"\bsummer[-\s_/]*2026\b", full):
        return True
    if re.search(r"\b(spring|winter|fall)[-\s_/]*2027\b", full) and "summer 2027" not in full:
        return True
    if "2026" in full and "2027" not in full:
        return True

    # Remove graduate-only and clearly off-target engineering/hardware roles.
    reject_phrases = [
        "phd", "doctoral", "graduate intern", "grad intern",
        "hardware test", "engine test", "ic test", "design for test", "analog validation",
        "digital verification", "device engineer", "fpga", "asic", "semiconductor test",
        "reservoir engineer", "electrical engineer", "clock design", "hbm", "dram cell",
        "forward deployed engineer", "autonomy", "intelligent systems engineering",
        "geospatial data", "credit risk intern", "markets credit", "category technology"
    ]
    if any(contains(title, k) for k in reject_phrases):
        return True

    if not any(k in title for k in ["intern", "internship"]):
        return True

    # Strict mode: if the listing never explicitly identifies itself as Summer 2027,
    # only keep it when it is extremely likely to be a current 2027 campus role.
    if cfg.get("require_explicit_summer_2027", False) and not has_explicit_summer_2027(job):
        return True

    # Only keep U.S.-based roles for this personal feed.
    location = job["location"].lower()
    foreign_markers = ["canada", "toronto", "ontario", "vancouver", "montreal", "united kingdom", "london, uk"]
    if any(m in location for m in foreign_markers) and not any(us in location for us in ["usa", "united states", " tx", "texas", "remote in usa", "remote, u.s."]):
        return True

    return False


def score(job: dict[str, str], cfg: dict[str, Any]) -> tuple[int, list[str], str]:
    haystack = " ".join([job["title"], job["location"], job["description"]]).lower()
    title = job["title"].lower()
    score_value = 0
    reasons: list[str] = []
    category = "Other"

    best_weight = -10**9
    family_weights = cfg.get("family_weights", {})
    matched_family = False
    for family, keywords in cfg["target_keywords"].items():
        hits = sum(1 for k in keywords if contains(haystack, k))
        if hits:
            matched_family = True
            weight = int(family_weights.get(family, 20)) + min(8, (hits - 1) * 2)
            score_value += weight
            if weight > best_weight:
                best_weight = weight
                category = family.replace("_", " ").title()
            reasons.append(f"{family.replace('_', ' ')} match")

    # Generic roles are allowed only as lower-priority fallback matches.
    if not matched_family:
        score_value += cfg["scores"].get("other_role_penalty", -30)
        reasons.append("outside primary target families")

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

    if has_explicit_summer_2027(job):
        score_value += cfg["scores"]["summer_2027"]
        reasons.append("Summer 2027")

    posted = parse_date(job["date"])
    if posted:
        age_days = (datetime.now(timezone.utc) - posted).days
        if 0 <= age_days <= 14:
            score_value += cfg["scores"].get("recent_14_days", 0)
            reasons.append("NEW: posted <=14 days")
        elif 0 <= age_days <= 30:
            score_value += cfg["scores"].get("recent_30_days", 0)
            reasons.append("recent: posted <=30 days")

    if any(contains(title, k) for k in cfg.get("swe_keywords", [])):
        score_value += cfg["scores"]["swe_penalty"]
        reasons.append("SWE-heavy penalty")

    if any(contains(title, k) for k in cfg.get("data_keywords", [])):
        score_value += cfg["scores"].get("data_penalty", 0)
        reasons.append("generic data-role penalty")

    if category == "Product":
        score_value += cfg["scores"].get("product_penalty", 0)
        reasons.append("product secondary-lane penalty")

    if any(contains(haystack, k) for k in cfg["negative_keywords"]):
        score_value += cfg["scores"]["negative_penalty"]
        reasons.append("excluded-specialty penalty")

    return score_value, reasons, category


def dedupe(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: list[tuple[str, str]] = []
    for job in jobs:
        company = canon(job["company"])
        title = canon(job["title"])
        duplicate = False
        for sc, st in seen:
            company_match = company == sc or company in sc or sc in company
            title_match = title == st or title in st or st in title
            if company_match and title_match:
                duplicate = True
                break
        if duplicate:
            continue
        seen.append((company, title))
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
        "Strict Summer 2027 feed. DFW/North Texas, Texas, and remote roles receive location boosts; corporate IT, QA/testing, systems/support, analyst, IT risk/audit, and consulting roles outrank generic product/data roles.",
        "",
        "| Score | Company | Role | Location | Category | Posted | Freshness | Apply | Source |",
        "|---:|---|---|---|---|---|---|---|---|",
    ]
    for job in matches:
        link = f"[Apply]({job['url']})" if job["url"] else "—"
        freshness = "NEW" if any(r.startswith("NEW:") for r in job.get("reasons", [])) else ("Recent" if any(r.startswith("recent:") for r in job.get("reasons", [])) else "—")
        lines.append(
            f"| **{job['score']}** | {md_escape(job['company']) or '—'} | "
            f"{md_escape(job['title']) or '—'} | {md_escape(job['location']) or '—'} | "
            f"{md_escape(job['category'])} | {md_escape(job['date']) or '—'} | {freshness} | {link} | "
            f"{md_escape(job['source'])} |"
        )
    if not matches:
        lines.extend(["", "No matches cleared the current score threshold on this run."])
    if errors:
        lines.extend(["", "## Source status", ""])
        lines.extend(f"- ⚠️ {e}" for e in errors)
    lines.extend([
        "",
        "> Discovery feed only. Verify that the employer posting is still open and that you meet its requirements before applying.",
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
                if hard_reject(job, cfg):
                    continue
                value, reasons, category = score(job, cfg)
                job.update({"score": value, "reasons": reasons, "category": category})
                if value >= cfg.get("minimum_score", 40) and not applied(job, known):
                    all_jobs.append(job)
        except Exception as exc:
            errors.append(f"{source['name']}: {type(exc).__name__}: {exc}")

    matches = dedupe(sorted(all_jobs, key=lambda x: (-x["score"], x["company"].lower(), x["title"].lower())))
    matches = matches[: int(cfg.get("max_results", 80))]

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
