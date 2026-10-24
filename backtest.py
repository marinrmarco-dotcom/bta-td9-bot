"""
Backtest de objetivos (TP) por temporalidad para las senales TD9 confirmadas.

Para cada senal historica mide, desde el cierre de la vela de confirmacion:
  - riesgo R = distancia entre entrada y stop (nivel de invalidacion)
  - MFE: maximo avance a favor antes de tocar el stop (dentro de un horizonte)
  - si alcanzo 1R, 1.5R, 2R y 3R antes del stop

Uso:  python backtest.py            (usa config.json; imprime tablas en pantalla)
"""

import statistics as stats
import time

import requests

import bot
from td9 import Params, analyze

HORIZON = {"15m": 32, "1h": 24, "4h": 30, "1d": 20, "1w": 12}   # velas hacia adelante
HISTORY = {"15m": 8000, "1h": 8000, "4h": 5000, "1d": 2500, "1w": 600}
TARGETS = (1.0, 1.5, 2.0, 3.0)


def fetch_binance_history(symbol, interval, total):
    out, end = [], None
    while len(out) < total:
        params = {"symbol": symbol, "interval": interval, "limit": 1000}
        if end:
            params["endTime"] = end
        r = requests.get(bot.BINANCE_URL, params=params, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        chunk = [dict(open_time=k[0], close_time=k[6], high=float(k[2]), low=float(k[3]), close=float(k[4]))
                 for k in rows]
        out = chunk + out
        end = rows[0][0] - 1
        if len(rows) < 1000:
            break
        time.sleep(0.2)
    now_ms = int(time.time() * 1000)
    seen, uniq = set(), []
    for c in out:
        if c["open_time"] not in seen and c["close_time"] < now_ms:
            seen.add(c["open_time"])
            uniq.append(c)
    return sorted(uniq, key=lambda c: c["open_time"])[-total:]


def fetch_history(cfg, symbol, interval, total):
    src = cfg.get("sources", {}).get(symbol, {})
    if src.get("provider") == "twelvedata":
        return bot.fetch_twelve(src.get("symbol", symbol), interval, min(total, 5000))
    return fetch_binance_history(symbol, interval, total)


def evaluate(candles, sig, horizon):
    entry, stop = sig.close, sig.invalid_level
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    short = sig.side == "VENTA"
    mfe, hit_stop, bars = 0.0, False, 0
    for c in candles[sig.index + 1: sig.index + 1 + horizon]:
        bars += 1
        fav = (entry - c["low"]) if short else (c["high"] - entry)
        adv = (c["high"] - entry) if short else (entry - c["low"])
        # conservador: si en la misma vela toca stop y objetivo, cuenta el stop primero
        if adv >= risk:
            hit_stop = True
            break
        mfe = max(mfe, fav)
    return {"risk_pct": risk / entry * 100, "mfe_r": mfe / risk, "mfe_pct": mfe / entry * 100,
            "stopped": hit_stop, "bars": bars}


def pct(values, q):
    if not values:
        return float("nan")
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def main():
    cfg = bot.load_json(bot.CONFIG_PATH, None)
    p = Params(**cfg.get("params", {}))
    print("BACKTEST TP · senales TD9 confirmadas (min_score=%d)" % p.min_score)
    print("Horizonte por temporalidad (velas):", HORIZON)
    print("Regla conservadora: si una vela toca stop y objetivo, cuenta como stop.\n")
    summary = []
    for symbol in cfg["symbols"]:
        cache = {}
        for tf, htf in cfg["timeframes"].items():
            try:
                if tf not in cache:
                    cache[tf] = fetch_history(cfg, symbol, tf, HISTORY.get(tf, 3000))
                if htf not in cache:
                    cache[htf] = fetch_history(cfg, symbol, htf, HISTORY.get(htf, 600))
                candles = cache[tf]
                signals, _ = analyze(candles, cache[htf], p)
            except Exception as e:
                print(f"{symbol} {tf}: error {e}")
                continue
            res = [r for r in (evaluate(candles, s, HORIZON.get(tf, 20)) for s in signals) if r]
            days = (candles[-1]["close_time"] - candles[0]["open_time"]) / 86_400_000 if candles else 0
            if not res:
                print(f"{symbol} {tf}: 0 senales en {days:.0f} dias")
                continue
            mfe_r = [r["mfe_r"] for r in res]
            mfe_pct = [r["mfe_pct"] for r in res]
            risk = [r["risk_pct"] for r in res]
            hits = {t: sum(1 for r in res if r["mfe_r"] >= t) / len(res) * 100 for t in TARGETS}
            line = (f"{symbol:8} {tf:>3} | n={len(res):3} en {days:4.0f}d | riesgo med {stats.median(risk):.2f}% | "
                    f"MFE med {stats.median(mfe_r):.2f}R ({stats.median(mfe_pct):.2f}%) p25 {pct(mfe_r, .25):.2f}R "
                    f"p75 {pct(mfe_r, .75):.2f}R | llega 1R {hits[1.0]:.0f}% 1.5R {hits[1.5]:.0f}% "
                    f"2R {hits[2.0]:.0f}% 3R {hits[3.0]:.0f}%")
            print(line)
            summary.append((symbol, tf, len(res), stats.median(risk), stats.median(mfe_r),
                            stats.median(mfe_pct), pct(mfe_r, .25), pct(mfe_r, .75), hits))
    # Agregado por temporalidad (todos los simbolos)
    print("\nAGREGADO POR TEMPORALIDAD")
    for tf in cfg["timeframes"]:
        rows = [s for s in summary if s[1] == tf]
        n = sum(r[2] for r in rows)
        if not n:
            continue
        w = lambda i: sum(r[i] * r[2] for r in rows) / n  # noqa: E731
        hit = {t: sum(r[8][t] * r[2] for r in rows) / n for t in TARGETS}
        print(f"{tf:>3} | n={n:3} | riesgo ~{w(3):.2f}% | MFE med ~{w(4):.2f}R (~{w(5):.2f}%) | "
              f"1R {hit[1.0]:.0f}% 1.5R {hit[1.5]:.0f}% 2R {hit[2.0]:.0f}% 3R {hit[3.0]:.0f}%")


if __name__ == "__main__":
    main()
