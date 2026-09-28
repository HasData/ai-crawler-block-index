"""Test 4: AI Overview / AI Mode citations vs GPTBot blocking.

Hypothesis: Google AI Overview/AI Mode uses Google-Extended (separate directive),
not GPTBot. Sites that block GPTBot in robots.txt may still appear in AI Overview.

Method:
1. Load blocking status per domain from job234 (blocks_any_ai + n_ai_blocked)
2. Query Google AI Mode for 10 general news queries
3. Extract cited domains from each AI Mode response
4. Cross-tab: cited domains that block AI crawlers vs those that don't
"""

import csv
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()
HASDATA_API_KEY = os.getenv("HASDATA_API_KEY")

RAW = Path("raw")  # fieldwork inputs, see README
OUT = Path("out")
OUT.mkdir(parents=True, exist_ok=True)

AI_MODE_URL = "https://api.hasdata.com/scrape/google/ai-mode"

QUERIES = [
    "latest US politics news 2026",
    "climate change news this week",
    "AI regulation latest updates",
    "Ukraine war current status",
    "US stock market news today",
    "supreme court ruling recent",
    "US economy outlook 2026",
    "tech industry layoffs 2026",
    "healthcare news 2026",
    "election results 2026",
]


def load_blocking_map():
    """Load per-domain AI blocking status from job234."""
    blocking = {}
    with open(RAW / "job234_ads_robots_llms.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            blocking[r["domain"]] = {
                "blocks_any_ai": r.get("blocks_any_ai", False),
                "n_ai_blocked": r.get("n_ai_blocked", 0),
            }
    return blocking


def extract_domain(url):
    if not url:
        return None
    try:
        d = urlparse(url).netloc.lower()
        if d.startswith("www."):
            d = d[4:]
        return d
    except Exception:
        return None


def query_ai_mode(query):
    """Query HasData Google AI Mode API."""
    resp = requests.get(
        AI_MODE_URL,
        params={"q": query, "gl": "us", "hl": "en"},
        headers={"x-api-key": HASDATA_API_KEY, "Content-Type": "application/json"},
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()


def extract_citations(ai_mode_response):
    """Pull cited URLs / domains from an AI Mode response."""
    citations = []
    # HasData AI Mode API returns citations under 'references' at top level
    for ref in ai_mode_response.get("references", []) or []:
        url = ref.get("link") or ref.get("url")
        if url:
            citations.append(url)
    return citations


def main():
    print(f"Loading blocking map from job234 ...")
    blocking = load_blocking_map()
    print(f"Loaded {len(blocking)} domain blocking records")
    blockers = sum(1 for v in blocking.values() if v["blocks_any_ai"])
    print(f"  {blockers} domains block at least one AI crawler in robots.txt\n")

    all_citations = []
    per_query = {}

    for i, query in enumerate(QUERIES, 1):
        print(f"[{i}/{len(QUERIES)}] AI Mode: {query!r}")
        try:
            resp = query_ai_mode(query)
        except Exception as e:
            print(f"  ERROR: {e}")
            per_query[query] = {"error": str(e)}
            continue

        citations = extract_citations(resp)
        cited_domains = set()
        for url in citations:
            d = extract_domain(url)
            if d:
                cited_domains.add(d)

        print(f"  citations: {len(citations)}  unique domains: {len(cited_domains)}")

        per_query[query] = {
            "citations_total": len(citations),
            "unique_domains": sorted(cited_domains),
            "raw_response_keys": list(resp.keys())[:10],
        }
        all_citations.append({"query": query, "domains": sorted(cited_domains)})

    # Cross-tab
    all_cited_domains = Counter()
    for entry in all_citations:
        for d in entry["domains"]:
            all_cited_domains[d] += 1

    print(f"\n=== Cross-tab: cited domains vs blocking status ===")
    blocks_and_cited = []
    not_blocks_and_cited = []
    unknown = []
    for domain, freq in all_cited_domains.most_common():
        # Match on suffix (e.g., "www.nytimes.com" → look for "nytimes.com" in blocking)
        matched = blocking.get(domain)
        if matched is None:
            # Try suffix match
            for base, info in blocking.items():
                if domain.endswith("." + base) or domain == base:
                    matched = info
                    break

        if matched is None:
            unknown.append((domain, freq))
        elif matched["blocks_any_ai"]:
            blocks_and_cited.append((domain, freq, matched["n_ai_blocked"]))
        else:
            not_blocks_and_cited.append((domain, freq))

    print(f"\nCited AND blocking AI crawlers: {len(blocks_and_cited)} domains")
    for d, f, n in sorted(blocks_and_cited, key=lambda x: -x[1]):
        print(f"  {f}x cited | {n} AI bots blocked | {d}")

    print(f"\nCited AND NOT blocking AI crawlers: {len(not_blocks_and_cited)} domains")
    for d, f in sorted(not_blocks_and_cited, key=lambda x: -x[1])[:20]:
        print(f"  {f}x cited | {d}")

    print(f"\nCited but not in our sample (unknown): {len(unknown)} domains")
    for d, f in sorted(unknown, key=lambda x: -x[1])[:20]:
        print(f"  {f}x cited | {d}")

    # Save
    summary = {
        "queries": QUERIES,
        "total_unique_cited_domains": len(all_cited_domains),
        "cited_and_blocks_ai": [
            {"domain": d, "citations": f, "n_ai_blocked": n}
            for d, f, n in sorted(blocks_and_cited, key=lambda x: -x[1])
        ],
        "cited_and_no_block": [
            {"domain": d, "citations": f}
            for d, f in sorted(not_blocks_and_cited, key=lambda x: -x[1])
        ],
        "cited_not_in_sample": [
            {"domain": d, "citations": f}
            for d, f in sorted(unknown, key=lambda x: -x[1])
        ],
        "per_query": per_query,
    }
    (OUT / "ai_overview_vs_blocking.json").write_text(json.dumps(summary, indent=2))
    print(f"\nSaved: {OUT / 'ai_overview_vs_blocking.json'}")


if __name__ == "__main__":
    main()
