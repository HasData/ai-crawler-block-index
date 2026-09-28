"""Test 1: Per-bot enforcement penalty.

Extends the GPTBot vs browser test to ClaudeBot, PerplexityBot, CCBot,
and Meta-ExternalAgent. Same sample of publisher domains, same timing,
per-domain sticky UA (each UA is tested against each domain).

Vantage: single machine (residential IP). Absolute rates differ from
datacenter, but relative pass-rates per-bot are meaningful.

Sample: 250 news-homepages publishers (the segment where blocking is strongest).
"""

import csv
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from urllib.parse import urlparse

RAW = Path("raw")  # fieldwork inputs, see README
OUT = Path("out")
OUT.mkdir(parents=True, exist_ok=True)

# Official published user-agents per each bot's docs
USER_AGENTS = {
    "browser": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "gptbot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; "
        "GPTBot/1.2; +https://openai.com/gptbot)"
    ),
    "claudebot": (
        "Mozilla/5.0 (Linux; Android 8.0) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/98.0.4757.132 Mobile Safari/537.36 (compatible; ClaudeBot/1.0; "
        "+claudebot@anthropic.com)"
    ),
    "perplexitybot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; "
        "PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)"
    ),
    "ccbot": "CCBot/2.0 (https://commoncrawl.org/faq/)",
    "meta-externalagent": (
        "meta-externalagent/1.1 (+https://developers.facebook.com/docs/"
        "sharing/webmasters/crawler)"
    ),
}

SAMPLE_SIZE = 250
TIMEOUT = 15
MAX_WORKERS = 6


def load_publisher_domains():
    domains = []
    with open(RAW / "inputs/news_homepages_sites.csv", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = row.get("url", "").strip()
            if not url:
                continue
            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")
            if domain:
                domains.append((domain, url.rstrip("/")))
    return domains


def classify(status, body_snippet):
    if status is None:
        return "error"
    if status in (200, 206):
        # Cloudflare challenge often 200 with "Just a moment" or "cf-mitigated"
        if body_snippet and ("cf-mitigated" in body_snippet.lower()
                             or "just a moment" in body_snippet.lower()
                             or "hcaptcha" in body_snippet.lower()
                             or "recaptcha" in body_snippet.lower()):
            return "challenge"
        return "ok"
    if status in (401, 403, 451):
        return "blocked"
    if status == 402:
        return "blocked-payment"
    if status == 429:
        return "ratelimited"
    if status >= 500:
        return "server_error"
    return f"other-{status}"


def probe(domain, url, ua_label, ua):
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": ua, "Accept": "*/*"},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        body_snippet = resp.text[:2000] if resp.text else ""
        return {
            "domain": domain,
            "ua_label": ua_label,
            "status": resp.status_code,
            "class": classify(resp.status_code, body_snippet),
            "final_url": resp.url,
        }
    except requests.exceptions.RequestException as e:
        return {
            "domain": domain,
            "ua_label": ua_label,
            "status": None,
            "class": "error",
            "error": str(e)[:200],
        }


def main():
    all_domains = load_publisher_domains()
    print(f"Loaded {len(all_domains)} publisher domains")

    random.seed(42)
    sample = random.sample(all_domains, min(SAMPLE_SIZE, len(all_domains)))
    print(f"Sampled {len(sample)} for per-bot enforcement test\n")

    tasks = []
    for domain, url in sample:
        for ua_label, ua in USER_AGENTS.items():
            tasks.append((domain, url, ua_label, ua))

    print(f"Total probes: {len(tasks)}")
    print(f"Workers: {MAX_WORKERS}")

    results = []
    start = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(probe, d, u, l, ua): (d, l) for d, u, l, ua in tasks}
        done = 0
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            done += 1
            if done % 100 == 0:
                elapsed = time.time() - start
                rate = done / elapsed
                remain = (len(tasks) - done) / rate if rate > 0 else 0
                print(f"  {done}/{len(tasks)}  rate={rate:.1f} req/s  eta={remain:.0f}s")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.0f}s. Total: {len(results)} probes.\n")

    # Aggregate per UA
    from collections import Counter, defaultdict
    by_ua = defaultdict(Counter)
    by_ua_status = defaultdict(Counter)
    for r in results:
        by_ua[r["ua_label"]][r["class"]] += 1
        by_ua_status[r["ua_label"]][r.get("status")] += 1

    print("=" * 90)
    print(f"{'UA':<20} {'Total':>7} {'OK':>7} {'Blocked':>9} {'Chal.':>7} {'Rate':>6} {'Err':>6} {'OK %':>7} {'Blk %':>7}")
    for ua_label in USER_AGENTS:
        counts = by_ua[ua_label]
        total = sum(counts.values())
        ok = counts.get("ok", 0)
        blocked = counts.get("blocked", 0) + counts.get("blocked-payment", 0)
        chal = counts.get("challenge", 0)
        rate = counts.get("ratelimited", 0)
        err = counts.get("error", 0)
        ok_pct = 100.0 * ok / total if total else 0
        blk_pct = 100.0 * blocked / total if total else 0
        print(f"{ua_label:<20} {total:>7} {ok:>7} {blocked:>9} {chal:>7} {rate:>6} {err:>6} {ok_pct:>6.1f}% {blk_pct:>6.1f}%")

    # Save
    summary = {
        "sample_size": len(sample),
        "total_probes": len(results),
        "elapsed_sec": round(elapsed, 1),
        "vantage": "residential (single machine, one exit IP)",
        "per_ua": {
            ua: dict(by_ua[ua])
            for ua in USER_AGENTS
        },
        "per_ua_status": {
            ua: {str(k): v for k, v in by_ua_status[ua].items()}
            for ua in USER_AGENTS
        },
    }
    (OUT / "per_bot_penalty.json").write_text(json.dumps(summary, indent=2))
    (OUT / "live_probes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in results)
    )
    print(f"\nSaved: {OUT / 'per_bot_penalty.json'}")


if __name__ == "__main__":
    main()
