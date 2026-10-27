"""
Datos de opciones gratuitos:
  - BTC  -> Deribit (API publica): open interest, IV y cada operacion con su direccion.
  - GLD  -> CBOE (delayed quotes, 15 min): open interest, IV y volumen por contrato.
           Se convierte a XAUUSD con el ratio PAXG/GLD (PAXG = 1 onza de oro tokenizada).

Calcula: GEX neto (USD por 1% de movimiento), Call Wall, Put Wall, Gamma flip,
Max Pain del vencimiento mas cercano y flujos (comprado / vendido) de BTC.

Convencion de GEX (la clasica, como SpotGamma): calls suman, puts restan.
"""

import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

DERIBIT = "https://www.deribit.com/api/v2/public"
CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
BINANCE = "https://data-api.binance.vision/api/v3/ticker/price"
UA = {"User-Agent": "Mozilla/5.0 (BTA-TD9-bot)"}


@dataclass
class Opt:
    strike: float
    expiry: datetime      # UTC
    is_call: bool
    oi: float             # contratos
    iv: float             # decimal (0.42 = 42%)


# --------------------------------------------------------------- matematica ---
def _pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_gamma(S, K, T, iv):
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return 0.0
    v = iv * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / v
    return _pdf(d1) / (S * v)


def _years(expiry, now):
    return (expiry - now).total_seconds() / (365.0 * 86400)


def gex_at(opts, S, mult, now):
    tot = 0.0
    for o in opts:
        g = bs_gamma(S, o.strike, _years(o.expiry, now), o.iv) * o.oi * mult * S * S * 0.01
        tot += g if o.is_call else -g
    return tot


def max_pain(opts):
    strikes = sorted({o.strike for o in opts})
    if not strikes:
        return None
    best, best_val = None, None
    for P in strikes:
        pain = sum(o.oi * (max(P - o.strike, 0) if o.is_call else max(o.strike - P, 0)) for o in opts)
        if best_val is None or pain < best_val:
            best, best_val = P, pain
    return best


def levels(opts, spot, mult, now=None, max_days=90, grid_pct=0.20, steps=161, wall_range=0.20):
    """Devuelve dict con gex neto, call/put wall, gamma flip y max pain."""
    now = now or datetime.now(timezone.utc)
    opts = [o for o in opts if 0 < _years(o.expiry, now) <= max_days / 365.0 and o.oi > 0 and o.iv > 0]
    if not opts:
        return None
    # Walls por open interest (resistencia / soporte). El strike de mayor gamma
    # (zona de "pin") se informa aparte: suele estar pegado al precio por los
    # vencimientos cercanos y por eso no sirve como wall.
    call_oi, put_oi, gam = defaultdict(float), defaultdict(float), defaultdict(float)
    for o in opts:
        g = bs_gamma(spot, o.strike, _years(o.expiry, now), o.iv) * o.oi * mult * spot * spot * 0.01
        gam[o.strike] += g
        if abs(o.strike / spot - 1) <= wall_range:  # ignora strikes "loteria" muy lejanos
            (call_oi if o.is_call else put_oi)[o.strike] += o.oi
    call_wall = max(call_oi, key=call_oi.get) if call_oi else None
    put_wall = max(put_oi, key=put_oi.get) if put_oi else None
    gamma_strike = max(gam, key=gam.get) if gam else None

    # Gamma flip: donde el GEX total cruza cero (el cruce mas cercano al spot)
    grid = [spot * (1 - grid_pct + 2 * grid_pct * i / (steps - 1)) for i in range(steps)]
    vals = [gex_at(opts, s, mult, now) for s in grid]
    flips = []
    for i in range(1, steps):
        a, b = vals[i - 1], vals[i]
        if a == 0 or (a < 0) != (b < 0):
            x = grid[i - 1] + (grid[i] - grid[i - 1]) * (0 - a) / (b - a) if b != a else grid[i]
            flips.append(x)
    flip = min(flips, key=lambda x: abs(x - spot)) if flips else None

    # Max Pain del vencimiento con mas OI en los proximos 10 dias (no el diario chico)
    oi_by_exp = defaultdict(float)
    for o in opts:
        if _years(o.expiry, now) <= 10 / 365.0:
            oi_by_exp[o.expiry] += o.oi
    near_exp = max(oi_by_exp, key=oi_by_exp.get) if oi_by_exp else min(o.expiry for o in opts)
    near = [o for o in opts if o.expiry == near_exp]
    return {
        "spot": spot,
        "gex": gex_at(opts, spot, mult, now),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "gamma_strike": gamma_strike,
        "flip": flip,
        "max_pain": max_pain(near),
        "max_pain_expiry": near_exp,
        "call_oi": sum(o.oi for o in opts if o.is_call),
        "put_oi": sum(o.oi for o in opts if not o.is_call),
    }


# ------------------------------------------------------------------- Deribit ---
def _get(url, params=None, retries=3):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=25)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == retries - 1:
                raise
            time.sleep(3)


def parse_deribit_name(name):
    # BTC-27NOV26-73000-P  (vence a las 08:00 UTC)
    _, d, k, cp = name.split("-")
    exp = datetime.strptime(d, "%d%b%y").replace(hour=8, tzinfo=timezone.utc)
    return float(k), exp, cp == "C"


def deribit_options(currency="BTC"):
    res = _get(f"{DERIBIT}/get_book_summary_by_currency", {"currency": currency, "kind": "option"})["result"]
    opts = []
    for x in res:
        try:
            k, exp, is_call = parse_deribit_name(x["instrument_name"])
        except ValueError:
            continue
        opts.append(Opt(k, exp, is_call, float(x.get("open_interest") or 0), float(x.get("mark_iv") or 0) / 100))
    spot = _get(f"{DERIBIT}/get_index_price", {"index_name": f"{currency.lower()}_usd"})["result"]["index_price"]
    return opts, float(spot)


def deribit_trades(start_ms, end_ms, currency="BTC"):
    out, cursor = [], start_ms
    for _ in range(60):  # tope de seguridad
        res = _get(f"{DERIBIT}/get_last_trades_by_currency_and_time",
                   {"currency": currency, "kind": "option", "start_timestamp": cursor,
                    "end_timestamp": end_ms, "count": 1000, "sorting": "asc"})["result"]
        trades = res.get("trades", [])
        out.extend(trades)
        if not res.get("has_more") or not trades:
            break
        cursor = trades[-1]["timestamp"] + 1
    seen, uniq = set(), []
    for t in out:
        if t["trade_id"] not in seen:
            seen.add(t["trade_id"])
            uniq.append(t)
    return uniq


def trade_premium_usd(t):
    return t["price"] * t["amount"] * t["index_price"]


def flow_summary(trades):
    """Prima en USD por tipo y lado del agresor (taker)."""
    s = {"call_buy": 0.0, "call_sell": 0.0, "put_buy": 0.0, "put_sell": 0.0, "n": len(trades), "blocks": 0}
    blocks = set()
    for t in trades:
        typ = "call" if t["instrument_name"].endswith("-C") else "put"
        s[f"{typ}_{t['direction']}"] += trade_premium_usd(t)
        if t.get("block_trade_id"):
            blocks.add(t["block_trade_id"])
    s["blocks"] = len(blocks)
    s["call_net"] = s["call_buy"] - s["call_sell"]
    s["put_net"] = s["put_buy"] - s["put_sell"]
    # Sesgo: calls compradas netas suman alcista, puts compradas netas suman bajista
    s["bias"] = s["call_net"] - s["put_net"]
    return s


def group_orders(trades):
    """Agrupa patas de un block trade, o varias ejecuciones de la misma orden."""
    groups = defaultdict(list)
    for t in trades:
        key = t.get("block_trade_id") or f"{t['instrument_name']}|{t['direction']}|{t['timestamp']}"
        groups[key].append(t)
    out = []
    for key, legs in groups.items():
        out.append({
            "id": key,
            "block": bool(legs[0].get("block_trade_id")),
            "legs": legs,
            "premium": sum(trade_premium_usd(t) for t in legs),
            "notional": sum(t["amount"] * t["index_price"] for t in legs),
            "timestamp": max(t["timestamp"] for t in legs),
        })
    return sorted(out, key=lambda g: g["premium"], reverse=True)


def unusual_orders(trades, min_premium_usd, min_notional_usd):
    return [g for g in group_orders(trades)
            if g["premium"] >= min_premium_usd or g["notional"] >= min_notional_usd]


# --------------------------------------------------------------------- CBOE ---
def parse_occ(sym):
    # GLD261023C00205000 -> raiz, AAMMDD, C/P, strike*1000 (vence ~20:00 UTC)
    root = sym[:-15]
    d, cp, k = sym[-15:-9], sym[-9], sym[-8:]
    exp = datetime.strptime(d, "%y%m%d").replace(hour=20, tzinfo=timezone.utc)
    return root, float(k) / 1000, exp, cp == "C"


def cboe_options(symbol="GLD"):
    j = None
    for url in (CBOE.format(sym=symbol), CBOE.format(sym=symbol).replace("cdn.cboe.com", "cdn-api.cboe.com")):
        try:
            j = _get(url)
            break
        except requests.RequestException:
            continue
    if j is None:
        raise RuntimeError("CBOE no respondio")
    d = j["data"]
    opts, raw = [], []
    for x in d["options"]:
        try:
            _, k, exp, is_call = parse_occ(x["option"])
        except ValueError:
            continue
        opts.append(Opt(k, exp, is_call, float(x.get("open_interest") or 0), float(x.get("iv") or 0)))
        raw.append((x, k, exp, is_call))
    return opts, float(d["current_price"]), raw, j.get("timestamp")


def cboe_unusual_volume(raw, now=None, min_volume=1000, top=5):
    """Contratos con volumen del dia mayor a su OI (actividad nueva). Sin direccion."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for x, k, exp, is_call in raw:
        vol, oi = float(x.get("volume") or 0), float(x.get("open_interest") or 0)
        if exp <= now or vol < min_volume or vol <= oi:
            continue
        px = float(x.get("last_trade_price") or 0)
        rows.append({"strike": k, "expiry": exp, "is_call": is_call, "volume": vol, "oi": oi,
                     "premium": vol * px * 100})
    return sorted(rows, key=lambda r: r["premium"], reverse=True)[:top]


def trade_side(x):
    """Estima el lado del ultimo trade comparando su precio con bid/ask (CBOE no informa el agresor).
    Devuelve "compra", "venta" o None si no se puede saber."""
    try:
        last, bid, ask = float(x.get("last_trade_price") or 0), float(x.get("bid") or 0), float(x.get("ask") or 0)
    except (TypeError, ValueError):
        return None
    if last <= 0 or ask <= 0 or ask < bid:
        return None
    spread = ask - bid
    if last >= ask - 0.25 * spread:
        return "compra"
    if last <= bid + 0.25 * spread:
        return "venta"
    return None


def cboe_unusual_buys(raw, now=None, min_volume=1000, min_premium=250_000):
    """Contratos con volumen > OI, prima grande y ultimo trade cerca del ask (compra probable)."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for x, k, exp, is_call in raw:
        vol, oi = float(x.get("volume") or 0), float(x.get("open_interest") or 0)
        if exp <= now or vol < min_volume or vol <= oi:
            continue
        px = float(x.get("last_trade_price") or 0)
        prem = vol * px * 100
        if prem < min_premium or trade_side(x) != "compra":
            continue
        rows.append({"option": x.get("option"), "strike": k, "expiry": exp, "is_call": is_call, "volume": vol,
                     "oi": oi, "premium": prem, "last": px, "bid": float(x.get("bid") or 0),
                     "ask": float(x.get("ask") or 0), "iv": float(x.get("iv") or 0)})
    return sorted(rows, key=lambda r: r["premium"], reverse=True)


def gold_ratio(gld_price, fallback):
    """XAUUSD aproximado / GLD usando PAXG (Binance). Si falla, usa el ratio fijo."""
    try:
        paxg = float(_get(BINANCE, {"symbol": "PAXGUSDT"})["price"])
        ratio = paxg / gld_price
        if 8 < ratio < 13:  # sanidad: GLD ~ 1/10.8 de onza
            return ratio, "PAXG"
    except Exception:
        pass
    return fallback, "fijo"
