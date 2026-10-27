"""
BTA TD9 Bot - avisos por Telegram de las senales del indicador "BTA TD9 Conf".

Uso:
  python bot.py              -> revisa todo y envia las senales nuevas
  python bot.py --dry-run    -> revisa y muestra en pantalla, sin enviar ni guardar
  python bot.py --status     -> muestra el estado del cuadro en cada temporalidad
  python bot.py --test       -> envia un mensaje de prueba a Telegram
  python bot.py --report daily|preopen  -> envia ese reporte de opciones ahora

Cada ejecucion normal ademas: envia el reporte diario (08:30 Chile) y el de
pre-apertura (15 min antes de Wall Street) cuando corresponde, y avisa las
operaciones grandes de opciones BTC en Deribit.

Variables de entorno (en GitHub van como Secrets):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from zoneinfo import ZoneInfo

from td9 import Params, analyze

ROOT = Path(__file__).parent
NY_TZ = ZoneInfo("America/New_York")
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"

# data-api.binance.vision: datos publicos de Binance, funciona desde servidores en EE. UU.
BINANCE_URL = "https://data-api.binance.vision/api/v3/klines"
TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000,
    "1w": 604_800_000, "1M": 2_678_400_000,
}
TF_LABEL = {"15m": "15M · Intradía", "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "8h": "8H", "12h": "12H",
            "1d": "1D", "3d": "3D", "1w": "1S", "1M": "1M"}


# ----------------------------------------------------------------- datos ---
# Twelve Data: velas reales de XAU/USD (plan gratis: 800 consultas/dia, 8/minuto).
TWELVE_URL = "https://api.twelvedata.com/time_series"
TWELVE_INTERVAL = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h",
                   "2h": "2h", "4h": "4h", "1d": "1day", "1w": "1week", "1M": "1month"}


class SkipSymbol(Exception):
    """Simbolo que no se puede revisar ahora (por ejemplo, falta la API key)."""


def _next_month_ms(dt):
    y, m = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


def fetch_twelve(td_symbol, interval, limit):
    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        raise SkipSymbol("falta el Secret TWELVEDATA_API_KEY")
    for attempt in range(3):
        try:
            r = requests.get(TWELVE_URL, params={
                "symbol": td_symbol, "interval": TWELVE_INTERVAL[interval], "outputsize": min(limit, 5000),
                "timezone": "UTC", "order": "asc", "apikey": key}, timeout=20)
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "ok":
                msg = data.get("message", "respuesta invalida")
                if data.get("code") == 429 and attempt < 2:  # limite por minuto: esperar y reintentar
                    time.sleep(62)
                    continue
                raise RuntimeError(f"Twelve Data: {msg}")
            now_ms = int(time.time() * 1000)
            out = []
            for v in data["values"]:
                dt = datetime.fromisoformat(v["datetime"]).replace(tzinfo=timezone.utc)
                ot = int(dt.timestamp() * 1000)
                ct = (_next_month_ms(dt) if interval == "1M" else ot + TF_MS[interval]) - 1
                if ct < now_ms:  # solo velas cerradas
                    out.append(dict(open_time=ot, close_time=ct, high=float(v["high"]),
                                    low=float(v["low"]), close=float(v["close"])))
            return out
        except requests.RequestException as e:
            if attempt == 2:
                raise
            print(f"  reintento {td_symbol} {interval}: {e}")
            time.sleep(3)


def fetch_symbol(cfg, symbol, interval, limit):
    src = cfg.get("sources", {}).get(symbol, {})
    if src.get("provider") == "twelvedata":
        return fetch_twelve(src.get("symbol", symbol), interval, limit)
    return fetch_closed(symbol, interval, limit)


def fetch_closed(symbol, interval, limit):
    for attempt in range(3):
        try:
            r = requests.get(BINANCE_URL, params={"symbol": symbol, "interval": interval, "limit": limit},
                             timeout=20)
            r.raise_for_status()
            now_ms = int(time.time() * 1000)
            return [dict(open_time=k[0], close_time=k[6], high=float(k[2]), low=float(k[3]),
                         close=float(k[4])) for k in r.json() if k[6] < now_ms]
        except requests.RequestException as e:
            if attempt == 2:
                raise
            print(f"  reintento {symbol} {interval}: {e}")
            time.sleep(3)


# -------------------------------------------------------------- telegram ---
def send_telegram(text, chat_id=None):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=20)
    if not r.ok:
        raise SystemExit(f"Telegram respondio {r.status_code}: {r.text[:300]}")


def signals_chat_id():
    """Canal de senales TD9 (Secret TELEGRAM_CHAT_ID_SENALES); si no existe, el chat normal."""
    return os.environ.get("TELEGRAM_CHAT_ID_SENALES") or None


DEFAULT_WELCOME = ("✅ <b>BTA TD9 Bot conectado.</b>\n"
                   "Te avisare aqui cuando haya una senal confirmada.")


def answer_starts(cfg, state, dry_run=False):
    """Responde la bienvenida a quien escribe /start al bot en privado. No guarda datos de nadie:
    solo recuerda el numero de la ultima actualizacion leida (tg_offset)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token or not cfg.get("welcome_enabled", True):
        return 0
    params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
    if state.get("tg_offset"):
        params["offset"] = state["tg_offset"] + 1
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=20)
    if not r.ok:
        raise RuntimeError(f"getUpdates {r.status_code}: {r.text[:200]}")
    text = cfg.get("welcome_text") or DEFAULT_WELCOME
    n = 0
    for u in r.json().get("result", []):
        state["tg_offset"] = max(state.get("tg_offset", 0), u["update_id"])
        m = u.get("message") or {}
        if m.get("chat", {}).get("type") == "private" and (m.get("text") or "").startswith("/start"):
            if not dry_run:
                send_telegram(text, m["chat"]["id"])
            n += 1
    return n


def options_chat_id():
    """Grupo aparte para opciones (Secret TELEGRAM_CHAT_ID_OPCIONES); si no existe, el chat normal."""
    return os.environ.get("TELEGRAM_CHAT_ID_OPCIONES") or None


def list_chats():
    """Muestra los chats donde el bot recibio mensajes o fue agregado (para obtener el id de un grupo)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN")
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
    if not r.ok:
        raise SystemExit(f"Telegram respondio {r.status_code}: {r.text[:300]}")
    chats = {}
    for u in r.json().get("result", []):
        for k in ("message", "my_chat_member", "channel_post", "edited_message"):
            c = (u.get(k) or {}).get("chat")
            if c:
                chats[c["id"]] = (c.get("type"), c.get("title") or c.get("first_name") or "")
    if not chats:
        print("No hay chats recientes: escribe un mensaje en el grupo (por ejemplo /start) y vuelve a ejecutar.")
    for cid, (typ, name) in chats.items():
        print(f"chat_id {cid} | {typ} | {name}")


# ---------------------------------------------------------------- formato ---
def fmt(x):
    s = f"{x:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def local_time(ms, tz_hours):
    return (datetime.fromtimestamp(ms / 1000, timezone.utc) + timedelta(hours=tz_hours)).strftime("%d-%m %H:%M")


CHECK_NAMES = {"perf": "9 perfeccionado", "rsi": "RSI extremo", "div": "Divergencia RSI",
               "bb": "Toque Bollinger", "htf": "TD mayor &gt;= 7", "candle": "Vela de confirmacion"}


REASON_NAMES = {"perf": "9 perfeccionado", "rsi": "RSI extremo", "div": "divergencia RSI",
                "bb": "toque de Bollinger", "htf": "TD semanal &gt;= 7", "candle": "vela de confirmacion"}


def idea_line(side, desde, hasta, stop, motivo, cond=""):
    """Linea de idea de entrada. side: 'LONG' o 'SHORT'."""
    icon = "🟢" if side == "LONG" else "🔴"
    return (f"💡 {icon} <b>{cond}Sería opción entrar en {side} desde {fmt(desde)} hasta {fmt(hasta)}</b> · "
            f"stop {fmt(stop)}\nMotivo: {motivo}")


def position_1h(cfg, tf, sig):
    """Replica la seccion "Posicion 1H" del indicador v2. Devuelve dict con TP o None si no califica."""
    pc = cfg.get("position_1h", {})
    if not pc.get("enabled", True):
        return None
    if pc.get("only_1h", True) and tf != "1h":
        return None
    if pc.get("require_bb", True) and not sig.details.get("bb"):
        return None
    if sig.side == "VENTA" and sig.score < pc.get("min_score_sell", 4):
        return None
    if sig.side == "VENTA" and not sig.close < sig.invalid_level:
        return None
    if sig.side == "COMPRA" and not sig.close > sig.invalid_level:
        return None
    h = datetime.fromtimestamp(sig.open_time / 1000, NY_TZ).hour  # hora NY de la vela (como hour(time))
    sess = pc.get("sessions", {})
    if h >= 18 or h < 2:
        ok = sess.get("asia", True)
    elif h < 7:
        ok = sess.get("london", True)
    elif h < 11:
        ok = sess.get("ny_am", True)
    elif h < 17:
        ok = sess.get("ny_pm", False)
    else:
        ok = False
    if not ok:
        return None
    d = 1 if sig.side == "COMPRA" else -1
    rk = abs(sig.close - sig.invalid_level)
    rs = pc.get("tp_r", [1.0, 2.0, 3.0])
    return {"r": rs, "tp": [sig.close + d * r * rk for r in rs]}


def signal_message(symbol, tf, htf, sig, tz_hours, cfg=None):
    entry, stop = sig.close, sig.invalid_level
    dist = abs(stop - entry)
    target = entry - dist if sig.side == "VENTA" else entry + dist
    risk = dist / entry * 100
    icon = "🔴" if sig.side == "VENTA" else "🟢"
    checks = "\n".join(f"{'✅' if v else '❌'} {CHECK_NAMES[k]}" for k, v in sig.details.items())
    pos = position_1h(cfg or {}, tf, sig)
    lado = "SHORT" if sig.side == "VENTA" else "LONG"
    ok = [REASON_NAMES[k] for k, v in sig.details.items() if v and k != "candle"]
    rompe = "bajo" if sig.side == "VENTA" else "sobre"
    motivo = (f"{sig.side.lower()} TD9 confirmada con {sig.score}/6 confluencias"
              f"{' (' + ', '.join(ok) + ')' if ok else ''}; la vela cerró {rompe} {fmt(sig.confirm_level)}.")
    idea = idea_line(lado, entry, pos["tp"][0] if pos else target, stop, motivo)
    if pos:
        head = f"📍 <b>POSICIÓN {sig.side} 1H</b> (filtros de sesión y Bollinger OK)\n"
        targets = "\n".join(f"TP{i + 1} ({r:g}R): {fmt(t)}" for i, (r, t) in enumerate(zip(pos["r"], pos["tp"])))
    else:
        head = ""
        targets = f"Objetivo 1:1: {fmt(target)}"
    return (
        f"{icon} <b>{sig.side} CONFIRMADA · {symbol} {TF_LABEL.get(tf, tf)}</b>\n"
        f"{head}"
        f"Confluencias: <b>{sig.score}/6</b>\n\n"
        f"Entrada (cierre): <b>{fmt(entry)}</b>\n"
        f"Stop (invalida): {fmt(stop)}  ·  riesgo {risk:.2f}%\n"
        f"{targets}\n\n"
        f"{idea}\n\n"
        f"{checks}\n\n"
        f"Vela 9: {local_time(sig.nine_open_time, tz_hours)} · cierre confirmacion: "
        f"{local_time(sig.close_time + 1, tz_hours)} (hora Chile)\n"
        f"TD mayor: {TF_LABEL.get(htf, htf)} · BTA TD9 Confluencias v2\n"
        f"<i>No es asesoria financiera.</i>"
    )


def nine_message(symbol, tf, side, st, tz_hours):
    word = "VENTA" if side == "venta" else "COMPRA"
    icon = "🔴" if side == "venta" else "🟢"
    sign = "&lt;" if side == "venta" else "&gt;"
    inv_sign = "&gt;" if side == "venta" else "&lt;"
    conf, inv = st["confirm"], st["invalid"]
    hasta = conf - abs(inv - conf) if side == "venta" else conf + abs(inv - conf)  # 1R desde la confirmacion
    lado = "SHORT" if side == "venta" else "LONG"
    idea = idea_line(lado, conf, hasta, inv,
                     f"9 de {word.lower()} formado con {st['score']}/6 confluencias; "
                     f"falta la vela de confirmación.", cond=f"Si cierra {sign} {fmt(conf)}: ")
    return (
        f"⚠️ <b>ALERTA · Se formó 9 de {word}</b> {icon}\n"
        f"<b>{symbol} {TF_LABEL.get(tf, tf)}</b> · ¡Estar atento!\n\n"
        f"Confirma si cierra {sign} {fmt(st['confirm'])}\n"
        f"Invalida si cierra {inv_sign} {fmt(st['invalid'])}\n"
        f"Confluencias por ahora: {st['score']}/6\n\n"
        f"{idea}\n\n"
        f"Vela 9: {local_time(st['nine_open_time'], tz_hours)} (hora Chile)\n"
        f"<i>Aún no es señal: espera la confirmación.</i>"
    )


# ----------------------------------------------------------------- estado ---
def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def prune(state, days=45):
    cutoff = time.time() - days * 86400
    for key in ("sent", "flow_sent"):
        state[key] = {k: v for k, v in state.get(key, {}).items() if v >= cutoff}
    state["reports"] = {k: v for k, v in state.get("reports", {}).items() if v >= cutoff}
    return state


# --------------------------------------------------------------- opciones ---
def run_options(cfg, state, args, outgoing):
    """Reportes programados y alertas de operaciones grandes (Deribit / CBOE)."""
    import reports  # import aqui: si algo falla en opciones, el TD9 sigue funcionando

    ocfg = cfg.get("options", {})
    if not ocfg.get("enabled", True):
        return
    now = time.time()

    # Reportes forzados a mano (--report daily|preopen)
    if args.report:
        fn = reports.daily_report if args.report == "daily" else reports.preopen_report
        outgoing.append((None, fn(ocfg)))
        return

    for kind, fn in (("daily", reports.daily_report), ("preopen", reports.preopen_report)):
        if not ocfg.get(f"{kind}_report", True):
            continue
        is_due, key = reports.due(kind, ocfg, state)
        if is_due:
            outgoing.append((("reports", key), fn(ocfg)))

    if ocfg.get("btc_unusual_alerts", True):
        now_ms = int(now * 1000)
        since = max(state.get("flow_last_ms", 0), now_ms - 60 * 60_000)  # maximo 1 hora atras
        alerts = reports.unusual_alerts(ocfg, since, now_ms, state.get("flow_sent", {}))
        for gid, text in alerts[: ocfg.get("max_alerts_per_run", 5)]:
            outgoing.append((("flow_sent", gid), text))
        if not args.dry_run:
            state["flow_last_ms"] = now_ms

    if ocfg.get("gld_unusual_alerts", True):
        try:
            for gid, text in reports.gld_unusual_alerts(ocfg, state.get("flow_sent", {}))[: ocfg.get("max_alerts_per_run", 5)]:
                outgoing.append((("flow_sent", gid), text))
        except Exception as e:  # un problema con CBOE no frena lo demas
            print(f"GLD inusual: {e}")


# ------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--chats", action="store_true", help="listar chats/grupos del bot")
    ap.add_argument("--report", choices=["daily", "preopen"], help="enviar ese reporte ahora")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        raise SystemExit("No se pudo leer config.json")
    tz = cfg.get("timezone_offset_hours", -3)

    if args.chats:
        list_chats()
        return

    if args.test:
        send_telegram("✅ <b>BTA TD9 Bot conectado.</b>\nTe avisare aqui cuando haya una senal confirmada.")
        if signals_chat_id():
            send_telegram("✅ <b>Canal BTA TD9 Señales conectado.</b>\nAqui se publicaran las senales confirmadas.",
                          signals_chat_id())
        print("Mensaje de prueba enviado.")
        return

    p = Params(**cfg.get("params", {}))
    max_age = cfg.get("max_signal_age_bars", 3)
    notify_nine = cfg.get("notify_nine_formed", False)
    state = prune(load_json(STATE_PATH, {"sent": {}}))
    now_ms = int(time.time() * 1000)
    outgoing, errors, skipped, checked = [], [], [], 0

    # Bienvenida a quien escribe /start (no detiene nada si falla)
    if not args.status and not args.report:
        try:
            n = answer_starts(cfg, state, args.dry_run)
            if n:
                print(f"Bienvenidas enviadas: {n}")
        except Exception as e:
            print(f"Bienvenida: {e}")

    # Opciones (reportes y operaciones grandes). Un error aqui no detiene el TD9.
    if not args.status:
        try:
            run_options(cfg, state, args, outgoing)
        except Exception as e:
            errors.append(f"opciones: {e}")
    if args.report:
        cfg = dict(cfg, symbols=[])  # solo el reporte pedido

    for symbol in cfg["symbols"]:
        cache = {}  # una sola descarga por temporalidad (ahorra consultas)

        def get(interval):
            if interval not in cache:
                cache[interval] = fetch_symbol(cfg, symbol, interval, 500)
            return cache[interval]

        for tf, htf in cfg["timeframes"].items():
            try:
                candles = get(tf)
                signals, st = analyze(candles, get(htf), p)
                checked += 1
            except SkipSymbol as e:
                skipped.append(f"{symbol}: {e}")
                break
            except Exception as e:  # una temporalidad con error no frena las demas
                errors.append(f"{symbol} {tf}: {e}")
                continue

            if args.status:
                v, c = st["venta"], st["compra"]
                print(f"{symbol} {tf:>4}: conteo up {st['up']} / dn {st['dn']} | "
                      f"VENTA {v['verdict']} ({v['score']}/6) | COMPRA {c['verdict']} ({c['score']}/6)")

            for sig in signals:
                if now_ms - sig.close_time > max_age * TF_MS[tf]:
                    continue  # senal vieja: no avisar
                key = f"{symbol}|{tf}|{sig.side}|{sig.close_time}"
                if key in state["sent"]:
                    continue
                outgoing.append((key, signal_message(symbol, tf, htf, sig, tz, cfg)))

            if notify_nine and tf in cfg.get("nine_timeframes", list(cfg["timeframes"])):
                # aviso temprano: 9 vigente, sin invalidar ni confirmar todavia
                for side in ("venta", "compra"):
                    s = st[side]
                    if not (s["active"] and s["verdict"] == "Esperando vela" and s["nine_open_time"]):
                        continue
                    nine_close = s["nine_open_time"] + TF_MS[tf]
                    if now_ms - nine_close > max_age * TF_MS[tf]:
                        continue  # 9 viejo: no avisar
                    key = f"{symbol}|{tf}|NUEVE-{side}|{s['nine_open_time']}"
                    if key not in state["sent"]:
                        outgoing.append((key, nine_message(symbol, tf, side, s, tz)))

    for s in skipped:
        print(f"Omitido {s}")
    if args.status:
        if errors:
            print("Errores:\n  " + "\n  ".join(errors))
        return

    for key, text in outgoing:
        print("-" * 40 + "\n" + text)
        if args.dry_run:
            continue
        is_options = key is None or isinstance(key, tuple)
        try:
            send_telegram(text, options_chat_id() if is_options else signals_chat_id())
        except SystemExit as e:
            errors.append(str(e))
            continue
        if isinstance(key, tuple):          # ("reports" | "flow_sent", id)
            state.setdefault(key[0], {})[key[1]] = time.time()
        elif key:                           # senal TD9
            state["sent"][key] = time.time()

    if not outgoing:
        print("Sin senales nuevas.")
    if errors:
        print("Errores:\n  " + "\n  ".join(errors))
    if not args.dry_run:
        STATE_PATH.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    if errors and (any("Telegram" in e or "Faltan" in e for e in errors) or (checked == 0 and cfg["symbols"])):
        sys.exit(1)  # problema con Telegram o todo fallo: que GitHub lo marque en rojo


if __name__ == "__main__":
    main()
