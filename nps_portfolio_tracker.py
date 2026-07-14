import argparse
import csv
import json
import math
import os
import re
import time
import pandas as pd
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ================================================================
# STOCK HOLDINGS  — update manually when you buy / sell
#
# ACTIVE_STOCKS : positions you currently hold
#   ticker          exchange symbol (used for Alpha Vantage price fetch)
#   isin            for reference only
#   shares          current quantity
#   cost_inr        total amount paid in INR across all purchases
#   cost_per_share  average cost per share in INR
#   acquired_on     optional; used to calculate annualised return
#
# CLOSED_STOCKS : positions you have fully exited
#   realized_return_inr   total profit / loss in INR
#   return_pct_pa         annualised return %
# ================================================================

# =========================================================
# CONFIG
# =========================================================

ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")

BASE_DIR = Path(__file__).resolve().parent
CACHE_FILE = BASE_DIR / "price_cache.json"
CACHE_EXPIRY_SECONDS = 43200  # 12 hours
REQUEST_DELAY_SECONDS = 1.1
HTTP_TIMEOUT_SECONDS = 15
NPSNAV_LATEST_MIN_URL = "https://npsnav.in/api/latest-min"
NPSNAV_HISTORICAL_URL_TEMPLATE = "https://npsnav.in/api/historical/{scheme_code}"

TRAILING_RETURN_WINDOWS = [
    ("1 week", "days", 7),
    ("1 month", "months", 1),
    ("3 months", "months", 3),
    ("6 months", "months", 6),
    ("1 year", "years", 1),
    ("3 years", "years", 3),
    ("5 years", "years", 5),
]

NPS_TIER_FILE_PATTERNS = {
    "Tier I": "NPS Tier I Contribution*.csv",
    "Tier II": "NPS Tier II Contribution*.csv",
}

NPSNAV_SCHEME_CODES = {
    "TATA PENSION FUND MANAGEMENT PRIVATE LIMITED SCHEME E - TIER I POP": "SM011001",
    "TATA PENSION FUND MANAGEMENT PRIVATE LIMITED SCHEME C - TIER I POP": "SM011002",
    "TATA PENSION FUND MANAGEMENT PRIVATE LIMITED SCHEME E - TIER II POP": "SM011005",
}

PPF_HOLDINGS = [
    {
        "name": "Arjun R PPF",
        "source_file": "Holdings Statement_Arjun&#039;s Portfolio_10-Jun-2026.xls",
        "as_of_date": "10-Jun-2026",
        "invested": 181_500.00,
        "current_value": 239_476.50,
        "annualized_pct": 7.1,
    },
]

BOND_PPF_HOLDING_NAME = "Arjun R PPF"
BOND_NPS_TIER_NAME = "Tier I"
BOND_NPS_TIER_WEIGHT = 0.25


ACTIVE_STOCKS = [
    {
        "name":           "Arista Networks",
        "ticker":         "ANET",
        "isin":           "US0404132054",
        "shares":         124,
        #"cost_inr":       1_241_137.50,
        #"cost_per_share": 9_547.21,
        "cost_inr":       699_369.86925,
        "cost_per_share": 5_379.768225,
        "acquired_on": "20-01-2024",
    },
    {
        "name":           "Oracle Corporation",
        "ticker":         "ORCL",
        "isin":           "US68389X1054",
        "shares":         50,
        "cost_inr":       618_227.104,
        "cost_per_share": 12_364.54208,
        "acquired_on": "05-02-2026",
    },
]

CLOSED_STOCKS = [
    {
        "name":                "Cisco Systems",
        "ticker":              "CSCO",
        "isin":                "US17275R1023",
        "realized_return_inr": 60_800.05,
        "return_pct_pa":       13.7,
        "acquired_on": "20-08-2021",
        "sold_on": "22-12-2023",
    },
]

# ================================================================
# FORMATTING HELPERS
# ================================================================

#DARK_GREEN = "\033[32m"
DARK_GREEN = ""
DARK_RED   = "\033[31m"
YELLOW     = "\033[33m"
BOLD       = "\033[1m"
DIM        = "\033[2m"
RESET      = "\033[0m"


def parse_args():
    parser = argparse.ArgumentParser(description="Track NPS, stock, and PPF portfolio values.")
    live_nav_group = parser.add_mutually_exclusive_group()
    live_nav_group.add_argument(
        "--live-nps-nav",
        dest="live_nps_nav",
        action="store_true",
        help="Revalue NPS holdings with latest npsnav.in NAVs using units from the latest statement CSVs. This is the default.",
    )
    live_nav_group.add_argument(
        "--no-live-nps-nav",
        dest="live_nps_nav",
        action="store_false",
        help="Use NPS values from the latest statement CSVs without fetching live npsnav.in NAVs.",
    )
    parser.set_defaults(live_nps_nav=None)
    return parser.parse_args()


ARGS = parse_args()
LIVE_NPS_NAV_ENV = os.getenv("LIVE_NPS_NAV", "").strip().lower()
if ARGS.live_nps_nav is not None:
    LIVE_NPS_NAV = ARGS.live_nps_nav
elif LIVE_NPS_NAV_ENV:
    LIVE_NPS_NAV = LIVE_NPS_NAV_ENV in {"1", "true", "yes", "on"}
else:
    LIVE_NPS_NAV = True


def load_cache():
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


def save_cache(cache_data):
    tmp_file = CACHE_FILE.with_suffix(".json.tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2, sort_keys=True)
    tmp_file.replace(CACHE_FILE)


def is_cache_valid(timestamp):
    try:
        return (time.time() - float(timestamp)) < CACHE_EXPIRY_SECONDS
    except (TypeError, ValueError):
        return False


def cache_entry(cache_key):
    return _cache.get(cache_key, {})


def cached_previous(cache_key):
    entry = cache_entry(cache_key)
    return entry.get("previous") if entry else None


def cached_fetch(cache_key, fetch_function, force_refresh=False):
    global DATA_FETCHED_LIVE

    entry = _cache.get(cache_key)
    if (
        not force_refresh
        and isinstance(entry, dict)
        and "data" in entry
        and is_cache_valid(entry.get("timestamp"))
    ):
        return entry["data"]

    new_data = fetch_function()
    _cache[cache_key] = {
        "data": new_data,
        "previous": entry.get("data") if isinstance(entry, dict) else None,
        "timestamp": time.time()
    }
    DATA_FETCHED_LIVE = True
    save_cache(_cache)
    return new_data


# Load once at startup
_cache = load_cache()
DATA_FETCHED_LIVE = False


def resolve_data_file(filename):
    path = Path(filename)
    return path if path.is_absolute() else BASE_DIR / path


def remote_json(url: str, timeout: int = HTTP_TIMEOUT_SECONDS) -> dict:
    time.sleep(REQUEST_DELAY_SECONDS)
    req = Request(url, headers={"User-Agent": "nps-portfolio-tracker/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8").strip())


def format_inr(amount):
    amount = round(amount, 2)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    s = f"{amount:.2f}"
    integer, decimal = s.split(".")
    if len(integer) <= 3:
        return f"Rs {sign}{integer}.{decimal}"
    last3     = integer[-3:]
    remaining = integer[:-3]
    indian    = ""
    while len(remaining) > 2:
        indian    = "," + remaining[-2:] + indian
        remaining = remaining[:-2]
    return f"Rs {sign}{remaining + indian},{last3}.{decimal}"


def colorize(value, text):
    if value > 0:
        return f"{DARK_GREEN}{text}{RESET}"
    elif value < 0:
        return f"{DARK_RED}{text}{RESET}"
    return text


def pct_bar(pct, width=20):
    """ASCII progress bar: 1 block per 5 % gain/loss."""
    filled = min(int(abs(pct) / 5), width)
    bar = "=" * filled + "-" * (width - filled)
    color  = DARK_GREEN if pct >= 0 else DARK_RED
    return f"{color}[{bar}]{RESET} {pct:+.1f}%"


def lrow(label, value, width=26):
    print(f"  {label:<{width}} {value}")


def divider(char="-", width=56):
    print(char * width)


def parse_date_flexible(value):
    """Parse common date formats used in config/API responses."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)

    for fmt in (
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%b-%Y",
        "%d %b %Y",
        "%B %d %Y",
        "%b %d %Y",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.isna(dt):
        return None
    return dt.to_pydatetime()


def format_date(value):
    dt = parse_date_flexible(value)
    return dt.strftime("%d-%b-%Y") if dt else "-"


def timestamp_to_datetime(timestamp):
    try:
        return datetime.fromtimestamp(float(timestamp))
    except (TypeError, ValueError, OSError):
        return None


def annualized_return_pct(initial_value, final_value, start_date, end_date=None):
    """Compute CAGR-like annualised return."""
    if initial_value is None or final_value is None:
        return None
    if initial_value <= 0 or final_value <= 0:
        return None

    start_dt = parse_date_flexible(start_date)
    end_dt = parse_date_flexible(end_date) or datetime.now()

    if start_dt is None or end_dt is None or end_dt <= start_dt:
        return None

    years = (end_dt - start_dt).days / 365.25
    if years <= 0:
        return None

    return ((final_value / initial_value) ** (1 / years) - 1) * 100


def period_return_pct(initial_value, final_value):
    if initial_value is None or final_value is None or initial_value <= 0:
        return None
    return ((final_value / initial_value) - 1) * 100


def format_return_pct(value):
    return "--" if value is None else f"{value:+.2f}%"


def subtract_window(end_date, unit, amount):
    end_ts = pd.Timestamp(parse_date_flexible(end_date) or datetime.now())
    if unit == "days":
        return (end_ts - pd.Timedelta(days=amount)).to_pydatetime()
    if unit == "months":
        return (end_ts - pd.DateOffset(months=amount)).to_pydatetime()
    if unit == "years":
        return (end_ts - pd.DateOffset(years=amount)).to_pydatetime()
    raise ValueError(f"Unsupported trailing return window unit: {unit}")


def history_points_from_mapping(mapping):
    points = []
    if not isinstance(mapping, dict):
        return points
    for date_text, value in mapping.items():
        dt = parse_date_flexible(date_text)
        if dt is None:
            continue
        try:
            points.append((dt, float(value)))
        except (TypeError, ValueError):
            continue
    return sorted(points, key=lambda item: item[0])


def history_value_on_or_before(points, target_date):
    target_dt = parse_date_flexible(target_date)
    if target_dt is None:
        return None

    best = None
    for dt, value in points:
        if dt <= target_dt:
            best = (dt, value)
        else:
            break
    return best


def implied_start_date_from_annualized(initial_value, final_value, annualized_pct, end_date):
    """Create an equivalent single cash-flow date from a known annualized return."""
    if initial_value is None or final_value is None or annualized_pct is None:
        return None
    if initial_value <= 0 or final_value <= 0:
        return None

    end_dt = parse_date_flexible(end_date) or datetime.now()
    rate = annualized_pct / 100
    if rate <= -1:
        return None

    if rate == 0:
        return None

    try:
        years = math.log(final_value / initial_value) / math.log(1 + rate)
    except (ValueError, ZeroDivisionError):
        return None

    if years <= 0:
        return None

    return end_dt - pd.Timedelta(days=years * 365.25)


def xirr_pct(cashflows):
    """Compute money-weighted annualized return for dated cashflows."""
    dated = [
        (parse_date_flexible(date), amount)
        for date, amount in cashflows
        if date is not None and amount not in (None, 0)
    ]
    dated = [(date, amount) for date, amount in dated if date is not None]
    if not dated or not any(amount < 0 for _, amount in dated) or not any(amount > 0 for _, amount in dated):
        return None

    start_date = min(date for date, _ in dated)

    def npv(rate):
        return sum(
            amount / ((1 + rate) ** ((date - start_date).days / 365.25))
            for date, amount in dated
        )

    low, high = -0.999999, 10.0
    try:
        low_npv = npv(low)
        high_npv = npv(high)
        while high_npv > 0 and high < 1000:
            high *= 2
            high_npv = npv(high)
    except (OverflowError, ZeroDivisionError, ValueError):
        return None

    if low_npv * high_npv > 0:
        return None

    for _ in range(200):
        mid = (low + high) / 2
        mid_npv = npv(mid)
        if abs(mid_npv) < 0.01:
            return mid * 100
        if low_npv * mid_npv > 0:
            low = mid
            low_npv = mid_npv
        else:
            high = mid

    return ((low + high) / 2) * 100


def parse_money(value):
    text = str(value or "").strip()
    if not text:
        return None
    negative = "(" in text and ")" in text
    cleaned = re.sub(r"[^0-9.\-]", "", text.replace(",", ""))
    if not cleaned:
        return None
    amount = float(cleaned)
    return -abs(amount) if negative else amount


def parse_percent(value):
    text = str(value or "").strip()
    if not text:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    return float(cleaned) if cleaned else None


# ================================================================
# NPS PARSER
# ================================================================

def nps_statement_files(tier):
    pattern = NPS_TIER_FILE_PATTERNS[tier]
    return sorted(BASE_DIR.glob(pattern))


def nps_scheme_code(scheme_name):
    return NPSNAV_SCHEME_CODES.get(str(scheme_name or "").strip())


def parse_nps_scheme_holdings(lines):
    holdings = []
    start_idx = None

    for idx, line in enumerate(lines):
        if line.strip().startswith("Particulars,Scheme wise Value"):
            start_idx = idx + 1
            break

    if start_idx is None:
        return holdings

    for line in lines[start_idx:]:
        if not line.strip():
            if holdings:
                break
            continue

        row = next(csv.reader([line]))
        if len(row) < 4:
            if holdings:
                break
            continue

        scheme_name = row[0].strip()
        if not scheme_name:
            if holdings:
                break
            continue

        holdings.append({
            "scheme_name": scheme_name,
            "scheme_code": nps_scheme_code(scheme_name),
            "statement_value": parse_money(row[1]),
            "units": parse_money(row[2]),
            "statement_nav": parse_money(row[3]),
        })

    return holdings


def parse_nps_statement_summary(filepath, tier):
    path = resolve_data_file(filepath)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()

    as_of_match = re.search(
        r"Value of your Holdings\(Investments\)as on\s*(.*?)\s*\(in Rs\)",
        text,
        re.IGNORECASE,
    )
    as_of_date = parse_date_flexible(as_of_match.group(1)) if as_of_match else None

    generation_match = re.search(r"Statement Generation Date\s*:\s*([^\n\r]+)", text)
    statement_generated_at = parse_date_flexible(generation_match.group(1)) if generation_match else None

    xirr_match = re.search(r"Return on Investment\(XIRR\),\s*([^,]+)", text)
    xirr_pct = parse_percent(xirr_match.group(1)) if xirr_match else None

    summary_row = None
    for idx, line in enumerate(lines):
        if line.strip().startswith("(A)"):
            for candidate in lines[idx + 1:]:
                row = next(csv.reader([candidate]))
                if row and row[0].strip().startswith("Rs"):
                    summary_row = row
                    break
            break
    if not summary_row:
        raise ValueError(f"Investment summary row not found in {path.name}")

    return {
        "tier": tier,
        "source_file": path.name,
        "as_of_date": as_of_date,
        "statement_generated_at": statement_generated_at,
        "current_value": parse_money(summary_row[0]),
        "contribution_count": int(parse_money(summary_row[1]) or 0),
        "invested": parse_money(summary_row[2]),
        "withdrawals": parse_money(summary_row[3]) or 0.0,
        "notional_gain": parse_money(summary_row[4]),
        "charges": parse_money(summary_row[5]) if len(summary_row) > 5 else None,
        "annualized_pct": xirr_pct,
        "scheme_holdings": parse_nps_scheme_holdings(lines),
        "value_source": "statement",
    }


def parse_nps_file(filepath):
    path = resolve_data_file(filepath)
    # Locate contribution table
    start_idx = None
    with path.open("r", encoding="utf-8-sig") as f:
        for i, line in enumerate(f):
            if line.strip().startswith("Date,Particulars"):
                start_idx = i
                break
    if start_idx is None:
        raise ValueError(f"Contribution table not found in {path}")

    df = pd.read_csv(path, skiprows=start_idx, engine="python", on_bad_lines="skip")
    df = df.iloc[:, :6]
    df.columns = [
        "Date", "Particulars", "Uploaded By",
        "Employee Contribution", "Employer Contribution", "Total",
    ]
    df["Date"]  = df["Date"].map(parse_date_flexible)
    df["Total"] = pd.to_numeric(df["Total"], errors="coerce")
    df = df.dropna(subset=["Date", "Total"])

    return df


def load_nps_tier_from_statements(tier):
    files = nps_statement_files(tier)
    if not files:
        raise FileNotFoundError(f"No NPS statement files found for {tier}")

    summaries = [parse_nps_statement_summary(path, tier) for path in files]
    summaries.sort(
        key=lambda item: (
            item.get("as_of_date") or datetime.min,
            item.get("statement_generated_at") or datetime.min,
            item.get("source_file", ""),
        )
    )
    latest = summaries[-1].copy()

    contribution_frames = []
    for path in files:
        try:
            contribution_frames.append(parse_nps_file(path))
        except Exception:
            pass
    if contribution_frames:
        contributions = pd.concat(contribution_frames, ignore_index=True)
        start_date = contributions["Date"].min()
        latest_contribution_date = contributions["Date"].max()
    else:
        start_date = None
        latest_contribution_date = None

    latest["statement_count"] = len(files)
    latest["start_date"] = start_date
    latest["latest_contribution_date"] = latest_contribution_date
    return latest


# ================================================================
# ALPHA VANTAGE PRICE FETCHER
#
# Requires: ALPHAVANTAGE_API_KEY environment variable
# ================================================================

def fetch_alpha_vantage_json(params: dict) -> dict:
    """Run one Alpha Vantage request and return parsed JSON."""
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "ALPHAVANTAGE_API_KEY environment variable is not set.\n"
            "  export ALPHAVANTAGE_API_KEY=your_key_here"
        )

    query = urlencode({**params, "apikey": api_key})
    return remote_json(f"https://www.alphavantage.co/query?{query}")


def fetch_stock_price_usd(ticker: str) -> dict:
    """
    Fetch latest price for a US-listed stock (in USD).
    Uses GLOBAL_QUOTE → field '05. price', same as your shell alias.
    """
    #time.sleep(1.1)
    data = fetch_alpha_vantage_json({"function": "GLOBAL_QUOTE", "symbol": ticker})
    quote = data.get("Global Quote", {})
    price = quote.get("05. price")
    if not price:
        raise ValueError(
            f"No price data for {ticker}. "
            f"Response: {json.dumps(data)[:200]}"
        )
    return {
        "price": float(price),
        "latest_trading_day": quote.get("07. latest trading day"),
    }


def fetch_stock_weekly_adjusted_history(ticker: str) -> dict:
    """Fetch adjusted weekly close history for a US-listed stock."""
    data = fetch_alpha_vantage_json({
        "function": "TIME_SERIES_WEEKLY_ADJUSTED",
        "symbol": ticker,
    })
    series = (
        data.get("Weekly Adjusted Time Series")
        or data.get("Time Series (Weekly)")
    )
    if not isinstance(series, dict):
        raise ValueError(
            f"No weekly history for {ticker}. "
            f"Response: {json.dumps(data)[:200]}"
        )

    history = {}
    for date_text, row in series.items():
        if not isinstance(row, dict):
            continue
        price = row.get("5. adjusted close") or row.get("4. close")
        if price is None:
            continue
        try:
            history[date_text] = float(price)
        except (TypeError, ValueError):
            continue
    return history


def stock_quote_price(quote):
    if isinstance(quote, dict):
        quote = quote.get("price")
    return float(quote)


def stock_quote_date(quote):
    if isinstance(quote, dict):
        return parse_date_flexible(
            quote.get("latest_trading_day")
            or quote.get("as_of_date")
            or quote.get("date")
        )
    return None


def current_cache_date(cache_key):
    entry = cache_entry(cache_key)
    if not isinstance(entry, dict):
        return None
    return stock_quote_date(entry.get("data")) or timestamp_to_datetime(entry.get("timestamp"))


def fetch_usd_inr() -> float:
    """Fetch live USD → INR exchange rate via Alpha Vantage CURRENCY_EXCHANGE_RATE."""
    data = fetch_alpha_vantage_json({
        "function":      "CURRENCY_EXCHANGE_RATE",
        "from_currency": "USD",
        "to_currency":   "INR",
    })
    rate = (
        data.get("Realtime Currency Exchange Rate", {})
            .get("5. Exchange Rate")
    )
    if not rate:
        raise ValueError(f"No FX data returned. Response: {json.dumps(data)[:200]}")
    return float(rate)


def fetch_usd_inr_weekly_history() -> dict:
    """Fetch USD/INR weekly close history."""
    data = fetch_alpha_vantage_json({
        "function": "FX_WEEKLY",
        "from_symbol": "USD",
        "to_symbol": "INR",
    })
    series = data.get("Time Series FX (Weekly)")
    if not isinstance(series, dict):
        raise ValueError(f"No USD/INR weekly history. Response: {json.dumps(data)[:200]}")

    history = {}
    for date_text, row in series.items():
        if not isinstance(row, dict):
            continue
        close = row.get("4. close")
        if close is None:
            continue
        try:
            history[date_text] = float(close)
        except (TypeError, ValueError):
            continue
    return history


def fetch_npsnav_latest_min():
    """Fetch latest NPS NAVs from npsnav.in in compact scheme_code -> NAV form."""
    data = remote_json(NPSNAV_LATEST_MIN_URL)
    rows = data.get("data")
    if not isinstance(rows, list):
        raise ValueError(f"No NPS NAV data returned. Response: {json.dumps(data)[:200]}")
    return data


def fetch_npsnav_historical_scheme(scheme_code):
    """Fetch historical NAVs for one NPS scheme from npsnav.in."""
    data = remote_json(NPSNAV_HISTORICAL_URL_TEMPLATE.format(scheme_code=scheme_code))
    rows = data.get("data")
    if not isinstance(rows, list):
        raise ValueError(
            f"No NPS historical NAV data for {scheme_code}. "
            f"Response: {json.dumps(data)[:200]}"
        )

    history = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        date_text = row.get("date")
        nav = row.get("nav")
        if not date_text or nav is None:
            continue
        try:
            history[date_text] = float(nav)
        except (TypeError, ValueError):
            continue
    return history


def revalue_nps_tiers_with_live_nav(tiers):
    latest = fetch_npsnav_latest_min()
    nav_by_code = {}
    metadata = latest.get("metadata", {})
    nav_date = parse_date_flexible(metadata.get("lastUpdated"))
    errors = []

    for row in latest.get("data", []):
        if not isinstance(row, list) or len(row) < 2:
            continue
        try:
            nav_by_code[str(row[0])] = float(row[1])
        except (TypeError, ValueError):
            continue

    for tier, data in tiers.items():
        holdings = data.get("scheme_holdings") or []
        if not holdings:
            errors.append(f"{tier}: scheme-wise units not found in latest statement")
            continue

        missing = []
        live_value = 0.0
        for holding in holdings:
            code = holding.get("scheme_code")
            units = holding.get("units")
            if not code:
                missing.append(f"{holding.get('scheme_name')} has no configured npsnav scheme code")
                continue
            if code not in nav_by_code:
                missing.append(f"{holding.get('scheme_name')} [{code}] not found in npsnav latest NAVs")
                continue
            if units is None:
                missing.append(f"{holding.get('scheme_name')} [{code}] has no statement units")
                continue

            live_nav = nav_by_code[code]
            holding["live_nav"] = live_nav
            holding["live_value"] = units * live_nav
            holding["live_nav_date"] = nav_date
            live_value += holding["live_value"]

        if missing:
            errors.extend(f"{tier}: {item}" for item in missing)
            continue

        statement_value = data.get("current_value")
        data["statement_current_value"] = statement_value
        data["statement_notional_gain"] = data.get("notional_gain")
        data["current_value"] = live_value
        data["notional_gain"] = live_value + (data.get("withdrawals") or 0.0) - (data.get("invested") or 0.0)
        data["value_source"] = "live_npsnav"
        data["npsnav_as_of_date"] = nav_date
        data["npsnav_metadata"] = metadata
        if statement_value is not None:
            data["live_value_delta"] = live_value - statement_value
            data["live_value_delta_pct"] = (
                (data["live_value_delta"] / statement_value) * 100
                if statement_value
                else None
            )

    return errors


def build_trailing_return_assets(tiers, stocks, usd_inr):
    """Build current-holdings assets that can be valued at prior dates."""
    assets = []
    issues = []

    for tier, data in sorted(tiers.items()):
        tier_date = (
            data.get("npsnav_as_of_date")
            or data.get("as_of_date")
            or datetime.now()
        )
        for holding in data.get("scheme_holdings") or []:
            scheme_code = holding.get("scheme_code")
            units = holding.get("units")
            current_value = holding.get("live_value")
            if current_value is None:
                current_value = holding.get("statement_value")
            current_date = (
                holding.get("live_nav_date")
                or data.get("npsnav_as_of_date")
                or tier_date
            )

            if not scheme_code or units is None or current_value is None:
                continue

            try:
                history = cached_fetch(
                    f"NPS_HIST_{scheme_code}",
                    lambda code=scheme_code: fetch_npsnav_historical_scheme(code),
                )
                points = history_points_from_mapping(history)
            except Exception as e:
                issues.append(f"NPS {scheme_code}: {e}")
                continue

            def historical_value(target_date, points=points, units=units):
                point = history_value_on_or_before(points, target_date)
                if point is None:
                    return None
                dt, nav = point
                return dt, units * nav

            assets.append({
                "name": f"{tier} {scheme_code}",
                "current_value": current_value,
                "current_date": parse_date_flexible(current_date) or datetime.now(),
                "historical_value": historical_value,
            })

    fx_points = []
    if usd_inr:
        try:
            fx_history = cached_fetch("USD_INR_WEEKLY_HISTORY", fetch_usd_inr_weekly_history)
            fx_points = history_points_from_mapping(fx_history)
        except Exception as e:
            issues.append(f"USD/INR history unavailable; using current FX for stock trailing returns: {e}")

    for stock in stocks:
        if stock.get("cur_value") is None or stock.get("price_usd") is None:
            continue

        ticker = stock["ticker"]
        try:
            price_history = cached_fetch(
                f"STOCK_WEEKLY_ADJ_{ticker}",
                lambda t=ticker: fetch_stock_weekly_adjusted_history(t),
            )
            price_points = history_points_from_mapping(price_history)
        except Exception as e:
            issues.append(f"{ticker} weekly history: {e}")
            continue

        def historical_value(
            target_date,
            price_points=price_points,
            fx_points=fx_points,
            shares=stock["shares"],
            fallback_fx=usd_inr,
        ):
            price_point = history_value_on_or_before(price_points, target_date)
            if price_point is None:
                return None
            price_dt, price_usd = price_point

            fx = fallback_fx
            fx_point = history_value_on_or_before(fx_points, target_date)
            if fx_point is not None:
                fx = fx_point[1]
            if fx is None:
                return None

            return price_dt, shares * price_usd * fx

        assets.append({
            "name": ticker,
            "current_value": stock["cur_value"],
            "current_date": stock.get("price_as_of_date") or datetime.now(),
            "historical_value": historical_value,
        })

    return assets, issues


def compute_trailing_annualized_returns(tiers, stocks, usd_inr):
    assets, issues = build_trailing_return_assets(tiers, stocks, usd_inr)
    assets = [
        asset for asset in assets
        if asset.get("current_value") is not None
        and parse_date_flexible(asset.get("current_date")) is not None
    ]

    if not assets:
        return [], issues

    end_date = min(parse_date_flexible(asset["current_date"]) for asset in assets)
    total_current_value = sum(asset["current_value"] for asset in assets)
    results = []

    for label, unit, amount in TRAILING_RETURN_WINDOWS:
        target_date = subtract_window(end_date, unit, amount)
        start_total = 0.0
        end_total = 0.0
        included_current_value = 0.0
        missing_assets = []

        for asset in assets:
            historical = asset["historical_value"](target_date)
            if historical is None:
                missing_assets.append(asset["name"])
                continue
            _, historical_value = historical
            if historical_value is None or historical_value <= 0:
                missing_assets.append(asset["name"])
                continue

            start_total += historical_value
            end_total += asset["current_value"]
            included_current_value += asset["current_value"]

        total_pct = period_return_pct(start_total, end_total)
        annualized_pct = annualized_return_pct(start_total, end_total, target_date, end_date)
        coverage_pct = (
            (included_current_value / total_current_value) * 100
            if total_current_value
            else 0.0
        )

        results.append({
            "label": label,
            "start_date": target_date,
            "end_date": end_date,
            "total_pct": total_pct,
            "annualized_pct": annualized_pct,
            "coverage_pct": coverage_pct,
            "missing_assets": missing_assets,
        })

    return results, issues

# ================================================================
# MAIN — NPS (official values/XIRR from statement CSVs)
# ================================================================

tier_data = {}
nps_statement_errors = []

for tier in NPS_TIER_FILE_PATTERNS:
    try:
        tier_data[tier] = load_nps_tier_from_statements(tier)
    except Exception as e:
        nps_statement_errors.append(f"{tier}: {e}")
        tier_data[tier] = {
            "source_file": None,
            "invested": 0.0,
            "current_value": None,
            "notional_gain": None,
            "annualized_pct": None,
            "start_date": None,
            "scheme_holdings": [],
            "value_source": "statement",
        }

if LIVE_NPS_NAV:
    try:
        nps_statement_errors.extend(revalue_nps_tiers_with_live_nav(tier_data))
    except Exception as e:
        nps_statement_errors.append(f"Live NPS NAV: {e}")

nps_invested = sum(d["invested"] for d in tier_data.values() if d.get("invested") is not None)
nps_value = sum(d["current_value"] for d in tier_data.values() if d.get("current_value") is not None)


# ================================================================
# MAIN — STOCKS
# ================================================================

active_stocks, closed_stocks = ACTIVE_STOCKS, CLOSED_STOCKS

# Fetch live USD prices + FX rate
stock_fetch_ok = False
usd_inr        = None
price_errors   = []

try:
    usd_inr = cached_fetch("USD_INR", fetch_usd_inr)

    for stock in active_stocks:
        try:
            cache_key = f"STOCK_{stock['ticker']}"
            quote = cached_fetch(
                cache_key,
                lambda t=stock["ticker"]: fetch_stock_price_usd(t),
            )
            price_usd = stock_quote_price(quote)
            stock["price_usd"] = price_usd
            stock["price_as_of_date"] = stock_quote_date(quote) or current_cache_date(cache_key)
            stock["price_inr"] = price_usd * usd_inr
            stock["cur_value"] = price_usd * usd_inr * stock["shares"]
            stock["gain_abs"]  = stock["cur_value"] - stock["cost_inr"]
            stock["gain_pct"]  = (stock["gain_abs"] / stock["cost_inr"]) * 100
            stock["annualized_pct"] = annualized_return_pct(
                stock["cost_inr"],
                stock["cur_value"],
                stock.get("acquired_on") or stock.get("purchase_date") or stock.get("start_date"),
            )
            # -------------------------------
            # 🔽 ADD DAILY CHANGE HERE
            # -------------------------------

            previous_quote = cached_previous(cache_key)
            if previous_quote is not None:
                prev = stock_quote_price(previous_quote)
                curr = float(price_usd)

                daily_abs_usd = curr - prev
                daily_pct = (daily_abs_usd / prev) * 100

                stock["daily_abs"] = daily_abs_usd * usd_inr * stock["shares"]
                stock["daily_pct"] = daily_pct
            else:
                stock["daily_abs"] = None
                stock["daily_pct"] = None
        except Exception as e:
            price_errors.append(f"{stock['ticker']}: {e}")
            stock["price_usd"] = stock["price_inr"] = None
            stock["cur_value"] = stock["gain_abs"] = stock["gain_pct"] = stock["annualized_pct"] = None

    stock_fetch_ok = True

except EnvironmentError as e:
    # API key not set — run in offline mode
    print(f"\n  ⚠  {e}{RESET}\n")

except Exception as e:
    print(f"\n  ⚠  Could not fetch live prices: {e}{RESET}\n")

# Aggregate stock totals
active_invested   = sum(s["cost_inr"] for s in active_stocks)
active_cur_value  = sum(s["cur_value"] for s in active_stocks if s.get("cur_value"))
realized_gains    = sum(s["realized_return_inr"] for s in closed_stocks)

ppf_holdings = [holding.copy() for holding in PPF_HOLDINGS]
ppf_invested = sum(h["invested"] for h in ppf_holdings)
ppf_value = sum(h["current_value"] for h in ppf_holdings)
portfolio_current_value = nps_value + active_cur_value + ppf_value

stocks_have_value = any(s.get("cur_value") is not None for s in active_stocks)

stock_start_dates = [
    parse_date_flexible(s.get("acquired_on") or s.get("purchase_date") or s.get("start_date"))
    for s in active_stocks
]
stock_start_dates = [d for d in stock_start_dates if d is not None]
stocks_annualized_pct = None
if stocks_have_value and len(stock_start_dates) == len(active_stocks) and stock_start_dates:
    stocks_annualized_pct = annualized_return_pct(
        active_invested,
        active_cur_value,
        min(stock_start_dates),
    )

# -------------------------------
# TOTAL DAILY CHANGE
# -------------------------------

# Stocks daily
stocks_daily = sum(
    s.get("daily_abs", 0) for s in active_stocks
    if s.get("daily_abs") is not None
)

# NPS live movement uses the same statement-to-live-NAV delta shown above.
nps_daily = sum(
    d.get("live_value_delta", 0) for d in tier_data.values()
    if d.get("value_source") == "live_npsnav" and d.get("live_value_delta") is not None
)

combined_daily = stocks_daily + nps_daily

overall_today_dates = []
for data in tier_data.values():
    if data.get("value_source") == "live_npsnav":
        overall_today_dates.append(data.get("npsnav_as_of_date"))
for stock in active_stocks:
    if stock.get("cur_value") is not None:
        overall_today_dates.append(stock.get("price_as_of_date"))

overall_today_dates = [
    parse_date_flexible(date) for date in overall_today_dates
    if parse_date_flexible(date) is not None
]
overall_today_as_of = min(overall_today_dates) if overall_today_dates else None

trailing_return_results, trailing_return_issues = compute_trailing_annualized_returns(
    tier_data,
    active_stocks,
    usd_inr,
)


# ================================================================
# OUTPUT
# ================================================================

#divider("=")

#if DATA_FETCHED_LIVE:
#    print("  DATA SOURCE: LIVE FETCH (API)")
#else:
#    print("  DATA SOURCE: CACHE (within 12h)")

#divider("=")
#print()

#print()
divider("=")
print(f"{BOLD}  EQUITY PORTFOLIO TRACKER{RESET}")
divider("=")

# ── NPS ─────────────────────────────────────────────────────────
for tier, data in sorted(tier_data.items()):
    invested = data["invested"]
    cv = data["current_value"]

    print(f"  {BOLD}{tier}{RESET}  {DIM}({data.get('source_file') or 'statement unavailable'}){RESET}")
    if data.get("as_of_date") is not None:
        print(
            f"  {DIM}Statement as of: {format_date(data['as_of_date'])} | "
            f"Files scanned: {data.get('statement_count', 0)}{RESET}"
        )
    if data.get("value_source") == "live_npsnav":
        print(
            f"  {DIM}Live NAV as of: {format_date(data.get('npsnav_as_of_date'))} | "
            f"Source: npsnav.in latest-min{RESET}"
        )

    if cv is not None:
        weight_pct = (cv / portfolio_current_value) * 100 if portfolio_current_value > 0 else 0
        withdrawals = data.get("withdrawals") or 0.0
        g = data.get("notional_gain")
        if g is None:
            g = cv + withdrawals - invested
        pct = (g / invested) * 100 if invested else 0
        lrow("    Contributions", format_inr(invested))
        if withdrawals:
            lrow("    Withdrawals", format_inr(withdrawals))
        value_label = "    Live Value" if data.get("value_source") == "live_npsnav" else "    Value"
        lrow(value_label,    format_inr(cv))
        if data.get("value_source") == "live_npsnav":
            statement_value = data.get("statement_current_value")
            if statement_value is not None:
                lrow("    Statement Value", format_inr(statement_value))
            if data.get("live_value_delta") is not None:
                delta = data["live_value_delta"]
                delta_pct = data.get("live_value_delta_pct")
                delta_text = format_inr(delta)
                if delta_pct is not None:
                    delta_text = f"{delta_text}  ({delta_pct:+.2f}%)"
                lrow("    Live NAV Delta", colorize(delta, delta_text))
        lrow("    Return",   colorize(g, f"{format_inr(g)}  ({pct:+.2f}%)"))
        if data.get("annualized_pct") is not None:
            lrow("    Annualized XIRR", f"{data['annualized_pct']:+.2f}% pa")
        lrow("    Return Bar",   pct_bar(pct))
        lrow("    Portfolio %", f"{weight_pct:.2f}%")
    else:
        lrow("    Contributions", format_inr(invested))
        lrow("    Value",    f"-- statement parse failed{RESET}")
    print()

if nps_statement_errors:
    print(f"  {DIM}NPS issues:{RESET}")
    for e in nps_statement_errors:
        print(f"    - {e}")
    print()

# ── Active Stock Positions ───────────────────────────────────────
print(f"\n\n{BOLD}  STOCK INVESTMENTS  (US Equities — live via Alpha Vantage){RESET}")
divider()

if usd_inr:
    print(f"  {DIM}USD/INR: {usd_inr:.4f}{RESET}\n")

for stock in active_stocks:
    stock_value = stock.get("cur_value")
    weight_pct = (stock_value / portfolio_current_value) * 100 if stock_value is not None and portfolio_current_value > 0 else 0
    print(f"  {BOLD}{stock['name']}  [{stock['ticker']}]{RESET}")
    print(f"  {DIM}ISIN: {stock['isin']}   |   Shares: {stock['shares']}{RESET}")
    print()

    lrow("    Cost Basis",       format_inr(stock["cost_inr"]))
    lrow("    Avg Cost/Share",   format_inr(stock["cost_per_share"]))

    if stock.get("price_usd") is not None:
        lrow("    Live Price",   f"${stock['price_usd']:.2f}  (Rs {stock['price_inr']:,.2f})")
        lrow("    Current Value",format_inr(stock["cur_value"]))
        lrow("    Gain / Loss",  colorize(stock["gain_abs"],
                                   f"{format_inr(stock['gain_abs'])}  "
                                   f"({stock['gain_pct']:+.2f}%)"))
        if stock.get("annualized_pct") is not None:
            lrow("    Annualized", f"{stock['annualized_pct']:+.2f}% pa")
        lrow("    Return Bar",   pct_bar(stock["gain_pct"]))
        if stock.get("daily_abs") is not None:
            lrow("    Today", colorize(stock["daily_abs"], f"{format_inr(stock['daily_abs'])}  ({stock['daily_pct']:+.2f}%)"))
            #lrow("    Today Bar", pct_bar(daily_pct))
        lrow("    Portfolio %", f"{weight_pct:.2f}%")
    else:
        lrow("    Live Price",   f"-- (offline){RESET}")
        lrow("    Current Value",f"-- run with ALPHAVANTAGE_API_KEY set{RESET}")

    print()

for err in price_errors:
    print(f"  ⚠  {err}{RESET}")

# ── Closed / Sold Positions ──────────────────────────────────────
if closed_stocks:
    print()
    print(f"  {DIM}CLOSED POSITIONS (Realized P&L){RESET}")
    divider()
    for stock in closed_stocks:
        ret  = stock["realized_return_inr"]
        pct  = stock.get("return_pct_pa")
        pct_str = f"  ({pct:+.1f}% pa)" if pct else ""
        print(f"  {stock['name']}  [{stock['ticker']}]  {DIM}sold{RESET}")
        lrow("    Realized Return", colorize(ret, f"{format_inr(ret)}{pct_str}"))
        print()

# ── Stocks Summary ───────────────────────────────────────────────
divider()
lrow("  Active Cost Basis",   format_inr(active_invested))

if stocks_have_value:
    unrealized = active_cur_value - active_invested
    total_pnl  = unrealized + realized_gains
    lrow("  Active Value",   format_inr(active_cur_value))
    lrow("  Unrealized P&L", colorize(unrealized,
                               f"{format_inr(unrealized)}  "
                               f"({unrealized / active_invested * 100:+.2f}%)"))
    if stocks_annualized_pct is not None:
        lrow("  Annualized Return", f"{stocks_annualized_pct:+.2f}% pa")
if realized_gains:
    lrow("  Realized P&L",   colorize(realized_gains, format_inr(realized_gains)))

if stocks_have_value and realized_gains:
    total_pnl = (active_cur_value - active_invested) + realized_gains
    lrow("  Total Stocks P&L", colorize(total_pnl, format_inr(total_pnl)))


# ── Other Investments ─────────────────────────────────────────────
if ppf_holdings:
    print(f"\n\n{BOLD}  OTHER INVESTMENTS  (statement snapshot){RESET}")
    divider()

    for holding in ppf_holdings:
        invested = holding["invested"]
        cv = holding["current_value"]
        gain = cv - invested
        pct = (gain / invested) * 100 if invested else 0
        weight_pct = (cv / portfolio_current_value) * 100 if portfolio_current_value > 0 else 0

        print(f"  {BOLD}{holding['name']}{RESET}  {DIM}[PPF]{RESET}")
        print(
            f"  {DIM}Source: {holding['source_file']} | "
            f"As of: {format_date(holding.get('as_of_date'))}{RESET}"
        )
        print()
        lrow("    Invested", format_inr(invested))
        lrow("    Value", format_inr(cv))
        lrow("    Return", colorize(gain, f"{format_inr(gain)}  ({pct:+.2f}%)"))
        if holding.get("annualized_pct") is not None:
            lrow("    Annualized XIRR", f"{holding['annualized_pct']:+.2f}% pa")
        lrow("    Portfolio %", f"{weight_pct:.2f}%")
        print()


# ── Combined Summary ─────────────────────────────────────────────
print(f"\n\n{BOLD}  COMBINED PORTFOLIO SUMMARY{RESET}")
divider("=")
print()

total_invested = nps_invested + active_invested + ppf_invested
nps_withdrawals = sum(d.get("withdrawals") or 0.0 for d in tier_data.values())

combined_cashflows = []
total_value = portfolio_current_value

for data in tier_data.values():
    invested = data.get("invested")
    cv = data.get("current_value")
    if invested is None or cv is None:
        continue
    statement_as_of_date = data.get("as_of_date") or datetime.now()
    final_date = data.get("npsnav_as_of_date") if data.get("value_source") == "live_npsnav" else statement_as_of_date
    final_date = final_date or statement_as_of_date
    statement_proceeds = (data.get("statement_current_value") or cv) + (data.get("withdrawals") or 0.0)
    proceeds = cv + (data.get("withdrawals") or 0.0)
    start_date = implied_start_date_from_annualized(
        invested,
        statement_proceeds,
        data.get("annualized_pct"),
        statement_as_of_date,
    )
    if start_date is None:
        start_date = data.get("start_date")
    combined_cashflows.extend([
        (start_date, -invested),
        (final_date, proceeds),
    ])

for stock in active_stocks:
    if stock.get("cur_value") is None:
        continue
    acquired_on = parse_date_flexible(stock.get("acquired_on") or stock.get("purchase_date") or stock.get("start_date"))
    if acquired_on is None:
        continue
    combined_cashflows.extend([
        (acquired_on, -stock["cost_inr"]),
        (datetime.now(), stock["cur_value"]),
    ])

for holding in ppf_holdings:
    as_of_date = parse_date_flexible(holding.get("as_of_date")) or datetime.now()
    start_date = implied_start_date_from_annualized(
        holding.get("invested"),
        holding.get("current_value"),
        holding.get("annualized_pct"),
        as_of_date,
    )
    combined_cashflows.extend([
        (start_date, -holding["invested"]),
        (as_of_date, holding["current_value"]),
    ])

combined_xirr_pct = xirr_pct(combined_cashflows)

if stocks_have_value:
    total_proceeds = total_value + nps_withdrawals
    total_gain   = total_proceeds - total_invested
    total_return = (total_gain / total_invested) * 100

    lrow("  Total Invested",  format_inr(total_invested))
    lrow("  Current Value",   format_inr(total_value))
    if nps_withdrawals:
        lrow("  NPS Withdrawals", format_inr(nps_withdrawals))
    lrow("  Return Gain",     colorize(total_gain, f"{format_inr(total_gain)}  ({total_return:+.2f}%)"))
    if combined_xirr_pct is not None:
        lrow("  Overall XIRR", f"{combined_xirr_pct:+.2f}% pa")
    lrow("  Overall Return",  pct_bar(total_return, width=30))
    if combined_daily is not None:
        daily_pct = (combined_daily / total_value) * 100
        today_label = "  Today"
        if overall_today_as_of is not None:
            today_label = f"  Today ({format_date(overall_today_as_of)})"
        lrow(today_label, colorize(combined_daily, f"{format_inr(combined_daily)}  ({daily_pct:+.2f}%)"))
        #lrow("  Today Bar", pct_bar(daily_pct, width=30)        )
else:
    lrow("  Total Invested",  format_inr(total_invested))
    lrow("  NPS + PPF Value", format_inr(nps_value + ppf_value))
    lrow("  Stocks Value",    f"-- set ALPHAVANTAGE_API_KEY for live prices{RESET}")

if trailing_return_results:
    print()
    print(f"{BOLD}  TRAILING ANNUALIZED RETURNS  (current NPS + active stock holdings){RESET}")
    divider()
    for result in trailing_return_results:
        if result.get("annualized_pct") is None:
            value_text = "-- insufficient history"
        else:
            value_text = (
                f"{format_return_pct(result['annualized_pct'])} pa"
                f"  ({format_return_pct(result['total_pct'])} total;"
                f" {result['coverage_pct']:.0f}% covered)"
            )
            if result.get("missing_assets"):
                value_text += f"; {len(result['missing_assets'])} missing"
        lrow(f"  {result['label']}", value_text)
    print(f"{DIM}  Note: trailing returns use current NPS scheme units and active stock share counts. PPF is excluded because only a statement snapshot is available.{RESET}")
    if trailing_return_issues:
        print(f"{DIM}  Trailing return data issues:{RESET}")
        for issue in trailing_return_issues:
            print(f"{DIM}    - {issue}{RESET}")

divider()

if total_value > 0:
    nps_weight = (nps_value / total_value) * 100
    stocks_weight = (active_cur_value / total_value) * 100

    #lrow("  NPS Allocation", f"{nps_weight:.2f}%")
    #lrow("  Stocks Allocation", f"{stocks_weight:.2f}%")

    bond_ppf_value = sum(
        h.get("current_value") or 0.0
        for h in ppf_holdings
        if h.get("name") == BOND_PPF_HOLDING_NAME
    )
    bond_nps_tier_value = (tier_data.get(BOND_NPS_TIER_NAME, {}).get("current_value") or 0.0) * BOND_NPS_TIER_WEIGHT
    bond_value = bond_ppf_value + bond_nps_tier_value
    equity_value = total_value - bond_value
    bond_pct = (bond_value / total_value) * 100
    equity_pct = (equity_value / total_value) * 100

    print(f"\n{BOLD}  BOND / EQUITY ALLOCATION{RESET}")
    divider()
    lrow("  Bonds", f"{format_inr(bond_value)}  ({bond_pct:.2f}%)")
    lrow("  Equity", f"{format_inr(equity_value)}  ({equity_pct:.2f}%)")

live_nps_values_used = any(d.get("value_source") == "live_npsnav" for d in tier_data.values())
nps_value_note = (
    "NPS value uses live npsnav.in NAVs with units from the latest statement CSV."
    if live_nps_values_used
    else "NPS value uses the latest statement CSV."
)
print(f"{DIM}  Note: {nps_value_note} NPS annualized return uses the XIRR from the latest statement CSV. PPF uses the holdings-statement snapshot. Overall XIRR combines equivalent dated cashflows for NPS/PPF with actual ANET/ORCL purchase dates; CSCO sale proceeds are excluded.{RESET}")
divider("=")
print()
