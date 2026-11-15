"""Fetches trusted market news, asks Gemini to filter/categorize/explain it, writes news.json.
Also refreshes tickers.json (company-name list for search) once a week."""
import json, os, re, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-flash-lite-latest"       # main model (fast, light)
BACKUP_MODEL = "gemini-flash-latest"     # used if the main model stays busy
MAX_CANDIDATES = 40   # articles sent to Gemini
MAX_ITEMS = 30        # articles kept after the stocks-only filter
UA = {"User-Agent": "Mozilla/5.0 (personal stock news project)"}
SEC_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
NASDAQ_URLS = ["https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
                "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"]
SKIP_NAME = re.compile(r"warrant|\brights?\b|\bunits?\b|preferred|notes due|debenture|subordinated|% ", re.I)

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
ACTIONS = ["Buy", "Hold", "Sell", "Watch"]

# Used only if Gemini is down: keeps items that look market-related.
FINANCE_WORDS = ["stock", "shares", "market", "fed ", "federal reserve", "rate", "inflation",
                 "earnings", "s&p", "nasdaq", "dow", "oil", "treasury", "econom", "tariff",
                 "bank", "ipo", "merger", "acquisition", "chip", "revenue", "profit", "wall street"]

items = {}


def get(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
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


def looks_financial(x):
    if x["tickers"]:
        return True
    text = (x["headline"] + " " + x["raw"]).lower()
    return any(w in text for w in FINANCE_WORDS)


def analyze(lst):
    """Ask Gemini to filter, categorize, explain and suggest. Returns {} on failure."""
    if not GEMINI_KEY or not lst:
        return {}
    payload = [{"id": i, "headline": x["headline"], "text": x["raw"][:300], "tickers": x["tickers"]}
               for i, x in enumerate(lst)]
    prompt = (
        "You are a careful markets explainer for a beginner US retail investor with a mixed, "
        "mostly long-term style. Use ONLY the headline and text supplied. Never invent facts, "
        "numbers or causes. If something is unclear, say so.\n"
        "relevant: true only if the item is news about a specific public company or stock, a market "
        "sector or index, commodities or currencies that move markets, central banks, the economy, or "
        "policy that affects markets. false for personal-finance questions or anecdotes, retirement or "
        "budgeting advice, lifestyle, real-estate tips, opinion pieces about someone's personal "
        "situation, and anything not mainly about markets.\n"
        f"For relevant items also give: category (exactly one of {CATEGORIES}), summary (one sentence, "
        "your own words), meaning (1-2 plain sentences on why it might matter to this investor), "
        "importance (1 low to 5 high), and a cautious suggestion: action = Buy, Hold, Sell or Watch, "
        "only when the item is about ONE specific listed company, with its ticker and a reason "
        "(one sentence using only facts in the text). Prefer Watch or Hold when the evidence is thin. "
        'For general market or economy news use action "None" with empty ticker and reason. '
        "Never promise returns or predict prices.\n"
        "digest: 5 short bullets covering the biggest themes among the relevant items.\n"
        'Return JSON only: {"digest":[...],"items":[{"id":0,"relevant":true,"category":"","summary":"",'
        '"meaning":"","importance":3,"action":"None","ticker":"","reason":""}]}\n'
        "ITEMS:\n" + json.dumps(payload)
    )
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
    for model in [MODEL, BACKUP_MODEL]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(3):
            try:
                raw = get(url, json.dumps(body).encode(),
                          {"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY},
                          timeout=120)
                text = json.loads(raw)["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
            except Exception as e:
                print("Gemini failed:", model, "attempt", attempt + 1, e)
                time.sleep(20)
    return {}


def build_items(lst, result):
    """Merge Gemini's answers into the articles, dropping anything not about markets."""
    by_id = {}
    for x in result.get("items", []):
        try:
            by_id[int(x["id"])] = x
        except Exception:
            pass
    ai_ok = bool(by_id)
    out = []
    for i, x in enumerate(lst):
        ai = by_id.get(i)
        if ai is not None and ai.get("relevant") is False:
            continue
        if ai is None and not looks_financial(x):
            continue
        ai = ai or {}
        cat = ai.get("category") if ai.get("category") in CATEGORIES else "Markets"
        act = ai.get("action") if ai.get("action") in ACTIONS else ""
        try:
            imp = int(ai.get("importance", 2))
        except Exception:
            imp = 2
        out.append({
            "headline": x["headline"], "url": x["url"], "source": x["source"], "time": x["time"],
            "tickers": x["tickers"], "category": cat,
            "summary": ai.get("summary") or x["raw"][:200],
            "meaning": ai.get("meaning", "") if ai_ok else "",
            "importance": min(5, max(1, imp)),
            "action": act,
            "action_ticker": (str(ai.get("ticker") or "").upper()[:6]) if act else "",
            "reason": (ai.get("reason") or "") if act else "",
        })
        if len(out) >= MAX_ITEMS:
            break
    return out


def clean_name(n):
    n = re.sub(r"\s*/[A-Za-z]{2,}/?\s*$", "", (n or "").strip()).strip(" ,")
    return n.title() if n.isupper() else n


def parse_sec(j):
    """Turn the SEC company list into [[TICKER, Name], ...] for NYSE and Nasdaq."""
    rows = []
    if isinstance(j, dict) and "fields" in j and "data" in j:
        f = j["fields"]
        ti, ni = f.index("ticker"), f.index("name")
        ei = f.index("exchange") if "exchange" in f else None
        for r in j["data"]:
            ex = (r[ei] or "") if ei is not None else ""
            if ei is not None and ex.lower() not in ("nasdaq", "nyse"):
                continue
            rows.append((r[ti], r[ni]))
    elif isinstance(j, dict):
        for v in j.values():
            if isinstance(v, dict) and "ticker" in v:
                rows.append((v["ticker"], v.get("title", "")))
    seen, out = set(), []
    for t, n in rows:
        t = (t or "").upper().replace("-", ".")
        if not re.fullmatch(r"[A-Z][A-Z.]{0,5}", t) or t in seen:
            continue
        seen.add(t)
        out.append([t, clean_name(n)])
    return out


def parse_nasdaq_trader(texts):
    """Parse Nasdaq's public symbol files into [[TICKER, Name], ...]."""
    out, seen = [], set()
    for text in texts:
        lines = text.splitlines()
        if not lines:
            continue
        head = lines[0].split("|")
        name_i = head.index("Security Name")
        test_i = head.index("Test Issue") if "Test Issue" in head else None
        for line in lines[1:]:
            if line.startswith("File Creation Time"):
                continue
            p = line.split("|")
            if len(p) <= name_i:
                continue
            if test_i is not None and len(p) > test_i and p[test_i].strip() == "Y":
                continue
            t, name = p[0].strip().upper(), p[name_i].strip()
            if SKIP_NAME.search(name) or not re.fullmatch(r"[A-Z][A-Z.]{0,5}", t) or t in seen:
                continue
            seen.add(t)
            out.append([t, re.split(r"\s+-\s+", name)[0].strip()])
    return out


def refresh_tickers():
    """Save the company-name list (for the My stocks search) once a week."""
    try:
        with open("tickers.json", encoding="utf-8") as f:
            old = json.load(f)
        if old.get("data") and time.time() - old.get("fetched", 0) < 7 * 86400:
            return
    except Exception:
        pass
    errors, data = [], []
    try:
        data = parse_nasdaq_trader([get(u, headers=UA) for u in NASDAQ_URLS])
        if len(data) < 500:
            raise ValueError(f"only {len(data)} companies found")
    except Exception as e:
        errors.append("Nasdaq Trader: " + repr(e))
        data = []
    if not data:
        owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "stocknews")
        headers = {"User-Agent": f"StockNewsPersonal {owner}@users.noreply.github.com"}
        try:
            data = parse_sec(json.loads(get(SEC_URL, headers=headers)))
            if len(data) < 500:
                raise ValueError(f"only {len(data)} companies found")
        except Exception as e:
            errors.append("SEC: " + repr(e))
            data = []
    if data:
        with open("tickers.json", "w", encoding="utf-8") as f:
            json.dump({"fetched": int(time.time()), "data": data}, f, ensure_ascii=False,
                      separators=(",", ":"))
        if os.path.exists("tickers_error.txt"):
            os.remove("tickers_error.txt")
        print("Saved", len(data), "companies to tickers.json")
    else:
        with open("tickers_error.txt", "w", encoding="utf-8") as f:
            f.write("Company list failed:\n" + "\n".join(errors) + "\n")
        print("Company list failed:", errors)


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
            for it in list(root.iter("item"))[:10]:
                title = it.findtext("title") or ""
                desc = re.sub(r"<[^>]+>", "", it.findtext("description") or "")
                pub = it.findtext("pubDate")
                ts = parsedate_to_datetime(pub).timestamp() if pub else time.time()
                add(title, (it.findtext("link") or "").strip(), name, ts, desc, tag(title + " " + desc))
        except Exception as e:
            print("Feed failed:", name, e)

    lst = sorted(items.values(), key=lambda x: x["time"], reverse=True)[:MAX_CANDIDATES]
    result = analyze(lst)
    out_items = build_items(lst, result)

    out = {"updated": int(time.time()), "universe": list(TICKERS), "categories": CATEGORIES,
           "digest": result.get("digest", []), "items": out_items}
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("Wrote", len(out_items), "items from", len(lst), "candidates")

    refresh_tickers()


if __name__ == "__main__":
    main()
