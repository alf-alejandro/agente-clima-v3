import requests
import json
import logging
from datetime import datetime, timezone, timedelta

from app.config import (
    GAMMA, WEATHER_CITIES, MIN_VOLUME, SCAN_DAYS_AHEAD,
    CITY_UTC_OFFSET, MIN_LOCAL_HOUR,
)

CLOB = "https://clob.polymarket.com"
log = logging.getLogger(__name__)


def now_utc():
    return datetime.now(timezone.utc)


def parse_price(val):
    try:
        return float(val)
    except Exception:
        return None


def parse_date(val):
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except Exception:
        return None


def get_prices(m):
    raw = m.get("outcomePrices") or "[]"
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        yes = parse_price(prices[0]) if len(prices) > 0 else None
        no  = parse_price(prices[1]) if len(prices) > 1 else None
        if yes is not None and yes < 0:
            yes = None
        if no is not None and no < 0:
            no = None
        if yes == 0.0 and no is not None and no >= 0.99:
            yes = 0.001
        if no == 0.0 and yes is not None and yes >= 0.99:
            no = 0.001
        return yes, no
    except Exception:
        return None, None


def city_is_ready(city, scan_date, today):
    """True si en esa ciudad ya son las MIN_LOCAL_HOUR del día scan_date."""
    offset = CITY_UTC_OFFSET.get(city)
    if offset is None:
        return False
    local_now = now_utc() + timedelta(hours=offset)
    return local_now.date() == scan_date and local_now.hour >= MIN_LOCAL_HOUR


def build_event_slug(city, date):
    months = {
        1: "january", 2: "february", 3: "march", 4: "april",
        5: "may", 6: "june", 7: "july", 8: "august",
        9: "september", 10: "october", 11: "november", 12: "december",
    }
    return f"highest-temperature-in-{city}-on-{months[date.month]}-{date.day}-{date.year}"


def fetch_event_by_slug(slug):
    try:
        r = requests.get(
            f"{GAMMA}/events", params={"slug": slug, "limit": 1},
            timeout=(5, 8),
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
    except Exception:
        pass
    return None


def fetch_market_live(slug):
    try:
        r = requests.get(
            f"{GAMMA}/markets", params={"slug": slug, "limit": 1},
            timeout=(5, 8),
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
    except Exception:
        pass
    return None


def fetch_live_prices(slug):
    """Fetch YES/NO prices via Gamma API (~2 min cache). Used as fallback."""
    m = fetch_market_live(slug)
    if not m:
        return None, None
    return get_prices(m)


def fetch_no_price_clob(no_token_id):
    """Fetch real-time NO price from CLOB order book (no cache).

    Uses best ask = "Buy No" price shown on Polymarket UI.
    """
    if not no_token_id:
        return None, None
    try:
        r = requests.get(
            f"{CLOB}/book",
            params={"token_id": no_token_id},
            timeout=(2, 3),
        )
        if r.status_code != 200:
            return None, None
        data = r.json()

        bids = data.get("bids") or []
        asks = data.get("asks") or []

        no_price = None
        if asks:
            no_price = min(float(a["price"]) for a in asks)
        elif bids:
            no_price = max(float(b["price"]) for b in bids)
        else:
            ltp = data.get("last_trade_price")
            if ltp:
                no_price = float(ltp)

        if no_price is None or not (0.0 < no_price < 1.0):
            return None, None

        yes_price = round(1.0 - no_price, 6)
        return yes_price, no_price

    except Exception:
        log.debug("CLOB book fetch failed for token %s", str(no_token_id)[:20])
        return None, None


def scan_opportunities(existing_ids=None):
    """Scan for NO-side weather opportunities across today + SCAN_DAYS_AHEAD days.

    Wide Gamma filter (0.50–0.97) — CLOB + score are the real entry gates in bot.py.
    Returns all candidates so market_scorer can build price history for any market.
    """
    if existing_ids is None:
        existing_ids = set()

    today = now_utc().date()
    scan_dates = [today + timedelta(days=d) for d in range(SCAN_DAYS_AHEAD + 1)]
    opportunities = []

    for scan_date in scan_dates:
        for city in WEATHER_CITIES:
            if not city_is_ready(city, scan_date, today):
                continue
            slug = build_event_slug(city, scan_date)
            event = fetch_event_by_slug(slug)
            if not event:
                continue

            for m in (event.get("markets") or []):
                condition_id = m.get("conditionId")
                if condition_id in existing_ids:
                    continue

                yes_price, no_price = get_prices(m)
                if yes_price is None or no_price is None:
                    continue

                volume = parse_price(m.get("volume") or 0) or 0
                if volume < MIN_VOLUME:
                    continue

                # Wide Gamma filter: only skip dead/resolved markets.
                if not (0.50 <= no_price <= 0.97):
                    continue

                profit = (1.0 - no_price) * 100

                end_dt = parse_date(m.get("endDate"))
                if end_dt and end_dt.date() < today:
                    continue

                raw_ids = m.get("clobTokenIds") or "[]"
                clob_ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
                yes_token_id = clob_ids[0] if len(clob_ids) > 0 else None
                no_token_id  = clob_ids[1] if len(clob_ids) > 1 else None

                opportunities.append({
                    "condition_id": condition_id,
                    "city": city,
                    "question": m.get("question", ""),
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "volume": volume,
                    "end_date": end_dt.isoformat() if end_dt else None,
                    "slug": m.get("slug", ""),
                    "profit_cents": round(profit, 1),
                    "yes_token_id": yes_token_id,
                    "no_token_id": no_token_id,
                })

    # Sort: markets closest to center of V3 entry range (0.855) first
    def sort_key(o):
        return abs(o["no_price"] - 0.855)

    opportunities.sort(key=sort_key)
    return opportunities
