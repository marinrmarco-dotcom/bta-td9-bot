"""
Mensajes de Telegram para opciones: reporte diario, reporte pre-apertura NY y
alertas de operaciones grandes en BTC (Deribit).
"""

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import options_data as od

CL = ZoneInfo("America/Santiago")
NY = ZoneInfo("America/New_York")
DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]


def n0(x):
    return "—" if x is None else f"{x:,.0f}".replace(",", ".")


def n2(x):
    return "—" if x is None else f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def money(x):
    sign = "-" if x < 0 else ""
    x = abs(x)
    if x >= 1e9:
        return f"{sign}${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"{sign}${x / 1e6:.1f}M"
    if x >= 1e3:
        return f"{sign}${x / 1e3:.0f}K"
    return f"{sign}${x:.0f}"


def regime(gex):
    return ("gamma positivo → tiende a rangos / menor volatilidad" if gex >= 0
            else "gamma negativo → movimientos más amplios")


def _exp(d):
    return d.astimezone(CL).strftime("%d-%m")


# ------------------------------------------------------------------ bloques ---
def btc_block(cfg, flow_hours):
    opts, spot = od.deribit_options("BTC")
    lv = od.levels(opts, spot, 1, max_days=cfg.get("max_days", 90))
    now_ms = int(time.time() * 1000)
    trades = od.deribit_trades(now_ms - int(flow_hours * 3600_000), now_ms)
    fs = od.flow_summary(trades)
    top = od.group_orders(trades)[:3]
    sesgo = "alcista 🟢" if fs["bias"] > 0 else "bajista 🔴" if fs["bias"] < 0 else "neutral"
    lines = [
        f"<b>₿ BTC</b> (Deribit) · spot {n0(spot)}",
        f"GEX neto: <b>{money(lv['gex'])}</b> por 1% ({regime(lv['gex'])})",
        f"Call Wall: <b>{n0(lv['call_wall'])}</b> · Put Wall: <b>{n0(lv['put_wall'])}</b>",
        f"Mayor gamma (pin): {n0(lv['gamma_strike'])} · Gamma flip: {n0(lv['flip'])}",
        f"Max Pain ({_exp(lv['max_pain_expiry'])}): {n0(lv['max_pain'])}",
        f"Put/Call OI: {lv['put_oi'] / lv['call_oi']:.2f}" if lv["call_oi"] else "",
        "",
        f"<b>Flujo {flow_hours:g}h</b> ({fs['n']} operaciones, {fs['blocks']} blocks)",
        f"Calls: compradas {money(fs['call_buy'])} · vendidas {money(fs['call_sell'])} → neto {money(fs['call_net'])}",
        f"Puts: compradas {money(fs['put_buy'])} · vendidas {money(fs['put_sell'])} → neto {money(fs['put_net'])}",
        f"Sesgo del flujo: <b>{sesgo}</b>",
    ]
    if top:
        lines.append("Mayores operaciones:")
        for g in top:
            lines.append("• " + order_line(g))
    return "\n".join(l for l in lines if l is not None)


def gold_block(cfg):
    opts, gld, raw, ts = od.cboe_options("GLD")
    ratio, src = od.gold_ratio(gld, cfg.get("gld_ratio_fallback", 10.85))
    lv = od.levels(opts, gld, 100, max_days=cfg.get("max_days", 90))
    x = lambda v: None if v is None else v * ratio  # noqa: E731
    lines = [
        f"<b>🥇 XAUUSD</b> (vía GLD, CBOE {ts or ''} UTC) · ratio {ratio:.2f} ({src})",
        f"GLD {n2(gld)} ≈ XAU {n0(gld * ratio)}",
        f"GEX neto: <b>{money(lv['gex'])}</b> por 1% ({regime(lv['gex'])})",
        f"Call Wall: GLD {n2(lv['call_wall'])} ≈ <b>XAU {n0(x(lv['call_wall']))}</b>",
        f"Put Wall: GLD {n2(lv['put_wall'])} ≈ <b>XAU {n0(x(lv['put_wall']))}</b>",
        f"Mayor gamma (pin): GLD {n2(lv['gamma_strike'])} ≈ XAU {n0(x(lv['gamma_strike']))}",
        f"Gamma flip: GLD {n2(lv['flip'])} ≈ XAU {n0(x(lv['flip']))}",
        f"Max Pain ({_exp(lv['max_pain_expiry'])}): GLD {n2(lv['max_pain'])} ≈ XAU {n0(x(lv['max_pain']))}",
    ]
    unusual = od.cboe_unusual_volume(raw, min_volume=cfg.get("gld_unusual_min_volume", 1000))
    if unusual:
        lines.append("Volumen inusual GLD (volumen > OI, sin dirección):")
        for r in unusual:
            lines.append(f"• {'Call' if r['is_call'] else 'Put'} {n2(r['strike'])} ({_exp(r['expiry'])}) · "
                         f"vol {n0(r['volume'])} vs OI {n0(r['oi'])} · ~{money(r['premium'])}")
    return "\n".join(lines)


def order_line(g):
    legs = g["legs"]
    if len({t["instrument_name"] for t in legs}) == 1:
        t = legs[0]
        _, exp, k, cp = t["instrument_name"].split("-")
        side = "COMPRA" if t["direction"] == "buy" else "VENTA"
        qty = sum(x["amount"] for x in legs)
        return (f"{side} {'Call' if cp == 'C' else 'Put'} {n0(float(k))} {exp} · {qty:g} BTC · "
                f"prima {money(g['premium'])} · IV {t['iv']:.0f}%" + (" · BLOCK" if g["block"] else ""))
    parts = []
    for t in legs:
        _, exp, k, cp = t["instrument_name"].split("-")
        parts.append(f"{'+' if t['direction'] == 'buy' else '−'}{t['amount']:g} {cp}{n0(float(k))} {exp}")
    return f"Estrategia ({len(legs)} patas): " + ", ".join(parts) + f" · prima {money(g['premium'])}" + \
           (" · BLOCK" if g["block"] else "")


# ----------------------------------------------------------------- mensajes ---
def daily_report(cfg):
    now = datetime.now(CL)
    head = f"📊 <b>Reporte de opciones · {DIAS[now.weekday()]} {now:%d-%m %H:%M}</b> (hora Chile)"
    parts = [head, ""]
    for fn in (lambda: btc_block(cfg, 24), lambda: gold_block(cfg)):
        try:
            parts.append(fn())
        except Exception as e:
            parts.append(f"⚠️ No se pudo calcular: {e}")
        parts.append("")
    parts.append("<i>Herramienta de análisis. No es asesoría financiera.</i>")
    return "\n".join(parts)


def preopen_report(cfg):
    now = datetime.now(CL)
    head = f"🔔 <b>Pre-apertura Wall Street · {DIAS[now.weekday()]} {now:%d-%m %H:%M}</b> (hora Chile)"
    parts = [head, "Abre en ~15 minutos.", ""]
    for fn in (lambda: gold_block(cfg), lambda: btc_block(cfg, 12)):
        try:
            parts.append(fn())
        except Exception as e:
            parts.append(f"⚠️ No se pudo calcular: {e}")
        parts.append("")
    parts.append("<i>Herramienta de análisis. No es asesoría financiera.</i>")
    return "\n".join(parts)


def unusual_alerts(cfg, since_ms, now_ms, already):
    """Devuelve [(id, texto)] de operaciones grandes de BTC nuevas."""
    trades = od.deribit_trades(since_ms, now_ms)
    out = []
    for g in od.unusual_orders(trades, cfg.get("btc_min_premium_usd", 250_000),
                               cfg.get("btc_min_notional_usd", 10_000_000)):
        if g["id"] in already:
            continue
        ts = datetime.fromtimestamp(g["timestamp"] / 1000, timezone.utc).astimezone(CL)
        spot = g["legs"][0]["index_price"]
        text = (f"🐋 <b>Operación grande BTC</b> · {ts:%d-%m %H:%M} (Chile)\n"
                f"{order_line(g)}\n"
                f"Nocional: {money(g['notional'])} · BTC {n0(spot)}\n"
                f"<i>COMPRA/VENTA = lado agresor (taker) en Deribit.</i>")
        out.append((g["id"], text))
    return out


# ---------------------------------------------------------------- horarios ---
def due(kind, cfg, state, now_utc=None):
    """True si toca enviar el reporte ahora y aun no se envio hoy."""
    now_utc = now_utc or datetime.now(timezone.utc)
    sent = state.setdefault("reports", {})
    if kind == "daily":
        local = now_utc.astimezone(CL)
        hh, mm = map(int, cfg.get("daily_report_time_cl", "08:30").split(":"))
        start = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        ok = start <= local <= start.replace(hour=min(hh + 2, 23))
        key = f"daily|{local:%Y-%m-%d}"
    else:  # preopen: 15 min antes de las 9:30 de Nueva York, dias habiles
        local = now_utc.astimezone(NY)
        start = local.replace(hour=9, minute=10, second=0, microsecond=0)
        ok = local.weekday() < 5 and start <= local < local.replace(hour=9, minute=45, second=0, microsecond=0)
        key = f"preopen|{local:%Y-%m-%d}"
    return (ok and key not in sent), key
