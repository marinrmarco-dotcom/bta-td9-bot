"""
BTA TD9 Bot - avisos por Telegram de las senales del indicador "BTA TD9 Conf".

Uso:
  python bot.py              -> revisa todo y envia las senales nuevas
  python bot.py --dry-run    -> revisa y muestra en pantalla, sin enviar ni guardar
  python bot.py --status     -> muestra el estado del cuadro en cada temporalidad
  python bot.py --test       -> envia un mensaje de prueba a Telegram

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

from td9 import Params, analyze

ROOT = Path(__file__).parent
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
TF_LABEL = {"1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "8h": "8H", "12h": "12H",
            "1d": "1D", "3d": "3D", "1w": "1S", "1M": "1M"}


# ----------------------------------------------------------------- datos ---
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
def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=20)
    if not r.ok:
        raise SystemExit(f"Telegram respondio {r.status_code}: {r.text[:300]}")


# ---------------------------------------------------------------- formato ---
def fmt(x):
    s = f"{x:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def local_time(ms, tz_hours):
    return (datetime.fromtimestamp(ms / 1000, timezone.utc) + timedelta(hours=tz_hours)).strftime("%d-%m %H:%M")


CHECK_NAMES = {"perf": "9 perfeccionado", "rsi": "RSI extremo", "div": "Divergencia RSI",
               "bb": "Toque Bollinger", "htf": "TD mayor >= 7", "candle": "Vela de confirmacion"}


def signal_message(symbol, tf, htf, sig, tz_hours):
    entry, stop = sig.close, sig.invalid_level
    dist = abs(stop - entry)
    target = entry - dist if sig.side == "VENTA" else entry + dist
    risk = dist / entry * 100
    icon = "🔴" if sig.side == "VENTA" else "🟢"
    checks = "\n".join(f"{'✅' if v else '❌'} {CHECK_NAMES[k]}" for k, v in sig.details.items())
    return (
        f"{icon} <b>{sig.side} CONFIRMADA · {symbol} {TF_LABEL.get(tf, tf)}</b>\n"
        f"Confluencias: <b>{sig.score}/6</b>\n\n"
        f"Entrada (cierre): <b>{fmt(entry)}</b>\n"
        f"Stop (invalida): {fmt(stop)}  ·  riesgo {risk:.2f}%\n"
        f"Objetivo 1:1: {fmt(target)}\n\n"
        f"{checks}\n\n"
        f"Vela 9: {local_time(sig.nine_open_time, tz_hours)} · cierre confirmacion: "
        f"{local_time(sig.close_time + 1, tz_hours)} (hora Chile)\n"
        f"TD mayor: {TF_LABEL.get(htf, htf)} · BTA TD9 Confluencias\n"
        f"<i>No es asesoria financiera.</i>"
    )


def nine_message(symbol, tf, side, st, tz_hours):
    word = "venta" if side == "venta" else "compra"
    sign = "<" if side == "venta" else ">"
    inv_sign = ">" if side == "venta" else "<"
    return (
        f"🟡 <b>9 de {word} formado · {symbol} {TF_LABEL.get(tf, tf)}</b>\n"
        f"Esperando vela de confirmacion (cierre {sign} {fmt(st['confirm'])})\n"
        f"Invalida si cierra {inv_sign} {fmt(st['invalid'])}\n"
        f"Confluencias por ahora: {st['score']}/6"
    )


# ----------------------------------------------------------------- estado ---
def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def prune(state, days=45):
    cutoff = time.time() - days * 86400
    state["sent"] = {k: v for k, v in state.get("sent", {}).items() if v >= cutoff}
    return state


# ------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        raise SystemExit("No se pudo leer config.json")
    tz = cfg.get("timezone_offset_hours", -3)

    if args.test:
        send_telegram("✅ <b>BTA TD9 Bot conectado.</b>\nTe avisare aqui cuando haya una senal confirmada.")
        print("Mensaje de prueba enviado.")
        return

    p = Params(**cfg.get("params", {}))
    max_age = cfg.get("max_signal_age_bars", 3)
    notify_nine = cfg.get("notify_nine_formed", False)
    state = prune(load_json(STATE_PATH, {"sent": {}}))
    now_ms = int(time.time() * 1000)
    outgoing, errors = [], []

    for symbol in cfg["symbols"]:
        htf_cache = {}
        for tf, htf in cfg["timeframes"].items():
            try:
                candles = fetch_closed(symbol, tf, 500)
                if htf not in htf_cache:
                    htf_cache[htf] = fetch_closed(symbol, htf, 300)
                signals, st = analyze(candles, htf_cache[htf], p)
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
                outgoing.append((key, signal_message(symbol, tf, htf, sig, tz)))

            if notify_nine:
                last_ct = candles[-1]["close_time"]
                for side, count in (("venta", st["up"]), ("compra", st["dn"])):
                    if count == 9 and now_ms - last_ct <= max_age * TF_MS[tf]:
                        key = f"{symbol}|{tf}|NUEVE-{side}|{last_ct}"
                        if key not in state["sent"]:
                            outgoing.append((key, nine_message(symbol, tf, side, st[side], tz)))

    if args.status:
        return

    for key, text in outgoing:
        print("-" * 40 + "\n" + text)
        if not args.dry_run:
            send_telegram(text)
            state["sent"][key] = time.time()

    if not outgoing:
        print("Sin senales nuevas.")
    if errors:
        print("Errores:\n  " + "\n  ".join(errors))
    if not args.dry_run:
        STATE_PATH.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    if errors and len(errors) == sum(len(cfg["timeframes"]) for _ in cfg["symbols"]):
        sys.exit(1)  # todo fallo: que GitHub lo marque en rojo


if __name__ == "__main__":
    main()
