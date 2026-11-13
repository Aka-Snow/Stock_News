"""Fetches trusted market news, asks Gemini to categorize/explain it, writes news.json."""
import json, os, re, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-flash-latest"  # if Gemini errors, check the model name in Google AI Studio
MAX_ITEMS = 30
UA = {"User-Agent": "Mozilla/5.0 (personal stock news project)"}

# Default coverage: ticker -> name used to spot it in headlines. Edit freely.
TICKERS = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "GOOGL": "Google",
    "AMZN": "Amazon", "META": "Meta", "TSLA": "Tesla", "AVGO": "Broadcom",
    "JPM": "JPMorgan", "V": "Visa", "UNH": "UnitedHealth", "XOM": "Exxon",
    "LLY": "Eli Lilly", "WMT": "Walmart", "COST": "Costco", "NFLX": "Netflix",
    "AMD": "AMD", "ORCL": "Oracle", "BAC": "Bank of America", "DIS": "Disney",
    "INTC": "Intel", "PLTR": "Palantir", "KO": "Coca-Cola", "PFE": "Pfizer",
}

# Only sources containing one of these words are shown. Edit to tighten or widen.
TRUSTED = ["reuters", "associated press", "ap news", "bloomberg", "cnbc", "marketwatch",
           "wall street journal", "wsj", "barron", "financial times", "federal reserve"]

RSS_FEEDS = [
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
]

CATEGORIES = ["Earnings", "Fed & Economy", "Big Tech", "AI & Chips",
              "Regulation", "Deals", "Markets", "Company"]

items = {}


def get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=240) as r:
        return r.read().decode("utf-8", "replace")


def finnhub(path, **params):
    params["token"] = FINNHUB_KEY
    return json.loads(get("https://finnhub.io/api/v1/" + path + "?" + urllib.parse.urlencode(params)))


def trusted(src):
    s = (src or "").lower()
    return any(t in s for t in TRUSTED)


def tag(text):
    found = []
    for t, name in TICKERS.items():
        if re.search(r"\b" + re.escape(name) + r"\b", text) or (
            len(t) >= 3 and re.search(r"\b" + t + r"\b", text)
        ):
            found.append(t)
    return found


def add(headline, url, source, ts, text, tickers):
    if not headline or not (url or "").startswith("http"):
        return
    if url in items:
        items[url]["tickers"] = sorted(set(items[url]["tickers"]) | set(tickers))
        return
    items[url] = {"headline": headline.strip(), "url": url, "source": source,
                  "time": int(ts), "raw": (text or "").strip(), "tickers": sorted(set(tickers))}


def analyze(lst):
    """Ask Gemini for categories, plain-English meaning and a digest. Returns {} on failure."""
    if not GEMINI_KEY or not lst:
        return {}
    payload = [{"id": i, "headline": x["headline"], "text": x["raw"][:300], "tickers": x["tickers"]}
               for i, x in enumerate(lst)]
    prompt = (
        "You explain market news to a beginner US retail investor with a mixed, mostly long-term style. "
        "Use ONLY the headline and text supplied. Never invent facts, numbers or causes. "
        "If something is unclear, say so. Do not give buy/sell advice or price predictions.\n"
        f"Categories (pick exactly one per item): {CATEGORIES}.\n"
        "For each item write: summary (one sentence, your own words), "
        "meaning (1-2 plain sentences on why it might matter to this investor), "
        "importance (1 low to 5 high).\n"
        "Also write digest: 5 short bullets covering the biggest themes across all items.\n"
        'Return JSON only: {"digest":[...],"items":[{"id":0,"category":"","summary":"","meaning":"","importance":3}]}\n'
        "ITEMS:\n" + json.dumps(payload)
    )
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    try:
        raw = get(url, json.dumps(body).encode(),
                  {"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY})
        text = json.loads(raw)["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except Exception as e:
        print("Gemini failed:", e)
        return {}


def main():
    now = datetime.now(timezone.utc)
    d2 = now.date().isoformat()
    d1 = (now - timedelta(days=2)).date().isoformat()

    if FINNHUB_KEY:
        try:
            for a in finnhub("news", category="general")[:60]:
                if trusted(a.get("source")):
                    rel = [t for t in (a.get("related") or "").split(",") if t]
                    add(a["headline"], a["url"], a["source"], a["datetime"], a.get("summary"),
                        rel + tag(a["headline"]))
        except Exception as e:
            print("General news failed:", e)
        for t in TICKERS:
            try:
                got = [a for a in finnhub("company-news", symbol=t, **{"from": d1, "to": d2})
                       if trusted(a.get("source"))]
                for a in got[:4]:
                    add(a["headline"], a["url"], a["source"], a["datetime"], a.get("summary"), [t])
            except Exception as e:
                print("Company news failed for", t, e)
            time.sleep(1.2)  # stay under the free rate limit

    for name, url in RSS_FEEDS:
        try:
            root = ET.fromstring(get(url, headers=UA))
            for it in list(root.iter("item"))[:8]:
                title = it.findtext("title") or ""
                desc = re.sub(r"<[^>]+>", "", it.findtext("description") or "")
                pub = it.findtext("pubDate")
                ts = parsedate_to_datetime(pub).timestamp() if pub else time.time()
                add(title, (it.findtext("link") or "").strip(), name, ts, desc, tag(title + " " + desc))
        except Exception as e:
            print("Feed failed:", name, e)

    lst = sorted(items.values(), key=lambda x: x["time"], reverse=True)[:MAX_ITEMS]
    result = analyze(lst)
    by_id = {int(x["id"]): x for x in result.get("items", []) if "id" in x}

    out_items = []
    for i, x in enumerate(lst):
        ai = by_id.get(i, {})
        cat = ai.get("category") if ai.get("category") in CATEGORIES else "Markets"
        out_items.append({
            "headline": x["headline"], "url": x["url"], "source": x["source"], "time": x["time"],
            "tickers": x["tickers"], "category": cat,
            "summary": ai.get("summary") or x["raw"][:200],
            "meaning": ai.get("meaning", ""),
            "importance": ai.get("importance", 2),
        })

    out = {"updated": int(time.time()), "universe": list(TICKERS), "categories": CATEGORIES,
           "digest": result.get("digest", []), "items": out_items}
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("Wrote", len(out_items), "items")


main()
