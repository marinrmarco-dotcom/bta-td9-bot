"""
BTA TD9 Confluencias - motor en Python.

Replica vela a vela la logica del indicador de Pine Script "BTA TD9 Conf":
conteo TD Sequential (setup 1-9), 9 perfeccionado, RSI extremo, divergencia RSI,
toque de Bollinger, TD de temporalidad mayor >= 7, vela de confirmacion,
invalidacion y puntaje de confluencias.

Solo trabaja con velas CERRADAS. La temporalidad mayor se toma sin mirar al
futuro: en cada vela se usa el conteo de la ultima vela mayor ya cerrada
(igual que request.security con lookahead off en el historico).
"""

import math
from bisect import bisect_right
from dataclasses import dataclass, field

NAN = float("nan")


@dataclass
class Params:
    lookback: int = 4
    window: int = 4
    min_score: int = 4
    rsi_len: int = 14
    rsi_ob: float = 70
    rsi_os: float = 30
    div_len: int = 30
    bb_len: int = 20
    bb_mult: float = 2.0


@dataclass
class Signal:
    side: str            # "VENTA" o "COMPRA"
    index: int           # indice de la vela donde se confirma
    open_time: int       # ms
    close_time: int      # ms
    close: float
    score: int
    confirm_level: float
    invalid_level: float
    risk_pct: float
    nine_open_time: int
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------- helpers ---
def _isnan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def td_counts(close, lookback=4):
    up = [0] * len(close)
    dn = [0] * len(close)
    u = d = 0
    for i, c in enumerate(close):
        if i >= lookback and c > close[i - lookback]:
            u = 1 if u >= 9 else u + 1
        else:
            u = 0
        if i >= lookback and c < close[i - lookback]:
            d = 1 if d >= 9 else d + 1
        else:
            d = 0
        up[i], dn[i] = u, d
    return up, dn


def rolling_max(x, n):
    out = [NAN] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        if any(_isnan(v) for v in w):
            continue
        out[i] = max(w)
    return out


def rolling_min(x, n):
    out = [NAN] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        if any(_isnan(v) for v in w):
            continue
        out[i] = min(w)
    return out


def shift(x, k):
    return [NAN] * k + x[:-k] if k else list(x)


def rma(x, n):
    """ta.rma de Pine: arranca con SMA de los primeros n valores validos."""
    out = [NAN] * len(x)
    acc, cnt, prev = 0.0, 0, NAN
    for i, v in enumerate(x):
        if _isnan(v):
            continue
        if _isnan(prev):
            acc += v
            cnt += 1
            if cnt == n:
                prev = acc / n
                out[i] = prev
        else:
            prev = (prev * (n - 1) + v) / n
            out[i] = prev
    return out


def rsi(close, n):
    gains, losses = [NAN], [NAN]
    for i in range(1, len(close)):
        ch = close[i] - close[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    up, dn = rma(gains, n), rma(losses, n)
    out = []
    for u, d in zip(up, dn):
        if _isnan(u) or _isnan(d):
            out.append(NAN)
        elif d == 0:
            out.append(100.0)
        elif u == 0:
            out.append(0.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + u / d))
    return out


def bollinger(close, n, mult):
    upper, lower = [NAN] * len(close), [NAN] * len(close)
    for i in range(n - 1, len(close)):
        w = close[i - n + 1:i + 1]
        m = sum(w) / n
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)  # ta.stdev (poblacional)
        upper[i], lower[i] = m + mult * sd, m - mult * sd
    return upper, lower


def htf_counts_aligned(chart_close_times, htf_close_times, htf_up, htf_dn):
    """Para cada vela del grafico, el conteo de la ultima vela mayor cerrada."""
    up, dn = [0] * len(chart_close_times), [0] * len(chart_close_times)
    for i, ct in enumerate(chart_close_times):
        j = bisect_right(htf_close_times, ct) - 1
        if j >= 0:
            up[i], dn[i] = htf_up[j], htf_dn[j]
        else:
            up[i] = dn[i] = -1  # sin dato
    return up, dn


# ------------------------------------------------------------------ motor ---
def analyze(candles, htf_candles, p: Params = Params()):
    """
    candles / htf_candles: listas de dicts con open_time, close_time, high, low, close
    (solo velas cerradas, en orden cronologico).
    Devuelve (signals, state) donde state describe el setup activo en la ultima vela.
    """
    n = len(candles)
    H = [c["high"] for c in candles]
    L = [c["low"] for c in candles]
    C = [c["close"] for c in candles]
    OT = [c["open_time"] for c in candles]
    CT = [c["close_time"] for c in candles]

    up, dn = td_counts(C, p.lookback)
    hU, hD = td_counts([c["close"] for c in htf_candles], p.lookback)
    htfUp, htfDn = htf_counts_aligned(CT, [c["close_time"] for c in htf_candles], hU, hD)

    setupHigh, setupLow = rolling_max(H, 9), rolling_min(L, 9)
    r = rsi(C, p.rsi_len)
    rsiMax3, rsiMin3 = rolling_max(r, 3), rolling_min(r, 3)
    bbUp, bbLo = bollinger(C, p.bb_len, p.bb_mult)
    hitUp = [(not _isnan(bbUp[i])) and H[i] >= bbUp[i] for i in range(n)]
    hitLo = [(not _isnan(bbLo[i])) and L[i] <= bbLo[i] for i in range(n)]
    prevHigh = shift(rolling_max(H, p.div_len), 9)
    prevRsiMax = shift(rolling_max(r, p.div_len), 9)
    prevLow = shift(rolling_min(L, p.div_len), 9)
    prevRsiMin = shift(rolling_min(r, p.div_len), 9)

    def any3(arr, i):
        return any(arr[k] for k in range(max(0, i - 2), i + 1))

    def gt(a, b):
        return (not _isnan(a)) and (not _isnan(b)) and a > b

    def lt(a, b):
        return (not _isnan(a)) and (not _isnan(b)) and a < b

    S = dict(bar=None, lvl=NAN, inv=NAN, perf=False, rsi=False, div=False, bb=False,
             htf=False, candle=False, invalid=False, fired=False, nine_ot=None)
    B = dict(S)
    signals = []
    last_state = {}

    for i in range(n):
        # -------- VENTA
        s_active_prev = S["bar"] is not None and i - S["bar"] <= p.window
        if up[i] == 9:
            perf = (H[i] >= max(H[i - 2], H[i - 3]) or H[i - 1] >= max(H[i - 2], H[i - 3])) if i >= 3 else False
            S.update(bar=i, lvl=L[i], inv=setupHigh[i], perf=perf,
                     rsi=(not _isnan(rsiMax3[i])) and rsiMax3[i] >= p.rsi_ob,
                     div=gt(H[i], prevHigh[i]) and lt(rsiMax3[i], prevRsiMax[i]),
                     bb=any3(hitUp, i), htf=htfUp[i] >= 7,
                     candle=False, invalid=False, fired=False, nine_ot=OT[i])
        elif s_active_prev and not S["invalid"] and not S["candle"]:
            if C[i] > S["inv"]:
                S["invalid"] = True
            elif C[i] < S["lvl"]:
                S["candle"] = True
        s_active = S["bar"] is not None and i - S["bar"] <= p.window
        s_score = sum([S["perf"], S["rsi"], S["div"], S["bb"], S["htf"], S["candle"]])
        if s_active and S["candle"] and not S["invalid"] and s_score >= p.min_score and not S["fired"]:
            S["fired"] = True
            risk = (S["inv"] - S["lvl"]) / S["lvl"] * 100
            signals.append(Signal("VENTA", i, OT[i], CT[i], C[i], s_score, S["lvl"], S["inv"], risk,
                                  S["nine_ot"], {k: S[k] for k in ("perf", "rsi", "div", "bb", "htf", "candle")}))

        # -------- COMPRA
        b_active_prev = B["bar"] is not None and i - B["bar"] <= p.window
        if dn[i] == 9:
            perf = (L[i] <= min(L[i - 2], L[i - 3]) or L[i - 1] <= min(L[i - 2], L[i - 3])) if i >= 3 else False
            B.update(bar=i, lvl=H[i], inv=setupLow[i], perf=perf,
                     rsi=(not _isnan(rsiMin3[i])) and rsiMin3[i] <= p.rsi_os,
                     div=lt(L[i], prevLow[i]) and gt(rsiMin3[i], prevRsiMin[i]),
                     bb=any3(hitLo, i), htf=htfDn[i] >= 7,
                     candle=False, invalid=False, fired=False, nine_ot=OT[i])
        elif b_active_prev and not B["invalid"] and not B["candle"]:
            if C[i] < B["inv"]:
                B["invalid"] = True
            elif C[i] > B["lvl"]:
                B["candle"] = True
        b_active = B["bar"] is not None and i - B["bar"] <= p.window
        b_score = sum([B["perf"], B["rsi"], B["div"], B["bb"], B["htf"], B["candle"]])
        if b_active and B["candle"] and not B["invalid"] and b_score >= p.min_score and not B["fired"]:
            B["fired"] = True
            risk = (B["lvl"] - B["inv"]) / B["lvl"] * 100
            signals.append(Signal("COMPRA", i, OT[i], CT[i], C[i], b_score, B["lvl"], B["inv"], risk,
                                  B["nine_ot"], {k: B[k] for k in ("perf", "rsi", "div", "bb", "htf", "candle")}))

        if i == n - 1:
            def verdict(X, active, score):
                if not active:
                    return "Sin 9"
                if X["invalid"]:
                    return "INVALIDADO"
                if X["candle"] and score >= p.min_score:
                    return "CONFIRMADA"
                return "Esperando vela" if not X["candle"] else "Debil"
            last_state = {
                "up": up[i], "dn": dn[i],
                "venta": dict(active=s_active, score=s_score, confirm=S["lvl"], invalid=S["inv"],
                              verdict=verdict(S, s_active, s_score), nine_open_time=S["nine_ot"]),
                "compra": dict(active=b_active, score=b_score, confirm=B["lvl"], invalid=B["inv"],
                               verdict=verdict(B, b_active, b_score), nine_open_time=B["nine_ot"]),
            }

    return signals, last_state
