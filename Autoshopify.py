"""
shopify-checker — optimized for Railway Pro
run: python api.py
deps: pip install aiohttp aiohttp-socks
"""
import asyncio
import aiohttp
import json
import re
import random
import logging
import time
import threading
import os
import traceback
import sys
import uuid
import queue
from typing import Optional, Dict, Any
from urllib.parse import urlparse, parse_qs
from decimal import Decimal
from aiohttp import web
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener

try:
    from aiohttp_socks import ProxyConnector, ProxyType
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False


# ═══════════════════════════════════════════════════════════════════════════
# ⚠️  GRAPHQL QUERIES — PASTE YOUR ORIGINAL ONES HERE (UNCHANGED) ⚠️
# ═══════════════════════════════════════════════════════════════════════════
# Copy from your original file:
#   QUERY_PROPOSAL_SHIPPING = """..."""
#   QUERY_PROPOSAL_DELIVERY = QUERY_PROPOSAL_SHIPPING
#   MUTATION_SUBMIT        = """..."""
#   QUERY_POLL             = """..."""
# ═══════════════════════════════════════════════════════════════════════════


# =====================================================================
# PRECOMPILED REGEX
# =====================================================================
_RE_SCHEME          = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*)://(.*)$")
_RE_OP_NAME         = re.compile(r"(?:query|mutation)\s+(\w+)")
_RE_PAYMENTS_CODE   = re.compile(r"(PAYMENTS_[A-Z_]+)")
_RE_CARD_CODE       = re.compile(r"(CARD_[A-Z_]+)")
_RE_TRIPLE_CODE     = re.compile(r"([A-Z]+_[A-Z]+_[A-Z_]+)")
_RE_DOUBLE_CODE     = re.compile(r"([A-Z]+_[A-Z]+)")
_RE_CODE_FIELD      = re.compile(r'code["\']?\s*[:=]\s*["\']?([^"\',]+)["\']?', re.IGNORECASE)
_RE_COMMIT_SHA      = re.compile(r'"commitSha"\s*:\s*"([a-f0-9]{40})"')
_RE_IDENT_SIG       = re.compile(r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"')
_RE_HEX32           = re.compile(r"[a-f0-9]{32,}")
_RE_PRICE           = re.compile(r'"price":\s*"([\d.]+)"')
_RE_CHECKOUT_CN     = re.compile(r"/checkouts/cn/([^/?]+)")
_RE_NON_DIGIT       = re.compile(r"\D")
_RE_WS              = re.compile(r"\s")


# =====================================================================
# LOGGING (async via queue — non-blocking)
# =====================================================================
LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_JSON = os.getenv("LOG_JSON", "false").lower() in ("1", "true", "yes")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
os.makedirs(LOG_DIR, exist_ok=True)


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k in ("request_id", "site"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        return json.dumps(payload)


logger = logging.getLogger("shopify-checker")
logger.setLevel(LOG_LEVEL)
logger.handlers.clear()

_formatter = _JsonFormatter() if LOG_JSON else logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
)
_fh = RotatingFileHandler(os.path.join(LOG_DIR, "checker.log"),
                          maxBytes=10_000_000, backupCount=3)
_fh.setFormatter(_formatter)
_fh.setLevel(LOG_LEVEL)
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_formatter)
_ch.setLevel(LOG_LEVEL)

_log_queue: "queue.Queue" = queue.Queue(-1)
_queue_handler = QueueHandler(_log_queue)
logger.addHandler(_queue_handler)

_listener = QueueListener(_log_queue, _fh, _ch, respect_handler_level=True)
_listener.start()
logger.propagate = False


# =====================================================================
# VARIANT CACHE (debounced disk writes)
# =====================================================================
VARIANT_CACHE_FILE = os.getenv("VARIANT_CACHE_FILE", "variant_cache.json")
_CACHE_TTL = int(os.getenv("VARIANT_CACHE_TTL", "3600"))
_variant_cache: Dict[str, dict] = {}
_cache_lock = threading.Lock()
_cache_dirty = threading.Event()
_cache_thread_started = False
_cache_thread_lock = threading.Lock()


def load_variant_cache():
    global _variant_cache
    try:
        if os.path.exists(VARIANT_CACHE_FILE):
            with open(VARIANT_CACHE_FILE, "r") as f:
                _variant_cache = json.load(f)
            logger.info(f"loaded {len(_variant_cache)} entries from variant cache")
    except Exception as e:
        logger.warning(f"failed to load variant cache: {e}")
        _variant_cache = {}


def save_variant_cache():
    try:
        with _cache_lock:
            snapshot = dict(_variant_cache)
        tmp = VARIANT_CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp, VARIANT_CACHE_FILE)
    except Exception as e:
        logger.warning(f"failed to save variant cache: {e}")


def _cache_save_worker():
    while True:
        _cache_dirty.wait()
        time.sleep(3)          # debounce: wait for quiet period
        _cache_dirty.clear()
        save_variant_cache()


def _ensure_cache_thread():
    global _cache_thread_started
    if _cache_thread_started:
        return
    with _cache_thread_lock:
        if _cache_thread_started:
            return
        t = threading.Thread(target=_cache_save_worker, daemon=True, name="cache-saver")
        t.start()
        _cache_thread_started = True


def get_cached_variant(domain):
    domain = domain.rstrip("/")
    with _cache_lock:
        entry = _variant_cache.get(domain)
        if entry and time.time() - entry.get("timestamp", 0) < _CACHE_TTL:
            return (entry.get("variant_id"), entry.get("price"),
                    entry.get("requires_shipping"), entry.get("handle"))
    return None, None, None, None


def set_cached_variant(domain, variant_id, price, requires_shipping, handle=None):
    domain = domain.rstrip("/")
    with _cache_lock:
        _variant_cache[domain] = {
            "variant_id": variant_id,
            "price": price,
            "requires_shipping": requires_shipping,
            "handle": handle or "cached",
            "timestamp": time.time(),
        }
    _ensure_cache_thread()
    _cache_dirty.set()


load_variant_cache()


# =====================================================================
# METRICS
# =====================================================================
_metrics_lock = threading.Lock()
_active_checkouts = 0


def _increment_active():
    global _active_checkouts
    with _metrics_lock:
        _active_checkouts += 1


def _decrement_active():
    global _active_checkouts
    with _metrics_lock:
        _active_checkouts -= 1


# =====================================================================
# ADDRESS BOOK + IDENTITY
# =====================================================================
C2C = {"USD": "US", "CAD": "CA", "INR": "IN", "AED": "AE", "HKD": "HK",
       "GBP": "GB", "CHF": "CH", "AUD": "AU"}

book = {
    "US": {"address1": "123 Main", "city": "NY", "postalCode": "10080", "zoneCode": "NY", "countryCode": "US", "phone": "2194157586"},
    "CA": {"address1": "88 Queen", "city": "Toronto", "postalCode": "M5J2J3", "zoneCode": "ON", "countryCode": "CA", "phone": "4165550198"},
    "GB": {"address1": "221B Baker Street", "city": "London", "postalCode": "NW1 6XE", "zoneCode": "LND", "countryCode": "GB", "phone": "2079460123"},
    "IN": {"address1": "221B MG", "city": "Mumbai", "postalCode": "400001", "zoneCode": "MH", "countryCode": "IN", "phone": "+91 9876543210"},
    "AE": {"address1": "Burj Tower", "city": "Dubai", "postalCode": "", "zoneCode": "DU", "countryCode": "AE", "phone": "+971 50 123 4567"},
    "HK": {"address1": "Nathan 88", "city": "Kowloon", "postalCode": "", "zoneCode": "KL", "countryCode": "HK", "phone": "+852 5555 5555"},
    "CH": {"address1": "Gotthardstrasse 17", "city": "Schweiz", "postalCode": "6430", "zoneCode": "SZ", "countryCode": "CH", "phone": "445512345"},
    "AU": {"address1": "1 Martin Place", "city": "Sydney", "postalCode": "2000", "zoneCode": "NSW", "countryCode": "AU", "phone": "291234567"},
    "DEFAULT": {"address1": "123 Main", "city": "New York", "postalCode": "10080", "zoneCode": "NY", "countryCode": "US", "phone": "2194157586"},
}


def pick_addr(url, currency=None):
    currency = (currency or "").upper()
    if currency in C2C and C2C[currency] in book:
        return book[C2C[currency]]
    tld = urlparse(url).netloc.split(".")[-1].upper()
    if tld in book:
        return book[tld]
    return book["DEFAULT"]


FIRST_NAMES = ["James", "John", "Robert", "Michael", "William", "David", "Mary", "Patricia", "Jennifer", "Linda"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez"]
EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "protonmail.com"]


def get_random_name():
    return random.choice(FIRST_NAMES), random.choice(LAST_NAMES)


def generate_email(first, last):
    return f"{first.lower()}.{last.lower()}@{random.choice(EMAIL_DOMAINS)}"


# =====================================================================
# PROXY
# =====================================================================
def _is_port(value):
    return value.isdigit() and 1 <= int(value) <= 65535


def parse_proxy(proxy_str):
    if not proxy_str:
        return None
    raw = proxy_str.strip()
    if not raw:
        return None

    scheme = "http"
    body = raw
    m = _RE_SCHEME.match(raw)
    if m:
        scheme = m.group(1).lower()
        body = m.group(2)

    user, password = None, None
    if "@" in body:
        auth, body = body.rsplit("@", 1)
        if ":" in auth:
            user, password = auth.split(":", 1)
        else:
            user = auth

    parts = body.split(":")
    if len(parts) == 2:
        host, port = parts
    elif len(parts) == 4:
        p0, p1, p2, p3 = parts
        if _is_port(p1):
            host, port, user, password = p0, p1, p2, p3
        elif _is_port(p3):
            user, password, host, port = p0, p1, p2, p3
        else:
            host, port, user, password = p0, p1, p2, p3
    elif len(parts) == 3:
        host, port, user = parts
        password = ""
    else:
        return None

    if not host or not _is_port(port):
        return None

    port_int = int(port)
    if user:
        url = f"{scheme}://{user}:{password or ''}@{host}:{port_int}"
    else:
        url = f"{scheme}://{host}:{port_int}"

    return {"url": url, "scheme": scheme, "host": host, "port": port_int, "user": user, "pass": password}


def proxy_is_socks(proxy):
    return bool(proxy) and proxy["scheme"] in ("socks4", "socks5", "socks5h")


def proxy_for_aiohttp(proxy):
    if not proxy or proxy_is_socks(proxy):
        return None
    return proxy["url"]


def make_connector(proxy):
    """Connector with tight timeouts + pooling. Cached per-proxy at SessionPool level."""
    if proxy and proxy_is_socks(proxy) and HAS_SOCKS:
        ptype = {
            "socks4": ProxyType.SOCKS4,
            "socks5": ProxyType.SOCKS5,
            "socks5h": ProxyType.SOCKS5,
        }[proxy["scheme"]]
        try:
            return ProxyConnector(
                proxy_type=ptype, host=proxy["host"], port=proxy["port"],
                username=proxy["user"], password=proxy["pass"] or "",
                limit=300, enable_cleanup_closed=True,
            )
        except TypeError:
            return ProxyConnector(
                proxy_type=ptype, host=proxy["host"], port=proxy["port"],
                username=proxy["user"], password=proxy["pass"] or "",
            )
    return aiohttp.TCPConnector(
        ssl=False,
        force_close=False,
        enable_cleanup_closed=True,
        limit=300,
        limit_per_host=100,
        ttl_dns_cache=300,
        keepalive_timeout=30,
    )


def mask_proxy_for_log(proxy_str):
    if not proxy_str:
        return None
    try:
        s = proxy_str
        if "://" in s:
            s = s.split("://", 1)[1]
        if "@" in s:
            s = s.split("@", 1)[1]
        return s.split(":", 1)[0]
    except Exception:
        return proxy_str[:30]


# =====================================================================
# SESSION POOL (reuse sessions + connections per proxy)
# =====================================================================
class SessionPool:
    def __init__(self):
        self._sessions: Dict[str, aiohttp.ClientSession] = {}
        self._lock = asyncio.Lock()

    async def get(self, proxy: Optional[dict]) -> aiohttp.ClientSession:
        key = proxy["url"] if proxy else "__direct__"
        async with self._lock:
            sess = self._sessions.get(key)
            if sess is not None and not sess.closed:
                return sess
            connector = make_connector(proxy)
            timeout = aiohttp.ClientTimeout(total=25, connect=10, sock_read=15)
            sess = aiohttp.ClientSession(connector=connector, timeout=timeout)
            self._sessions[key] = sess
            return sess

    async def close_all(self):
        for sess in list(self._sessions.values()):
            if not sess.closed:
                await sess.close()
        self._sessions.clear()


session_pool = SessionPool()


# =====================================================================
# HELPERS
# =====================================================================
def extract_between(text, start, end):
    if not text or not start or not end or start not in text:
        return None
    try:
        parts = text.split(start, 1)
        if len(parts) > 1 and end in parts[1]:
            return parts[1].split(end, 1)[0]
    except Exception:
        pass
    return None


def find_first(text, *patterns):
    if not text:
        return None
    for start, end in patterns:
        if not start or not end or start not in text:
            continue
        tail = text.split(start, 1)[1]
        if end in tail:
            result = tail.split(end, 1)[0]
            if result:
                return result
    return None


def is_captcha_required(text):
    if not text:
        return False
    lower = text.lower()
    return any(i in lower for i in
               ("CAPTCHA_REQUIRED", "hcaptcha", "cf-browser-verification", "just a moment"))


def is_dead_store(html):
    if not html:
        return False
    lower = html.lower()
    return any(i in lower for i in (
        "this store is currently unavailable",
        "store is currently unavailable",
        "reactivate your store",
        "store is unavailable",
    ))


def extract_clean_response(message):
    if not message:
        return "UNKNOWN_ERROR"
    message = str(message)
    for rx in (_RE_PAYMENTS_CODE, _RE_CARD_CODE, _RE_TRIPLE_CODE,
               _RE_DOUBLE_CODE, _RE_CODE_FIELD):
        m = rx.search(message)
        if m:
            token = m.group(1)
            if "_" in token:
                return token.strip("{}:'\" ")
    return message[:200]


def mask_card(card):
    s = _RE_NON_DIGIT.sub("", card or "")
    if len(s) >= 10:
        return f"{s[:6]}******{s[-4:]}"
    return "***"


def get_operation_name(query_str):
    m = _RE_OP_NAME.search(query_str)
    return m.group(1) if m else None


async def post_with_retry(session, url, data=None, json=None, headers=None,
                          proxy=None, max_retries=2, backoff=0.5):
    for attempt in range(max_retries):
        try:
            async with session.post(url, data=data, json=json, headers=headers, proxy=proxy) as resp:
                if resp.status == 429:
                    await asyncio.sleep(backoff * (2 ** attempt) + random.uniform(0, 1))
                    continue
                return resp
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(backoff * (2 ** attempt))
    return None


# =====================================================================
# PRODUCT FETCH  (GraphQL-first, fewer round-trips)
# =====================================================================
async def fetch_products(domain, proxy_str=None, force_refresh=False):
    domain = domain.rstrip("/")
    if not domain.startswith("http"):
        domain = "https://" + domain

    if not force_refresh:
        vid, price, ship, handle = get_cached_variant(domain)
        if vid is not None:
            logger.info(f"cache hit {domain} variant={vid} price={price}")
            return {
                "site": domain, "price": price, "variant_id": vid,
                "link": f"{domain}/products/{handle or 'cached'}",
                "requires_shipping": ship, "handle": handle or "cached",
            }, None

    proxy = parse_proxy(proxy_str) if proxy_str else None
    proxy_url = proxy_for_aiohttp(proxy)
    session = await session_pool.get(proxy)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": domain + "/",
    }

    # ── 1) REST first page (fast, single request) ────────────────────
    all_variants = []
    try:
        url = f"{domain}/products.json?limit=250&page=1"
        async with session.get(url, headers=headers, proxy=proxy_url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                for p in data.get("products", []):
                    handle = p.get("handle", "product")
                    for v in p.get("variants", []):
                        if v.get("available", True):
                            try:
                                price = float(v.get("price", "0").replace(",", ""))
                                vid = str(v.get("id", ""))
                                if vid and price > 0:
                                    all_variants.append({
                                        "id": vid, "price": price, "handle": handle,
                                        "requires_shipping": v.get("requires_shipping", True),
                                    })
                            except (ValueError, TypeError):
                                continue
    except Exception as e:
        logger.debug(f"REST page1 failed: {e}")

    # ── 2) If nothing yet, try GraphQL (single request) ──────────────
    if not all_variants:
        try:
            query = """
            query Products($first:Int!) {
              products(first:$first) {
                edges { node { id title handle variants(first:50) {
                  edges { node { id priceV2 { amount currencyCode } availableForSale requiresShipping } }
                } } }
              }
            }
            """
            async with session.post(
                f"{domain}/api/2024-01/graphql.json",
                json={"query": query, "variables": {"first": 50}},
                headers={"Content-Type": "application/json", **headers},
                proxy=proxy_url, timeout=8,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "errors" not in data:
                        for edge in data.get("data", {}).get("products", {}).get("edges", []):
                            node = edge.get("node", {})
                            handle = node.get("handle", "product")
                            for ve in node.get("variants", {}).get("edges", []):
                                vn = ve.get("node", {})
                                if vn.get("availableForSale", True):
                                    try:
                                        price = float(vn.get("priceV2", {}).get("amount", "0"))
                                        vid = str(vn.get("id", "").split("/")[-1])
                                        if vid and price > 0:
                                            all_variants.append({
                                                "id": vid, "price": price, "handle": handle,
                                                "requires_shipping": vn.get("requiresShipping", True),
                                            })
                                    except (ValueError, TypeError):
                                        continue
        except Exception as e:
            logger.debug(f"GraphQL fetch failed: {e}")

    if all_variants:
        all_variants.sort(key=lambda x: x["price"])
        s = all_variants[0]
        logger.info(f"selected handle={s['handle']} id={s['id']} price={s['price']}")
        set_cached_variant(domain, s["id"], str(s["price"]), s["requires_shipping"], s["handle"])
        return {
            "site": domain, "price": f"{s['price']:.2f}",
            "variant_id": s["id"],
            "link": f"{domain}/products/{s['handle']}",
            "requires_shipping": s["requires_shipping"],
            "handle": s["handle"],
        }, None

    return False, "No products found"


# =====================================================================
# ADD TO CART
# =====================================================================
async def add_to_cart_async(session, domain, variant_id, proxy=None):
    domain = domain.rstrip("/")
    if not domain.startswith("http"):
        domain = "https://" + domain

    base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": domain,
        "Referer": domain + "/products",
    }

    url = f"{domain}/cart/add.js"

    headers = {**base_headers, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    try:
        resp = await post_with_retry(session, url, data=f"id={variant_id}&quantity=1",
                                     headers=headers, proxy=proxy, max_retries=1, backoff=0.5)
        if resp and resp.status in (200, 201):
            logger.info("cart add ok (form)")
            return resp
    except Exception as e:
        logger.warning(f"cart form exception: {e}")

    headers = {**base_headers, "Content-Type": "application/json"}
    try:
        resp = await post_with_retry(session, url,
                                     json={"items": [{"id": int(variant_id), "quantity": 1}]},
                                     headers=headers, proxy=proxy, max_retries=1, backoff=0.5)
        if resp and resp.status in (200, 201):
            logger.info("cart add ok (json)")
            return resp
    except Exception as e:
        logger.warning(f"cart json exception: {e}")

    logger.error("cart add failed on all methods")
    return None


# =====================================================================
# CORE: process_card
# =====================================================================
async def process_card(cc, mes, ano, cvv, site_url, variant_id=None, proxy_str=None):
    proxy_ok = True
    gateway = "UNKNOWN"
    total_price = "0.00"
    currency = "USD"
    attempted_price = "0.00"

    ourl = site_url if site_url.startswith("http") else f"https://{site_url}"
    payment_identifier = None
    proxy = parse_proxy(proxy_str) if proxy_str else None
    proxy_url = proxy_for_aiohttp(proxy)
    checkpoint_data = None
    requires_shipping = True

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": ourl,
            "Referer": ourl,
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }

        address_info = pick_addr(ourl)
        country_code = address_info["countryCode"]
        firstName, lastName = get_random_name()
        email = generate_email(firstName, lastName)
        phone = address_info["phone"]
        street = address_info["address1"]
        city = address_info["city"]
        state = address_info["zoneCode"]
        s_zip = address_info["postalCode"]

        # primary domain (best-effort, short timeout)
        primary_domain = None
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as ts:
                async with ts.post(
                    f"{ourl}/api/2024-01/graphql.json",
                    json={"query": "{ shop { primaryDomain { url } } }"},
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pd = data.get("data", {}).get("shop", {}).get("primaryDomain", {}).get("url")
                        if pd:
                            primary_domain = pd.rstrip("/")
        except Exception as e:
            logger.debug(f"primary domain fetch failed: {e}")

        if primary_domain:
            ourl = primary_domain

        if not variant_id:
            info, err = await fetch_products(ourl, proxy_str)
            if info is False or info is None:
                if not ourl.startswith("https://www."):
                    www = ourl.replace("https://", "https://www.")
                    info, err = await fetch_products(www, proxy_str, force_refresh=True)
                    if info is not False and info is not None:
                        ourl = www
                if info is False or info is None:
                    info, err = await fetch_products(ourl, proxy_str, force_refresh=True)
            if info is False or info is None:
                return False, err or "Product fetch failed", gateway, total_price, currency, proxy_ok
            variant_id = info["variant_id"]
            requires_shipping = info.get("requires_shipping", True)

        session = await session_pool.get(proxy)

        cart_resp = await add_to_cart_async(session, ourl, variant_id, proxy_url)
        if not cart_resp or cart_resp.status not in (200, 201):
            status = cart_resp.status if cart_resp else "NO_RESPONSE"
            return False, f"Cart failed with status {status}", gateway, total_price, currency, proxy_ok

        checkout_headers = {**headers, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
        try:
            response = await session.post(
                url=ourl + "/checkout",
                allow_redirects=True,
                headers=checkout_headers,
                proxy=proxy_url,
                timeout=10,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            proxy_ok = False
            return False, f"Checkout proxy error: {str(e)}", gateway, total_price, currency, proxy_ok

        checkout_url = str(response.url)
        text = await response.text()

        if is_dead_store(text):
            return False, "DEAD_STORE", gateway, total_price, currency, proxy_ok

        if "login" in checkout_url.lower():
            return False, "Site requires login!", gateway, total_price, currency, proxy_ok

        m = _RE_CHECKOUT_CN.search(checkout_url)
        attempt_token = m.group(1) if m else checkout_url.split("/")[-1].split("?")[0]

        sst = response.headers.get("X-Checkout-One-Session-Token")
        if not sst:
            parsed = urlparse(checkout_url)
            r_param = parse_qs(parsed.query).get("_r", [None])[0]
            if r_param and len(r_param) > 20:
                sst = r_param
        if not sst:
            sst = find_first(
                text,
                ('name="serialized-sessionToken" content="&quot;', "&quot;"),
                ('name="serialized-sessionToken" content="', '"'),
                ('"serializedSessionToken":"', '"'),
                ('data-session-token="', '"'),
                ('<meta name="serialized-sessionToken" content="', '"'),
                ('"sessionToken":"', '"'),
            )
        if not sst:
            tok = _RE_HEX32.search(checkout_url)
            if tok:
                sst = tok.group(0)

        if not sst:
            logger.warning(f"no sst. url={checkout_url}")
            return False, "Failed to get session token", gateway, total_price, currency, proxy_ok

        queueToken = find_first(text, ("queueToken&quot;:&quot;", "&quot;"), ('"queueToken":"', '"'))
        stableId = find_first(text, ("stableId&quot;:&quot;", "&quot;"), ('"stableId":"', '"')) or "1"
        merch = find_first(
            text,
            ("ProductVariantMerchandise/", "&quot;"),
            ('"merchandiseId":"gid://shopify/ProductVariantMerchandise/', '"'),
        ) or str(variant_id)

        currency = "USD"
        if "currencyCode&quot;:&quot;" in text:
            currency = extract_between(text, "currencyCode&quot;:&quot;", "&quot;") or "USD"
        elif '"currencyCode":"' in text:
            currency = extract_between(text, '"currencyCode":"', '"') or "USD"

        address_info = pick_addr(ourl, currency)
        country_code = address_info["countryCode"]
        phone = address_info["phone"]
        street = address_info["address1"]
        city = address_info["city"]
        state = address_info["zoneCode"]
        s_zip = address_info["postalCode"]

        subtotal = extract_between(
            text,
            'subtotalBeforeTaxesAndShipping&quot;:{&quot;value&quot;:{&quot;amount&quot;:&quot;',
            "&quot;",
        ) or extract_between(text, '"subtotalBeforeTaxesAndShipping":{"value":{"amount":"', '"')
        if not subtotal:
            pm = _RE_PRICE.search(text)
            subtotal = pm.group(1) if pm else "0.01"
        attempted_price = subtotal

        unescaped = text.replace("&quot;", '"')
        bm = _RE_COMMIT_SHA.search(unescaped)
        build_id = bm.group(1) if bm else None
        im = _RE_IDENT_SIG.search(unescaped)
        ident_sig = im.group(1) if im else None

        source_token = extract_between(text, 'name="serialized-sourceToken" content="', '"')
        if source_token:
            source_token = source_token.replace("&quot;", "").strip('"')

        use_persisted = "internal/graphql/persisted" in text or "persisted?operationName" in text
        persisted_id = None
        if use_persisted:
            persisted_id = extract_between(text, '"persistedQueryId":"', '"') or \
                "ab9f6891d952494319a7dc077b627ed5e468a855944d95806ee91c1e64a1edad"

        if use_persisted:
            graphql_url = f"https://{urlparse(ourl).netloc}/checkouts/internal/graphql/persisted"
            logger.info("persisted endpoint")
        else:
            graphql_url = f"https://{urlparse(ourl).netloc}/checkouts/unstable/graphql"
            logger.info("unstable endpoint")

        headers.update({
            "shopify-checkout-client": "checkout-web/1.0",
            "shopify-checkout-source": f'id="{attempt_token}", type="cn"',
            "x-checkout-one-session-token": sst,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        })
        if build_id:
            headers["x-checkout-web-build-id"] = build_id
            headers["x-checkout-web-deploy-stage"] = "production"
            headers["x-checkout-web-server-handling"] = "fast"
            headers["x-checkout-web-server-rendering"] = "yes"
        if source_token:
            headers["x-checkout-web-source-id"] = source_token

        async def make_graphql_request(payload):
            nonlocal proxy_ok
            try:
                op_name = payload.get("operationName")
                params = {"operationName": op_name} if op_name else {}
                if use_persisted and persisted_id:
                    payload = payload.copy()
                    payload["id"] = persisted_id
                    payload.pop("query", None)
                async with session.post(
                    graphql_url, params=params, headers=headers,
                    json=payload, proxy=proxy_url, timeout=10,
                ) as resp:
                    return await resp.text(), resp
            except (aiohttp.ClientError, asyncio.TimeoutError):
                proxy_ok = False
                raise

        if requires_shipping:
            shipping_delivery = {
                "deliveryLines": [{
                    "destination": {"partialStreetAddress": {
                        "address1": street, "address2": "", "city": city,
                        "countryCode": country_code, "postalCode": s_zip,
                        "firstName": firstName, "lastName": lastName,
                        "zoneCode": state, "phone": phone,
                    }},
                    "selectedDeliveryStrategy": {
                        "deliveryStrategyMatchingConditions": {
                            "estimatedTimeInTransit": {"any": True},
                            "shipments": {"any": True},
                        },
                        "options": {},
                    },
                    "targetMerchandiseLines": {"any": True},
                    "deliveryMethodTypes": ["SHIPPING"],
                    "expectedTotalPrice": {"any": True},
                    "destinationChanged": True,
                }],
                "noDeliveryRequired": [],
                "useProgressiveRates": False,
                "prefetchShippingRatesStrategy": None,
                "supportsSplitShipping": True,
            }
        else:
            shipping_delivery = {
                "deliveryLines": [],
                "noDeliveryRequired": [{"stableId": stableId}],
                "useProgressiveRates": False,
                "prefetchShippingRatesStrategy": None,
                "supportsSplitShipping": True,
            }

        proposal_op = get_operation_name(QUERY_PROPOSAL_SHIPPING) or "Proposal"
        json_data = {
            "query": QUERY_PROPOSAL_SHIPPING,
            "variables": {
                "sessionInput": {"sessionToken": sst},
                "queueToken": queueToken or "",
                "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
                "delivery": shipping_delivery,
                "deliveryExpectations": {"deliveryExpectationLines": []},
                "merchandise": {"merchandiseLines": [{
                    "stableId": stableId,
                    "merchandise": {"productVariantReference": {
                        "id": f"gid://shopify/ProductVariantMerchandise/{merch}",
                        "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                        "properties": [], "sellingPlanId": None, "sellingPlanDigest": None,
                    }},
                    "quantity": {"items": {"value": 1}},
                    "expectedTotalPrice": {"value": {"amount": subtotal, "currencyCode": currency}},
                    "lineComponentsSource": None,
                    "lineComponents": [],
                }]},
                "payment": {
                    "totalAmount": {"any": True},
                    "paymentLines": [],
                    "billingAddress": {"streetAddress": {
                        "address1": "", "city": "", "countryCode": country_code,
                        "lastName": "", "zoneCode": "ENG", "phone": "",
                    }},
                },
                "buyerIdentity": {
                    "customer": {"presentmentCurrency": currency, "countryCode": country_code},
                    "email": email, "emailChanged": False,
                    "phoneCountryCode": country_code,
                    "marketingConsent": [{"email": {"value": email}}],
                    "shopPayOptInPhone": {"countryCode": country_code},
                    "rememberMe": False,
                },
                "tip": {"tipLines": []},
                "taxes": {
                    "proposedAllocations": None,
                    "proposedTotalAmount": {"value": {"amount": "0", "currencyCode": currency}},
                    "proposedTotalIncludedAmount": None,
                    "proposedMixedStateTotalAmount": None,
                    "proposedExemptions": [],
                },
                "note": {"message": None, "customAttributes": []},
                "localizationExtension": {"fields": []},
                "nonNegotiableTerms": None,
                "scriptFingerprint": {
                    "signature": None, "signatureUuid": None,
                    "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": [],
                },
                "optionalDuties": {"buyerRefusesDuties": False},
            },
            "operationName": proposal_op,
        }

        proposal_text = None
        for attempt in range(2):
            try:
                proposal_text, _ = await make_graphql_request(json_data)
                break
            except Exception as e:
                if attempt == 0:
                    continue
                proxy_ok = False
                return False, f"Shipping proposal error: {str(e)}", gateway, total_price, currency, proxy_ok

        if not proposal_text:
            return False, "Shipping proposal failed", gateway, total_price, currency, proxy_ok
        if is_captcha_required(proposal_text):
            return False, "CAPTCHA_REQUIRED", gateway, total_price, currency, proxy_ok

        try:
            proposal_json = json.loads(proposal_text)
        except Exception:
            return False, "Invalid JSON in proposal", gateway, total_price, currency, proxy_ok

        if "errors" in proposal_json:
            return False, f"GraphQL Error: {proposal_json['errors']}", gateway, total_price, currency, proxy_ok

        session_data = proposal_json.get("data", {}).get("session", {})
        if not session_data:
            return False, "No session data", gateway, total_price, currency, proxy_ok
        result = session_data.get("negotiate", {}).get("result", {})
        if not result or result.get("__typename") != "NegotiationResultAvailable":
            return False, "No valid result", gateway, total_price, currency, proxy_ok

        checkpoint_data = result.get("checkpointData")
        seller_proposal = result.get("sellerProposal", {})
        if not seller_proposal:
            return False, "No seller proposal", gateway, total_price, currency, proxy_ok

        if requires_shipping:
            delivery_data = seller_proposal.get("delivery", {})
            if delivery_data.get("__typename") == "FilledDeliveryTerms":
                dlines = delivery_data.get("deliveryLines", [{}])
                strategies = dlines[0].get("availableDeliveryStrategies", []) if dlines else []
                if strategies:
                    cheapest = min(strategies, key=lambda s: float(
                        s.get("amount", {}).get("value", {}).get("amount", 999999)))
                    delivery_strategy = cheapest.get("handle", "")
                    shipping_amount = float(
                        cheapest.get("amount", {}).get("value", {}).get("amount", "0"))
                else:
                    delivery_strategy = ""
                    shipping_amount = 0.0
            else:
                delivery_strategy = ""
                shipping_amount = 0.0
        else:
            delivery_strategy = ""
            shipping_amount = 0.0

        if requires_shipping and shipping_amount == 0.0:
            shipping_amount = 0.01
            logger.warning("shipping was 0, set 0.01")

        tax_data = seller_proposal.get("tax", {})
        tax_amount = 0.0
        if tax_data and tax_data.get("totalTaxAmount"):
            tax_amount = float(tax_data["totalTaxAmount"].get("value", {}).get("amount", "0"))

        payment_data = seller_proposal.get("payment", {})
        if payment_data and payment_data.get("__typename") == "FilledPaymentTerms":
            for method in payment_data.get("availablePaymentLines", []):
                pm = method.get("paymentMethod", {})
                if pm.get("paymentMethodIdentifier"):
                    payment_identifier = pm.get("paymentMethodIdentifier")
                    gateway = pm.get("extensibilityDisplayName") or pm.get("name", "UNKNOWN")
                    break

        if not payment_identifier:
            return False, "No valid payment method", gateway, total_price, currency, proxy_ok

        # delivery proposal
        delivery_op = get_operation_name(QUERY_PROPOSAL_DELIVERY) or "Proposal"
        json_data["query"] = QUERY_PROPOSAL_DELIVERY
        json_data["operationName"] = delivery_op

        if requires_shipping:
            delivery_line = {
                "destination": {"streetAddress": {
                    "address1": street, "address2": "", "city": city,
                    "countryCode": country_code, "postalCode": s_zip,
                    "firstName": firstName, "lastName": lastName,
                    "zoneCode": state, "phone": phone,
                }},
                "selectedDeliveryStrategy": {
                    "deliveryStrategyByHandle": {
                        "handle": delivery_strategy or "",
                        "customDeliveryRate": False,
                    },
                    "options": {"phone": phone},
                },
                "targetMerchandiseLines": {"lines": [{"stableId": stableId}]},
                "deliveryMethodTypes": ["SHIPPING"],
                "expectedTotalPrice": {"value": {"amount": str(shipping_amount), "currencyCode": currency}},
                "destinationChanged": False,
            }
            delivery_input = {
                "deliveryLines": [delivery_line],
                "noDeliveryRequired": [],
                "useProgressiveRates": True,
                "prefetchShippingRatesStrategy": None,
                "supportsSplitShipping": True,
            }
        else:
            delivery_input = {
                "deliveryLines": [],
                "noDeliveryRequired": [{"stableId": stableId}],
                "useProgressiveRates": True,
                "prefetchShippingRatesStrategy": None,
                "supportsSplitShipping": True,
            }

        json_data["variables"]["delivery"] = delivery_input
        json_data["variables"]["payment"]["billingAddress"] = {"streetAddress": {
            "address1": street, "address2": "", "city": city,
            "countryCode": country_code, "postalCode": s_zip,
            "firstName": firstName, "lastName": lastName,
            "zoneCode": state, "phone": phone,
        }}
        json_data["variables"]["taxes"]["proposedTotalAmount"]["value"]["amount"] = str(tax_amount)
        json_data["variables"]["buyerIdentity"]["shopPayOptInPhone"]["number"] = phone

        try:
            delivery_text, _ = await make_graphql_request(json_data)
        except Exception as e:
            proxy_ok = False
            return False, f"Delivery proposal error: {str(e)}", gateway, total_price, currency, proxy_ok

        if is_captcha_required(delivery_text):
            return False, "CAPTCHA_REQUIRED on delivery", gateway, total_price, currency, proxy_ok

        try:
            delivery_json = json.loads(delivery_text)
        except Exception:
            return False, "Invalid JSON in delivery proposal", gateway, total_price, currency, proxy_ok

        if "errors" in delivery_json:
            return False, f"Delivery proposal error: {delivery_json['errors']}", gateway, total_price, currency, proxy_ok

        delivery_result = delivery_json.get("data", {}).get("session", {}).get("negotiate", {}).get("result", {})
        if not delivery_result or delivery_result.get("__typename") != "NegotiationResultAvailable":
            return False, "No valid result from delivery proposal", gateway, total_price, currency, proxy_ok

        if delivery_result.get("queueToken"):
            queueToken = delivery_result["queueToken"]
        if delivery_result.get("checkpointData"):
            checkpoint_data = delivery_result["checkpointData"]

        delivery_seller = delivery_result.get("sellerProposal", {})
        if not delivery_seller:
            return False, "No seller proposal from delivery", gateway, total_price, currency, proxy_ok

        payment_data_final = delivery_seller.get("payment", {})
        if payment_data_final and payment_data_final.get("__typename") == "FilledPaymentTerms":
            for method in payment_data_final.get("availablePaymentLines", []):
                pm = method.get("paymentMethod", {})
                if pm.get("paymentMethodIdentifier"):
                    payment_identifier = pm.get("paymentMethodIdentifier")
                    gateway = pm.get("extensibilityDisplayName") or pm.get("name", "UNKNOWN")
                    break

        payment_stable_id = None
        payment_special_instructions = None
        payment_due_at = None
        payment_lines = delivery_seller.get("payment", {}).get("paymentLines", [])
        if payment_lines:
            fp = payment_lines[0]
            payment_stable_id = fp.get("stableId")
            payment_special_instructions = fp.get("specialInstructions")
            payment_due_at = fp.get("dueAt")

        merchandise_stable_id = None
        merchandise_total = None
        merch_lines = delivery_seller.get("merchandise", {}).get("merchandiseLines", [])
        if merch_lines:
            merchandise_stable_id = merch_lines[0].get("stableId")
            merchandise_total = merch_lines[0].get("totalAmount", {}).get("value", {}).get("amount")

        if requires_shipping:
            seller_delivery = delivery_seller.get("delivery", {})
            if seller_delivery.get("__typename") == "FilledDeliveryTerms":
                dlines = seller_delivery.get("deliveryLines", [{}])
                if dlines:
                    selected_handle = dlines[0].get("selectedDeliveryStrategy", {}).get("handle")
                    available = dlines[0].get("availableDeliveryStrategies", [])
                    if selected_handle:
                        for strat in available:
                            if strat.get("handle") == selected_handle:
                                shipping_amount = float(
                                    strat.get("amount", {}).get("value", {}).get("amount", "0"))
                                break
                        else:
                            if available:
                                shipping_amount = float(
                                    available[0].get("amount", {}).get("value", {}).get("amount", "0"))
                    elif available:
                        shipping_amount = float(
                            available[0].get("amount", {}).get("value", {}).get("amount", "0"))

                    if selected_handle:
                        delivery_input["deliveryLines"][0]["selectedDeliveryStrategy"]["deliveryStrategyByHandle"]["handle"] = selected_handle

                    seller_expected_price = dlines[0].get("expectedTotalPrice", {})
                    if seller_expected_price:
                        if shipping_amount == 0.0:
                            shipping_amount = 0.01
                        delivery_input["deliveryLines"][0]["expectedTotalPrice"] = {
                            "value": {"amount": str(shipping_amount), "currencyCode": currency}
                        }
                    else:
                        delivery_input["deliveryLines"][0]["expectedTotalPrice"] = {"any": True}

            if merchandise_stable_id:
                delivery_input["deliveryLines"][0]["targetMerchandiseLines"]["lines"][0]["stableId"] = merchandise_stable_id
            delivery_input["deliveryLines"][0]["destinationChanged"] = False
        else:
            if merchandise_stable_id:
                delivery_input["noDeliveryRequired"][0]["stableId"] = merchandise_stable_id

        final_total = None
        for field in ("checkoutTotal", "total", "runningTotal"):
            val = delivery_seller.get(field, {})
            if val and val.get("__typename") in ("MoneyValue", "MoneyValueConstraint"):
                amount = val.get("value", {}).get("amount") or val.get("amount")
                if amount is not None:
                    final_total = str(amount)
                    currency = val.get("value", {}).get("currencyCode") or val.get("currencyCode") or currency
                    break
        if final_total is None:
            merch_total = merchandise_total or subtotal
            shipping = "0"
            if requires_shipping and "expectedTotalPrice" in delivery_input["deliveryLines"][0]:
                exp = delivery_input["deliveryLines"][0]["expectedTotalPrice"]
                if "value" in exp:
                    shipping = exp["value"].get("amount", "0")
            tax = delivery_seller.get("tax", {}).get("totalTaxAmount", {}).get("value", {}).get("amount", "0")
            final_total = str(Decimal(str(merch_total)) + Decimal(str(shipping)) + Decimal(str(tax)))

        if float(final_total) <= 0:
            final_total = str(max(0.01, float(merchandise_total or subtotal)))

        # ── PCI vault (parallel strategies, first success wins) ──────
        payload = {
            "credit_card": {
                "number": cc, "month": int(mes), "year": int(ano),
                "verification_value": cvv,
                "name": f"{firstName} {lastName}",
            },
            "payment_session_scope": urlparse(ourl).netloc,
        }
        vault_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://checkout.pci.shopifyinc.com",
            "User-Agent": headers["User-Agent"],
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
        if ident_sig:
            vault_headers["shopify-identification-signature"] = ident_sig

        async def _vault_attempt(pl, hd, px):
            try:
                async with session.post(
                    "https://checkout.pci.shopifyinc.com/sessions",
                    json=pl, headers=hd, proxy=px, timeout=8,
                ) as vr:
                    if vr.status in (200, 201):
                        vd = await vr.json()
                        tid = vd.get("id")
                        if tid:
                            return tid
            except Exception:
                pass
            return None

        token = None
        primary_task = asyncio.create_task(_vault_attempt(payload, vault_headers, proxy_url))
        # First attempt runs; if it fails fast, we race the other variants
        try:
            token = await asyncio.wait_for(primary_task, timeout=4.0)
        except asyncio.TimeoutError:
            # primary still running — let it finish in background while we try alternates
            alt_payload = {**payload,
                           "credit_card": {**payload["credit_card"],
                                           "number": _RE_WS.sub("", cc)}}
            alt_headers = {**vault_headers,
                           "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"}
            alt_headers2 = {**vault_headers, "Accept": "application/vnd.shopify.pci.v1+json"}
            alt_payload2 = {**payload,
                            "credit_card": {**payload["credit_card"],
                                            "number": _RE_WS.sub("", cc)}}

            races = [
                asyncio.create_task(_vault_attempt(alt_payload, vault_headers, proxy_url)),
                asyncio.create_task(_vault_attempt(payload, alt_headers, proxy_url)),
                asyncio.create_task(_vault_attempt(payload, alt_headers2, proxy_url)),
                asyncio.create_task(_vault_attempt(alt_payload2, vault_headers, None)),
                primary_task,
            ]
            done, pending = await asyncio.wait(races, return_when=asyncio.FIRST_COMPLETED, timeout=6)
            for t in done:
                r = t.result() if not t.cancelled() and t.exception() is None else None
                if r:
                    token = r
                    break
            for t in pending:
                t.cancel()

        if not token:
            return False, "Unable to get payment token", gateway, attempted_price, currency, proxy_ok
        logger.info("pci token ok")

        # submit
        submit_merch = {
            "merchandiseLines": [{
                "stableId": merchandise_stable_id or stableId,
                "merchandise": {"productVariantReference": {
                    "id": f"gid://shopify/ProductVariantMerchandise/{merch}",
                    "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                    "properties": [], "sellingPlanId": None, "sellingPlanDigest": None,
                }},
                "quantity": {"items": {"value": 1}},
                "expectedTotalPrice": {"value": {"amount": str(merchandise_total or subtotal), "currencyCode": currency}},
                "lineComponentsSource": None,
                "lineComponents": [],
            }]
        }

        payment_method = {
            "directPaymentMethod": {
                "paymentMethodIdentifier": payment_identifier,
                "sessionId": token,
                "billingAddress": {"streetAddress": {
                    "address1": street, "address2": "", "city": city,
                    "countryCode": country_code, "postalCode": s_zip,
                    "firstName": firstName, "lastName": lastName,
                    "zoneCode": state, "phone": phone,
                }},
                "cardSource": None,
            }
        }

        payment_line = {
            "paymentMethod": payment_method,
            "amount": {"value": {"amount": final_total, "currencyCode": currency}},
            "dueAt": payment_due_at,
        }
        if payment_stable_id:
            payment_line["stableId"] = payment_stable_id
        if payment_special_instructions:
            payment_line["specialInstructions"] = payment_special_instructions

        submit_variables = {
            "input": {
                "sessionInput": {"sessionToken": sst},
                "queueToken": queueToken or "",
                "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
                "delivery": delivery_input,
                "merchandise": submit_merch,
                "payment": {
                    "totalAmount": {"value": {"amount": final_total, "currencyCode": currency}},
                    "paymentLines": [payment_line],
                    "billingAddress": {"streetAddress": {
                        "address1": street, "address2": "", "city": city,
                        "countryCode": country_code, "postalCode": s_zip,
                        "firstName": firstName, "lastName": lastName,
                        "zoneCode": state, "phone": phone,
                    }},
                },
                "buyerIdentity": {
                    "customer": {"presentmentCurrency": currency, "countryCode": country_code},
                    "email": email, "emailChanged": False,
                    "phoneCountryCode": country_code,
                    "marketingConsent": [{"email": {"value": email}}],
                    "shopPayOptInPhone": {"number": phone, "countryCode": country_code},
                    "rememberMe": False,
                },
                "taxes": {
                    "proposedAllocations": None,
                    "proposedTotalAmount": {"value": {"amount": str(tax_amount), "currencyCode": currency}},
                    "proposedTotalIncludedAmount": None,
                    "proposedMixedStateTotalAmount": None,
                    "proposedExemptions": [],
                },
                "tip": {"tipLines": []},
                "note": {"message": None, "customAttributes": []},
                "localizationExtension": {"fields": []},
                "nonNegotiableTerms": None,
                "optionalDuties": {"buyerRefusesDuties": False},
            },
            "attemptToken": attempt_token,
            "metafields": [],
            "analytics": {"requestUrl": checkout_url},
        }
        if checkpoint_data:
            submit_variables["input"]["checkpointData"] = checkpoint_data

        submit_op = get_operation_name(MUTATION_SUBMIT) or "SubmitForCompletion"
        submit_json = {
            "query": MUTATION_SUBMIT,
            "variables": submit_variables,
            "operationName": submit_op,
        }
        if use_persisted and persisted_id:
            submit_json["id"] = persisted_id
            submit_json.pop("query", None)

        try:
            submit_text, _ = await make_graphql_request(submit_json)
        except Exception as e:
            proxy_ok = False
            return False, f"Submit error: {str(e)}", gateway, attempted_price, currency, proxy_ok

        if is_captcha_required(submit_text):
            return False, "CAPTCHA_REQUIRED on submit", gateway, attempted_price, currency, proxy_ok

        try:
            submit_result = json.loads(submit_text)
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON in submit: {str(e)}", gateway, attempted_price, currency, proxy_ok

        if "errors" in submit_result:
            err = submit_result["errors"][0]
            code = err.get("extensions", {}).get("code", "")
            msg = err.get("message", "")
            return False, f"GRAPHQL_ERROR: {code} - {msg}", gateway, attempted_price, currency, proxy_ok

        submit_data = submit_result.get("data", {}).get("submitForCompletion", {})
        if not submit_data:
            return False, "No submit data", gateway, attempted_price, currency, proxy_ok

        result_type = submit_data.get("__typename", "")
        rid = None

        if result_type in ("SubmitSuccess", "SubmittedForCompletion", "SubmitAlreadyAccepted"):
            receipt = submit_data.get("receipt") or submit_data.get("order", {}).get("receipt")
            rid = receipt.get("id") if receipt else None
            if not rid:
                order = submit_data.get("order") or submit_data.get("purchaseOrder")
                if order:
                    rid = order.get("id") or order.get("checkoutId")
                if not rid:
                    return False, "No receipt ID", gateway, attempted_price, currency, proxy_ok
            purchase_order = submit_data.get("purchaseOrder", {})
            total_amount = purchase_order.get("totalAmountToPay", {})
            if total_amount:
                total_price = total_amount.get("amount", "0.00")
                currency = total_amount.get("currencyCode", currency)
            else:
                total_price = final_total

        elif result_type == "SubmitFailed":
            reason = submit_data.get("reason", "Unknown")
            return False, extract_clean_response(reason), gateway, attempted_price, currency, proxy_ok
        elif result_type == "SubmitRejected":
            errors = submit_data.get("errors", [])
            if errors:
                code = errors[0].get("code", "")
                return False, code if code else "SUBMIT_REJECTED", gateway, attempted_price, currency, proxy_ok
            return False, "SUBMIT_REJECTED", gateway, attempted_price, currency, proxy_ok
        elif result_type in ("Throttled", "CheckpointDenied"):
            return False, result_type, gateway, attempted_price, currency, proxy_ok
        else:
            return False, f"Unknown submit type: {result_type}", gateway, attempted_price, currency, proxy_ok

        # poll
        poll_op = get_operation_name(QUERY_POLL) or "PollForReceipt"
        poll_json = {
            "query": QUERY_POLL,
            "variables": {"receiptId": rid, "sessionToken": sst},
            "operationName": poll_op,
        }
        if use_persisted and persisted_id:
            poll_json["id"] = persisted_id
            poll_json.pop("query", None)

        poll_attempts = 0
        poll_data = {}
        while poll_attempts < 8:
            if poll_attempts > 0:
                delay = None
                if poll_data and poll_data.get("__typename") in ("ProcessingReceipt", "WaitingReceipt"):
                    delay = poll_data.get("pollDelay", 450) / 1000.0
                if delay is None:
                    delay = min(0.5 * (2 ** (poll_attempts - 1)), 4.0)
                await asyncio.sleep(max(0.2, min(delay, 0.8) if delay else 0.5))

            try:
                poll_text, _ = await make_graphql_request(poll_json)
            except Exception as e:
                proxy_ok = False
                return False, f"Poll error: {str(e)}", gateway, total_price, currency, proxy_ok

            if is_captcha_required(poll_text):
                return True, "CARD_DECLINED", gateway, total_price, currency, proxy_ok

            try:
                poll_data = json.loads(poll_text).get("data", {}).get("receipt", {})
                typename = poll_data.get("__typename", "")

                if typename == "ProcessedReceipt":
                    return True, random.choice(["Order Confirm", "ORDER_PAID"]), gateway, total_price, currency, proxy_ok
                elif typename == "FailedReceipt":
                    error = poll_data.get("processingError", {})
                    if error.get("__typename") == "PaymentFailed":
                        code = error.get("code", "")
                        msg = error.get("messageUntranslated", "")
                        if code in ("GENERIC_ERROR", "PAYMENT_FAILED", "") and msg:
                            return True, msg, gateway, total_price, currency, proxy_ok
                        return True, code if code else "CARD_DECLINED", gateway, total_price, currency, proxy_ok
                    return True, "FAILED", gateway, total_price, currency, proxy_ok
                elif typename == "ActionRequiredReceipt":
                    return True, "OTP_REQUIRED", gateway, total_price, currency, proxy_ok
                else:
                    poll_attempts += 1
                    continue
            except Exception:
                poll_attempts += 1
                continue

        return False, "Poll timeout", gateway, total_price, currency, proxy_ok

    except Exception as e:
        logger.error(f"checkout error: {e}")
        logger.error(traceback.format_exc())
        return False, f"Error Processing Card: {str(e)}", gateway, attempted_price, currency, proxy_ok


# =====================================================================
# aiohttp web app
# =====================================================================
def pretty_json(data, status=200):
    return web.Response(
        text=json.dumps(data, indent=2, ensure_ascii=False),
        status=status,
        content_type="application/json",
    )


@web.middleware
async def _req_id_middleware(request, handler):
    rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
    request["request_id"] = rid
    request["started"] = time.time()
    try:
        resp = await handler(request)
    except web.HTTPException as e:
        resp = e
    resp.headers["X-Request-Id"] = rid
    logger.info("req done", extra={"request_id": rid, "site": request.query.get("site", "")})
    return resp


async def shopify_handler(request: web.Request):
    start_time = time.time()
    _increment_active()
    try:
        site = request.query.get("site")
        cc_string = request.query.get("cc")
        proxy_str = request.query.get("proxy")
        variant = request.query.get("variant")

        if not site or not cc_string:
            return pretty_json({"error": "Missing parameters", "status": False}, status=400)

        parts = cc_string.split("|")
        if len(parts) != 4:
            return pretty_json({"error": "Invalid format. Use: CC|MM|YYYY|CVV", "status": False}, status=400)
        cc, mes, ano, cvv = (p.strip() for p in parts)

        try:
            success, message, gateway, price, currency, proxy_ok = await asyncio.wait_for(
                process_card(cc, mes, ano, cvv, site, variant, proxy_str),
                timeout=int(os.getenv("REQUEST_TIMEOUT", "90")),
            )
        except asyncio.TimeoutError:
            return pretty_json({
                "CC": cc_string, "Currency": "USD", "Gateway": "Shopify",
                "Price": "0.00", "Proxy": "live" if proxy_str else "none",
                "Response": "TIMEOUT", "Status": False,
                "Time": f"{time.time() - start_time:.2f}s",
                "RequestId": request["request_id"],
            })

        clean = extract_clean_response(message)
        elapsed = time.time() - start_time

        if not proxy_str:
            proxy_status = "none"
        elif proxy_ok:
            proxy_status = "live"
        else:
            proxy_status = "dead"

        response = {
            "CC": cc_string,
            "Currency": currency,
            "Gateway": gateway,
            "Price": str(price) if price else "0.00",
            "Proxy": proxy_status,
            "Response": clean,
            "Status": success,
            "Time": f"{elapsed:.2f}s",
            "RequestId": request["request_id"],
        }
        logger.info(f"response: {response}")
        return pretty_json(response)

    except Exception as e:
        logger.exception("request error")
        elapsed = time.time() - start_time
        proxy_status = "dead" if any(k in str(e).lower() for k in
                                     ("proxy", "connection", "timeout", "refused")) else "unknown"
        if not request.query.get("proxy"):
            proxy_status = "none"
        return pretty_json({
            "CC": request.query.get("cc", ""),
            "Currency": "USD",
            "Gateway": "UNKNOWN",
            "Price": "0.00",
            "Proxy": proxy_status,
            "Response": f"ERROR: {str(e)}",
            "Status": False,
            "Time": f"{elapsed:.2f}s",
            "RequestId": request.get("request_id", ""),
        }, status=500)
    finally:
        _decrement_active()


async def health_handler(request: web.Request):
    with _metrics_lock:
        active = _active_checkouts
    return pretty_json({
        "status": "ok",
        "active_checkouts": active,
        "socks": HAS_SOCKS,
        "cached_sites": len(_variant_cache),
        "sessions_cached": len(session_pool._sessions),
        "time": time.time(),
    })


async def index_handler(request: web.Request):
    with _metrics_lock:
        active = _active_checkouts
    return pretty_json({
        "name": "Shopify Checker API (Railway-optimized)",
        "endpoint": "/shopify?site=URL&cc=CC|MM|YYYY|CVV&proxy=PROXY",
        "active_checkouts": active,
        "features": [
            "aiohttp.web (native async server)",
            "Shared ClientSession pool per proxy (HTTP keep-alive reuse)",
            "Async queue-based logging",
            "Debounced variant cache writes",
            "Parallel PCI vault fallbacks",
            "Persisted GraphQL endpoint support",
            "CheckpointData propagation",
            "Dynamic poll delays",
            "requiresShipping detection",
            "Multi-scheme proxy support (http/https/socks4/socks5)",
        ],
    })


async def _on_startup(app):
    logger.info("server starting")


async def _on_cleanup(app):
    logger.info("server shutting down")
    await session_pool.close_all()
    save_variant_cache()
    _listener.stop()


def build_app():
    app = web.Application(middlewares=[_req_id_middleware])
    app.router.add_get("/shopify", shopify_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/", index_handler)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8073"))
    logger.info(f"starting aiohttp on {host}:{port}")
    if not HAS_SOCKS:
        logger.warning("aiohttp_socks missing — socks proxies will not work")

    web.run_app(
        build_app(),
        host=host,
        port=port,
        access_log=None,       # we log via middleware
        print=None,
        backlog=2048,
        reuse_port=False,
    )
