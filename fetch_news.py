"""Stock News updater.

Collects trusted market news from several free sources, asks Gemini to filter, categorize,
explain and flag likely winners/losers, keeps a 30-day archive, and writes:
  news.json     last 24 hours (what the page loads first)
  archive.json  everything from the last 30 days (loaded for the Past week / Past month tabs)
  tickers.json  company-name list for the My stocks search (refreshed weekly)
"""
import json, os, re, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-flash-lite-latest"       # main model (fast, light)
BACKUP_MODEL = "gemini-flash-latest"     # used if the main model stays busy

KEEP_DAYS = 30            # how long news is kept in the archive
DAY = 86400
MAX_ARCHIVE = 2500        # hard cap on saved stories
MAX_TODAY = 150           # stories shown on the Today tab
ANALYZE_BATCH = 30        # new stories sent to Gemini per run
GOOGLE_SLICE = int(os.environ.get("GOOGLE_SLICE", "50"))    # stocks checked on Google News per run
FINNHUB_SLICE = int(os.environ.get("FINNHUB_SLICE", "10"))  # stocks checked on Finnhub per run
UA = {"User-Agent": "Mozilla/5.0 (personal stock news project)"}
SEC_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
NASDAQ_URLS = ["https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
               "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"]
SKIP_NAME = re.compile(r"warrant|\brights?\b|\bunits?\b|preferred|notes due|debenture|subordinated|% ", re.I)

# TICKER|Name used to search|optional names to spot in headlines (separated by /)
UNIVERSE_TEXT = """
AAPL|Apple
MSFT|Microsoft
NVDA|Nvidia
GOOGL|Alphabet|Alphabet/Google
AMZN|Amazon
META|Meta Platforms|Meta
TSLA|Tesla
AVGO|Broadcom
ORCL|Oracle
AMD|AMD
INTC|Intel
QCOM|Qualcomm
TXN|Texas Instruments
MU|Micron
AMAT|Applied Materials
LRCX|Lam Research
KLAC|KLA Corporation|KLA
ASML|ASML
TSM|TSMC
ARM|Arm Holdings
MRVL|Marvell
ON|ON Semiconductor
NXPI|NXP Semiconductors
ADI|Analog Devices
MCHP|Microchip Technology
SMCI|Super Micro Computer|Super Micro
DELL|Dell Technologies|Dell
HPQ|HP Inc
HPE|Hewlett Packard Enterprise
IBM|IBM
CSCO|Cisco
ANET|Arista Networks
ADBE|Adobe
CRM|Salesforce
NOW|ServiceNow
INTU|Intuit
PANW|Palo Alto Networks
CRWD|CrowdStrike
FTNT|Fortinet
ZS|Zscaler
NET|Cloudflare
DDOG|Datadog
SNOW|Snowflake
MDB|MongoDB
PLTR|Palantir
SHOP|Shopify
UBER|Uber
LYFT|Lyft
ABNB|Airbnb
DASH|DoorDash
BKNG|Booking Holdings
EXPE|Expedia
PYPL|PayPal
XYZ|Block Inc
COIN|Coinbase
HOOD|Robinhood
SOFI|SoFi
AFRM|Affirm
NFLX|Netflix
DIS|Disney
WBD|Warner Bros. Discovery
PSKY|Paramount
CMCSA|Comcast
T|AT&T
VZ|Verizon
TMUS|T-Mobile
SPOT|Spotify
RBLX|Roblox
EA|Electronic Arts
TTWO|Take-Two Interactive|Take-Two
U|Unity Software
SONY|Sony
PINS|Pinterest
SNAP|Snap Inc
RDDT|Reddit
DUOL|Duolingo
TTD|Trade Desk
APP|AppLovin
ROKU|Roku
TWLO|Twilio
ZM|Zoom
DOCU|DocuSign
WDAY|Workday
TEAM|Atlassian
ADSK|Autodesk
SNPS|Synopsys
CDNS|Cadence Design
ACN|Accenture
SAP|SAP
GLW|Corning
WDC|Western Digital
STX|Seagate
NTAP|NetApp
PSTG|Pure Storage
ENPH|Enphase
FSLR|First Solar
RIVN|Rivian
LCID|Lucid
F|Ford Motor
GM|General Motors
NIO|NIO
TM|Toyota
JPM|JPMorgan Chase|JPMorgan
BAC|Bank of America
WFC|Wells Fargo
C|Citigroup
GS|Goldman Sachs
MS|Morgan Stanley
SCHW|Charles Schwab
BLK|BlackRock
BX|Blackstone
KKR|KKR
APO|Apollo Global
AXP|American Express
V|Visa|
MA|Mastercard
COF|Capital One
USB|U.S. Bancorp
PNC|PNC Financial
TFC|Truist
BK|BNY Mellon|BNY
STT|State Street
MET|MetLife
PRU|Prudential Financial
AIG|AIG
CB|Chubb
PGR|Progressive
TRV|Travelers
ALL|Allstate
AON|Aon
SPGI|S&P Global
MCO|Moody's
ICE|Intercontinental Exchange
CME|CME Group
NDAQ|Nasdaq Inc
BRK.B|Berkshire Hathaway
MSTR|MicroStrategy
MARA|MARA Holdings
RIOT|Riot Platforms
IBIT|iShares Bitcoin Trust
UNH|UnitedHealth
LLY|Eli Lilly
JNJ|Johnson & Johnson
PFE|Pfizer
MRK|Merck
ABBV|AbbVie
BMY|Bristol Myers Squibb
AMGN|Amgen
GILD|Gilead
REGN|Regeneron
VRTX|Vertex Pharmaceuticals|Vertex
MRNA|Moderna
BIIB|Biogen
NVO|Novo Nordisk
AZN|AstraZeneca
NVS|Novartis
SNY|Sanofi
GSK|GSK
TMO|Thermo Fisher
DHR|Danaher
ABT|Abbott Laboratories
MDT|Medtronic
ISRG|Intuitive Surgical
SYK|Stryker
BSX|Boston Scientific
EW|Edwards Lifesciences
ZTS|Zoetis
CVS|CVS Health
CI|Cigna
HUM|Humana
ELV|Elevance Health
HCA|HCA Healthcare
MCK|McKesson
CAH|Cardinal Health
BDX|Becton Dickinson
DXCM|Dexcom
HIMS|Hims & Hers
WMT|Walmart
COST|Costco
TGT|Target|
HD|Home Depot
LOW|Lowe's
NKE|Nike
SBUX|Starbucks
MCD|McDonald's
CMG|Chipotle
YUM|Yum Brands
KO|Coca-Cola
PEP|PepsiCo
PG|Procter & Gamble
CL|Colgate-Palmolive
KMB|Kimberly-Clark
MDLZ|Mondelez
KHC|Kraft Heinz
GIS|General Mills
HSY|Hershey
MO|Altria
PM|Philip Morris
STZ|Constellation Brands
BUD|Anheuser-Busch
LULU|Lululemon
TJX|TJX Companies
ROST|Ross Stores
DG|Dollar General
DLTR|Dollar Tree
BBY|Best Buy
EBAY|eBay
ETSY|Etsy
W|Wayfair
CHWY|Chewy
EL|Estee Lauder
ULTA|Ulta Beauty
DPZ|Domino's
MAR|Marriott
HLT|Hilton
CCL|Carnival
RCL|Royal Caribbean
NCLH|Norwegian Cruise Line
DAL|Delta Air Lines
UAL|United Airlines
AAL|American Airlines
LUV|Southwest Airlines
CVNA|Carvana
DKNG|DraftKings
MGM|MGM Resorts
LVS|Las Vegas Sands
WYNN|Wynn Resorts
GME|GameStop
AMC|AMC Entertainment
CELH|Celsius Holdings
KDP|Keurig Dr Pepper
MNST|Monster Beverage
TSN|Tyson Foods
XOM|Exxon Mobil|Exxon
CVX|Chevron
COP|ConocoPhillips
OXY|Occidental Petroleum|Occidental
SLB|Schlumberger
EOG|EOG Resources
PSX|Phillips 66
MPC|Marathon Petroleum
VLO|Valero
KMI|Kinder Morgan
WMB|Williams Companies
OKE|ONEOK
HAL|Halliburton
BKR|Baker Hughes
DVN|Devon Energy
FANG|Diamondback Energy
CEG|Constellation Energy
VST|Vistra
NEE|NextEra Energy
DUK|Duke Energy
SO|Southern Company
D|Dominion Energy
AEP|American Electric Power
GE|GE Aerospace
GEV|GE Vernova
CAT|Caterpillar
DE|Deere
BA|Boeing
LMT|Lockheed Martin
RTX|RTX
NOC|Northrop Grumman
GD|General Dynamics
HON|Honeywell
MMM|3M
UPS|UPS
FDX|FedEx
UNP|Union Pacific
CSX|CSX
NSC|Norfolk Southern
WM|Waste Management
ETN|Eaton
EMR|Emerson Electric
ITW|Illinois Tool Works
PH|Parker Hannifin
LIN|Linde
SHW|Sherwin-Williams
APD|Air Products
FCX|Freeport-McMoRan
NEM|Newmont
NUE|Nucor
DOW|Dow Inc
DD|DuPont
MOS|Mosaic
AA|Alcoa
MP|MP Materials
RKLB|Rocket Lab
ASTS|AST SpaceMobile
JOBY|Joby Aviation
OKLO|Oklo
SMR|NuScale Power|NuScale
IONQ|IonQ
RGTI|Rigetti Computing|Rigetti
TT|Trane Technologies
CARR|Carrier Global
JCI|Johnson Controls
LHX|L3Harris
TDG|TransDigm
ADP|ADP
PAYX|Paychex
CTAS|Cintas
FAST|Fastenal
ORLY|O'Reilly Automotive
AZO|AutoZone
GWW|Grainger
MSI|Motorola Solutions
KEYS|Keysight
AMT|American Tower
PLD|Prologis
EQIX|Equinix
O|Realty Income
SPG|Simon Property Group
WELL|Welltower
DLR|Digital Realty
CCI|Crown Castle
PSA|Public Storage
PDD|PDD Holdings
BABA|Alibaba
JD|JD.com
BIDU|Baidu
SE|Sea Limited
MELI|MercadoLibre
NU|Nu Holdings
CPNG|Coupang
GRAB|Grab Holdings
SPY|SPDR S&P 500 ETF|SPDR S&P 500
QQQ|Invesco QQQ
DIA|SPDR Dow Jones Industrial Average ETF
IWM|iShares Russell 2000 ETF
GLD|SPDR Gold Shares
TLT|iShares 20+ Year Treasury Bond ETF
XLE|Energy Select Sector SPDR
XLF|Financial Select Sector SPDR
XLK|Technology Select Sector SPDR
ARKK|ARK Innovation ETF
"""

TICKERS = {}      # ticker -> search name
TAG_NAMES = {}    # ticker -> names to look for in headlines
for _line in UNIVERSE_TEXT.strip().splitlines():
    _p = [x.strip() for x in _line.split("|")]
    TICKERS[_p[0]] = _p[1]
    TAG_NAMES[_p[0]] = [n for n in (_p[2].split("/") if len(_p) > 2 else [_p[1]]) if n]
UNIVERSE = list(TICKERS)


def _compile_tag_patterns():
    pats = {}
    for t in UNIVERSE:
        parts = [r"(?<![\w])" + re.escape(n) + r"(?![\w])" for n in TAG_NAMES[t]]
        parts.append(r"\$" + re.escape(t) + r"(?![\w])")
        parts.append(r"\b(?:NASDAQ|NYSE):\s*" + re.escape(t) + r"\b")
        if len(t) >= 4:
            parts.append(r"\b" + re.escape(t) + r"\b")
        pats[t] = re.compile("|".join(parts))
    return pats


TAG_PATTERNS = _compile_tag_patterns()

# Only sources containing one of these words are shown. Edit to tighten or widen.
TRUSTED = ["reuters", "associated press", "ap news", "bloomberg", "cnbc", "marketwatch",
           "wall street journal", "wsj", "barron", "financial times", "federal reserve"]

# General market feeds: (label, url)
RSS_FEEDS = [
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("Wall Street Journal", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"),
]

CATEGORIES = ["Earnings", "Fed & Economy", "Big Tech", "AI & Chips",
              "Regulation", "Deals", "Markets", "Company"]
ACTIONS = ["Buy", "Hold", "Sell", "Watch"]

FINANCE_RE = re.compile(
    r"\b(stocks?|shares|markets?|fed|federal reserve|rates?|inflation|earnings|s&p|nasdaq|dow|oil|"
    r"treasur(?:y|ies)|econom\w*|tariffs?|banks?|ipo|mergers?|acquisitions?|acquire[sd]?|chips?|"
    r"revenue|profits?|wall street|investors?|etfs?|crypto|bitcoin|gold|yields?|guidance|forecast|"
    r"downgrades?|upgrades?|analysts?|ceo|sec|antitrust|lawsuit|bankruptcy|dividend|buyback)\b", re.I)

# ---- state for one run ---------------------------------------------------
store = {}    # url -> story
skip = {}     # url (or "k:"+headline key) -> time, for stories Gemini marked as not about markets
by_key = {}   # headline key -> url, to merge the same story from different sources
now = 0


class RateLimited(Exception):
    pass


def get(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(url.split("?")[0])
        raise


def finnhub(path, **params):
    params["token"] = FINNHUB_KEY
    return json.loads(get("https://finnhub.io/api/v1/" + path + "?" + urllib.parse.urlencode(params)))


def trusted(src):
    s = (src or "").lower()
    return any(t in s for t in TRUSTED)


def norm_key(h):
    return re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()[:70]


def tag(text):
    return [t for t in UNIVERSE if TAG_PATTERNS[t].search(text)]


def looks_financial(headline, raw="", tickers=()):
    return bool(tickers) or bool(FINANCE_RE.search((headline or "") + " " + (raw or "")))


def add(headline, url, source, ts, text, tickers, force=False):
    """Add a story unless we already have it. force=True skips the keyword check."""
    headline = (headline or "").strip()
    url = (url or "").strip()
    if not headline or not url.startswith("http"):
        return
    ts = int(ts)
    if ts > now + 3600:
        ts = now
    if now - ts > KEEP_DAYS * DAY:
        return
    key = norm_key(headline)
    tickers = sorted(set(tickers))
    if url in store or url in skip or ("k:" + key) in skip:
        if url in store:
            store[url]["tickers"] = sorted(set(store[url]["tickers"]) | set(tickers))
        return
    if key in by_key and by_key[key] in store:
        old = store[by_key[key]]
        old["tickers"] = sorted(set(old["tickers"]) | set(tickers))
        return
    if not force and not looks_financial(headline, text, tickers):
        return
    store[url] = {"headline": headline, "url": url, "source": source, "time": ts,
                  "raw": (text or "").strip()[:300], "tickers": tickers, "ai": False}
    by_key[key] = url


# ---- sources -------------------------------------------------------------
def slice_of(cursor, size):
    n = len(UNIVERSE)
    size = min(size, n)
    return [UNIVERSE[(cursor + i) % n] for i in range(size)], (cursor + size) % n


def clean_related(rel):
    out = []
    for t in (rel or "").split(","):
        t = t.strip().upper()
        if re.fullmatch(r"[A-Z][A-Z.]{0,5}", t):
            out.append(t)
    return out[:4]


def src_finnhub_general():
    for a in finnhub("news", category="general")[:80]:
        if trusted(a.get("source")):
            text = a.get("summary") or ""
            add(a["headline"], a["url"], a["source"], a["datetime"], text,
                clean_related(a.get("related")) + tag(a["headline"] + " " + text))


def src_finnhub_slice(symbols):
    d2 = time.strftime("%Y-%m-%d", time.gmtime(now))
    d1 = time.strftime("%Y-%m-%d", time.gmtime(now - 2 * DAY))
    for t in symbols:
        try:
            got = [a for a in finnhub("company-news", symbol=t, **{"from": d1, "to": d2})
                   if trusted(a.get("source"))]
            for a in got[:4]:
                add(a["headline"], a["url"], a["source"], a["datetime"], a.get("summary"), [t], force=True)
        except RateLimited:
            print("Finnhub said too many requests; skipping the rest of its stocks this run")
            return
        except Exception as e:
            print("Finnhub company news failed for", t, e)
        time.sleep(1.1)  # stay under the free rate limit


def google_news(sym, name):
    q = f'"{name}" (stock OR shares) when:2d'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    root = ET.fromstring(get(url, headers=UA, timeout=30))
    kept = 0
    for it in root.iter("item"):
        src = (it.findtext("source") or "").strip()
        if not trusted(src):
            continue
        title = (it.findtext("title") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[:-len(src) - 3].strip()
        pub = it.findtext("pubDate")
        ts = parsedate_to_datetime(pub).timestamp() if pub else now
        add(title, (it.findtext("link") or "").strip(), src, ts, "", [sym] + tag(title), force=True)
        kept += 1
        if kept >= 6:
            break


def src_google_slice(symbols):
    for t in symbols:
        try:
            google_news(t, TICKERS[t])
        except (RateLimited, urllib.error.HTTPError) as e:
            print("Google News refused a request, skipping the rest this run:", e)
            return
        except Exception as e:
            print("Google News failed for", t, e)
        time.sleep(0.5)


def src_rss():
    for label, url in RSS_FEEDS:
        try:
            root = ET.fromstring(get(url, headers=UA, timeout=30))
            for it in list(root.iter("item"))[:12]:
                title = (it.findtext("title") or "").strip()
                desc = re.sub(r"<[^>]+>", "", it.findtext("description") or "")
                pub = it.findtext("pubDate")
                ts = parsedate_to_datetime(pub).timestamp() if pub else now
                add(title, (it.findtext("link") or "").strip(), label, ts, desc, tag(title + " " + desc))
        except Exception as e:
            print("Feed failed:", label, e)


# ---- AI step -------------------------------------------------------------
def analyze(batch, context):
    """Ask Gemini to filter, categorize, explain, suggest and flag likely winners/losers."""
    if not GEMINI_KEY or not batch:
        return {}
    payload = [{"id": i, "headline": x["headline"], "text": x["raw"][:300], "tickers": x["tickers"]}
               for i, x in enumerate(batch)]
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
        "impacts: for items with importance 3 or higher, up to 3 US-listed stocks or ETFs that the news "
        'likely helps (direction "up") or hurts (direction "down") in the near term, each with a short '
        "why (under 20 words) based only on the text. Only name a stock when the link is direct and "
        "clear. Use an empty list when unsure. Never promise returns or predict prices.\n"
        "digest: 5 short bullets covering the biggest themes across the new items and the context "
        "headlines below.\n"
        'Return JSON only: {"digest":[...],"items":[{"id":0,"relevant":true,"category":"","summary":"",'
        '"meaning":"","importance":3,"action":"None","ticker":"","reason":"",'
        '"impacts":[{"ticker":"","direction":"up","why":""}]}]}\n'
        "CONTEXT_HEADLINES: " + json.dumps(context) + "\n"
        "ITEMS:\n" + json.dumps(payload)
    )
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
    for model in [MODEL, BACKUP_MODEL]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(2):
            try:
                raw = get(url, json.dumps(body).encode(),
                          {"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY},
                          timeout=90)
                text = json.loads(raw)["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
            except Exception as e:
                print("Gemini failed:", model, "attempt", attempt + 1, e)
                time.sleep(15)
    return {}


def apply_results(batch, result):
    by_id = {}
    for x in result.get("items", []):
        try:
            by_id[int(x["id"])] = x
        except Exception:
            pass
    done = 0
    for i, x in enumerate(batch):
        ai = by_id.get(i)
        if ai is None:
            continue  # stays pending, tried again next run
        if ai.get("relevant") is False:
            store.pop(x["url"], None)
            skip[x["url"]] = now
            skip["k:" + norm_key(x["headline"])] = now
            continue
        act = ai.get("action") if ai.get("action") in ACTIONS else ""
        try:
            imp = int(ai.get("importance", 2))
        except Exception:
            imp = 2
        impacts = []
        for im in (ai.get("impacts") or [])[:3]:
            if not isinstance(im, dict):
                continue
            t = str(im.get("ticker", "")).upper().replace("$", "").strip()
            d = im.get("direction")
            if re.fullmatch(r"[A-Z][A-Z.]{0,5}", t) and d in ("up", "down"):
                impacts.append({"ticker": t, "direction": d, "why": str(im.get("why", ""))[:160]})
        x.update({
            "ai": True, "raw": "",
            "category": ai.get("category") if ai.get("category") in CATEGORIES else "Markets",
            "summary": str(ai.get("summary") or "")[:300],
            "meaning": str(ai.get("meaning") or "")[:400],
            "importance": min(5, max(1, imp)),
            "action": act,
            "action_ticker": str(ai.get("ticker") or "").upper()[:6] if act else "",
            "reason": str(ai.get("reason") or "")[:300] if act else "",
            "impacts": impacts,
        })
        done += 1
    return done


# ---- output --------------------------------------------------------------
def public(x):
    if x.get("ai"):
        out = {k: x.get(k, "") for k in ("headline", "url", "source", "time", "tickers", "category",
                                         "summary", "meaning", "importance", "action",
                                         "action_ticker", "reason", "impacts")}
        out["ai"] = True
        return out
    return {"headline": x["headline"], "url": x["url"], "source": x["source"], "time": x["time"],
            "tickers": x["tickers"], "category": "Markets", "summary": x.get("raw", "")[:200],
            "meaning": "", "importance": 2, "action": "", "action_ticker": "", "reason": "",
            "impacts": [], "ai": False}


def visible(x):
    return x.get("ai") or looks_financial(x["headline"], x.get("raw", ""), x["tickers"])


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ---- company list for the My stocks search ---------------------------------
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
    old = load_json("tickers.json", {})
    if old.get("data") and time.time() - old.get("fetched", 0) < 7 * DAY:
        return
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
        write_json("tickers.json", {"fetched": int(time.time()), "data": data})
        if os.path.exists("tickers_error.txt"):
            os.remove("tickers_error.txt")
        print("Saved", len(data), "companies to tickers.json")
    else:
        with open("tickers_error.txt", "w", encoding="utf-8") as f:
            f.write("Company list failed:\n" + "\n".join(errors) + "\n")
        print("Company list failed:", errors)


# ---- main ----------------------------------------------------------------
def main():
    global store, skip, by_key, now
    now = int(time.time())
    arch = load_json("archive.json", {})
    store = {}
    for x in arch.get("items", []):
        if x.get("url") and x.get("headline"):
            x.setdefault("ai", False)
            x.setdefault("tickers", [])
            x.setdefault("raw", "")
            store[x["url"]] = x
    skip = {k: v for k, v in (arch.get("skip") or {}).items() if now - v < KEEP_DAYS * DAY}
    by_key = {norm_key(x["headline"]): u for u, x in store.items()}
    cursor = arch.get("cursor") or {}
    g_cur, f_cur = int(cursor.get("g", 0)), int(cursor.get("f", 0))
    start_count = len(store)

    if FINNHUB_KEY:
        try:
            src_finnhub_general()
        except RateLimited:
            print("Finnhub said too many requests; skipping it this run")
        except Exception as e:
            print("Finnhub general news failed:", e)
        syms, f_cur = slice_of(f_cur, FINNHUB_SLICE)
        src_finnhub_slice(syms)
    syms, g_cur = slice_of(g_cur, GOOGLE_SLICE)
    src_google_slice(syms)
    src_rss()
    new_count = len(store) - start_count

    # AI step: only stories that have not been analyzed yet, newest first
    pending = [x for x in store.values() if not x["ai"] and now - x["time"] < DAY]
    pending.sort(key=lambda x: (bool(x["tickers"]), x["time"]), reverse=True)
    batch = pending[:ANALYZE_BATCH]
    context = [x["headline"] for x in sorted(
        (y for y in store.values() if y["ai"] and now - y["time"] < DAY),
        key=lambda y: (y.get("importance", 2), y["time"]), reverse=True)[:10]]
    result = analyze(batch, context)
    done = apply_results(batch, result) if result else 0
    digest = result.get("digest") if isinstance(result.get("digest"), list) else None
    if digest:
        arch["digest"] = [str(d)[:300] for d in digest][:6]
        arch["digest_time"] = now

    # tidy up: drop old stories and keep the archive a sensible size
    for u in [u for u, x in store.items() if now - x["time"] > KEEP_DAYS * DAY]:
        del store[u]
    if len(store) > MAX_ARCHIVE:
        keep = sorted(store.values(), key=lambda x: x["time"], reverse=True)[:MAX_ARCHIVE]
        store = {x["url"]: x for x in keep}

    items = [x for x in store.values() if visible(x)]
    items.sort(key=lambda x: x["time"], reverse=True)
    today = [public(x) for x in items if now - x["time"] < DAY][:MAX_TODAY]

    write_json("archive.json", {
        "updated": now, "cursor": {"g": g_cur, "f": f_cur}, "skip": skip,
        "digest": arch.get("digest", []), "digest_time": arch.get("digest_time", 0),
        "items": [dict(public(x), **({"raw": x["raw"]} if not x["ai"] else {})) for x in items],
    })
    write_json("news.json", {
        "updated": now, "universe": UNIVERSE, "categories": CATEGORIES,
        "digest": arch.get("digest", []), "items": today,
    })
    pending_left = len([x for x in store.values() if not x["ai"] and now - x["time"] < DAY])
    print(f"Wrote {len(today)} items for today; {len(items)} in the archive; "
          f"{new_count} new this run; {done} analyzed; {pending_left} waiting for AI notes")

    refresh_tickers()


if __name__ == "__main__":
    main()

