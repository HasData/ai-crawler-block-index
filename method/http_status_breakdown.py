"""Test 3: HTTP status breakdown of GPTBot 'blocks'.

Re-tabulate existing enforcement data (job5_dc.jsonl) to categorize
what "blocked" actually looks like: 403 vs 429 vs Cloudflare challenge
vs blank body vs other.

Also joins with CDN data (job1_cdn.jsonl) for cross-tab.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

RAW = Path("raw")  # fieldwork inputs, see README
OUT = Path("out")
OUT.mkdir(parents=True, exist_ok=True)


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def main():
    # Load CDN mapping
    cdn_by_domain = {}
    for r in load_jsonl(RAW / "job1_cdn.jsonl"):
        cdn_by_domain[r["domain"]] = r.get("vendor", "none") or "none"

    # Load enforcement data
    total = 0
    gptbot_status_counter = Counter()
    gptbot_class_counter = Counter()
    browser_class_counter = Counter()

    by_cdn_gptbot = defaultdict(Counter)
    by_cdn_gptbot_class = defaultdict(Counter)

    challenged = 0
    excluded = 0

    for r in load_jsonl(RAW / "job5_dc.jsonl"):
        total += 1
        gptbot_status = r.get("enf_gptbot_dc_status")
        gptbot_class = r.get("enf_gptbot_dc_class")
        browser_class = r.get("enf_browser_dc_class")
        cdn = cdn_by_domain.get(r["domain"], "unknown")

        if r.get("enf_measurement_challenged"):
            challenged += 1

        if gptbot_class in ("error", None):
            excluded += 1
            continue

        gptbot_status_counter[gptbot_status] += 1
        gptbot_class_counter[gptbot_class] += 1
        browser_class_counter[browser_class] += 1

        by_cdn_gptbot[cdn][gptbot_class] += 1
        by_cdn_gptbot_class[cdn][gptbot_status] += 1

    # === TEST 3: HTTP status breakdown of GPTBot responses ===
    print("=" * 70)
    print("TEST 3: HTTP status breakdown of GPTBot responses (datacenter IP)")
    print("=" * 70)
    print(f"Total enforcement records: {total}")
    print(f"Excluded (proxy errors): {excluded}")
    print(f"Challenged (CAPTCHA/interstitial flagged): {challenged}")
    print()

    print("Class distribution:")
    tested = total - excluded
    for cls, n in gptbot_class_counter.most_common():
        pct = 100.0 * n / tested if tested else 0
        print(f"  {cls:15}  {n:4}  {pct:5.1f}%")
    print()

    print("Status code distribution (top 15):")
    for status, n in sorted(gptbot_status_counter.most_common(15), key=lambda x: -x[1]):
        pct = 100.0 * n / tested if tested else 0
        print(f"  {status!s:8}  {n:4}  {pct:5.1f}%")
    print()

    # === TEST 2: CDN-by-CDN enforcement ===
    print("=" * 70)
    print("TEST 2: GPTBot pass-rate per CDN vendor")
    print("=" * 70)
    print(f"{'CDN':<15} {'Total':>7} {'OK':>7} {'Blocked':>8} {'Chal.':>7} {'Rate':>7} {'OK %':>7} {'Blk %':>7} {'Chal %':>7}")
    for cdn, counts in sorted(by_cdn_gptbot.items(), key=lambda x: -sum(x[1].values())):
        n_total = sum(counts.values())
        if n_total < 20:
            continue
        ok = counts.get("ok", 0)
        blocked = counts.get("blocked", 0)
        challenged = counts.get("challenge", 0)
        rate = counts.get("ratelimited", 0)
        ok_pct = 100.0 * ok / n_total
        blk_pct = 100.0 * blocked / n_total
        chal_pct = 100.0 * challenged / n_total
        print(f"{cdn:<15} {n_total:>7} {ok:>7} {blocked:>8} {challenged:>7} {rate:>7} {ok_pct:>6.1f}% {blk_pct:>6.1f}% {chal_pct:>6.1f}%")

    # Save summary as JSON
    result = {
        "test3_http_status_breakdown": {
            "total_records": total,
            "tested_records": tested,
            "excluded_proxy_errors": excluded,
            "challenged_count": challenged,
            "class_distribution": dict(gptbot_class_counter),
            "status_distribution": dict(gptbot_status_counter),
        },
        "test2_cdn_enforcement": {
            cdn: {
                "total": sum(counts.values()),
                "ok": counts.get("ok", 0),
                "blocked": counts.get("blocked", 0),
                "challenge": counts.get("challenge", 0),
                "ratelimited": counts.get("ratelimited", 0),
                "ok_pct": round(100.0 * counts.get("ok", 0) / sum(counts.values()), 2) if sum(counts.values()) else 0,
                "blocked_pct": round(100.0 * counts.get("blocked", 0) / sum(counts.values()), 2) if sum(counts.values()) else 0,
                "challenge_pct": round(100.0 * counts.get("challenge", 0) / sum(counts.values()), 2) if sum(counts.values()) else 0,
            }
            for cdn, counts in by_cdn_gptbot.items()
            if sum(counts.values()) >= 20
        },
    }

    (OUT / "http_status_breakdown.json").write_text(json.dumps(result, indent=2))
    print(f"\nSaved: {OUT / 'http_status_breakdown.json'}")


if __name__ == "__main__":
    main()
