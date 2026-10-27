"""Pruebas del modulo de opciones con datos sinteticos.  Ejecutar: python tests/test_options.py"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import options_data as od  # noqa: E402
import reports  # noqa: E402

NOW = datetime(2026, 10, 24, 18, tzinfo=timezone.utc)


def test_parseo():
    assert od.parse_deribit_name("BTC-27NOV26-73000-P") == (73000.0, datetime(2026, 11, 27, 8, tzinfo=timezone.utc), False)
    root, k, exp, is_call = od.parse_occ("GLD261023C00205000")
    assert (root, k, is_call) == ("GLD", 205.0, True) and exp.date().isoformat() == "2026-10-23"


def test_niveles():
    e = NOW + timedelta(days=6)
    opts = [od.Opt(110, e, True, 5000, 0.3), od.Opt(90, e, False, 4000, 0.3), od.Opt(100, e, True, 1000, 0.3)]
    lv = od.levels(opts, 100, 100, now=NOW)
    assert lv["call_wall"] == 110 and lv["put_wall"] == 90 and lv["gamma_strike"] == 100
    assert lv["max_pain"] == 90
    assert lv["flip"] is None or 80 < lv["flip"] < 120


def test_flujos_y_agrupacion():
    tr = [dict(trade_id="1", instrument_name="BTC-27NOV26-90000-C", direction="buy", price=0.01, amount=300,
               index_price=80000, iv=40, timestamp=1),
          dict(trade_id="2", instrument_name="BTC-27NOV26-70000-P", direction="sell", price=0.02, amount=10,
               index_price=80000, iv=45, timestamp=2, block_trade_id="B1"),
          dict(trade_id="3", instrument_name="BTC-25DEC26-60000-P", direction="buy", price=0.005, amount=10,
               index_price=80000, iv=50, timestamp=2, block_trade_id="B1")]
    fs = od.flow_summary(tr)
    assert fs["call_buy"] == 240000 and fs["blocks"] == 1 and fs["bias"] > 0
    big = od.unusual_orders(tr, 200000, 1e12)
    assert len(big) == 1 and "COMPRA Call 90.000" in reports.order_line(big[0])


def test_horarios():
    assert reports.due("daily", {}, {}, datetime(2026, 10, 24, 11, 40, tzinfo=timezone.utc))[0]      # 08:40 Chile
    assert not reports.due("daily", {}, {}, datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc))[0]   # 07:00 Chile
    assert reports.due("preopen", {}, {}, datetime(2026, 10, 26, 13, 20, tzinfo=timezone.utc))[0]    # 09:20 NY (EDT)
    assert reports.due("preopen", {}, {}, datetime(2026, 11, 2, 14, 20, tzinfo=timezone.utc))[0]     # 09:20 NY (EST)
    assert not reports.due("preopen", {}, {}, datetime(2026, 10, 24, 13, 20, tzinfo=timezone.utc))[0]  # sabado


def test_compras_gld():
    def row(sym, vol, oi, last, bid, ask):
        x = dict(option=sym, volume=vol, open_interest=oi, last_trade_price=last, bid=bid, ask=ask, iv=0.2)
        _, k, exp, c = od.parse_occ(sym)
        return (x, k, exp, c)
    raw = [row("GLD261120C00380000", 4000, 900, 3.20, 3.05, 3.20),   # compra
           row("GLD261120P00350000", 5000, 800, 2.00, 2.00, 2.15),   # venta
           row("GLD261120C00400000", 1500, 100, 0.40, 0.35, 0.41)]   # prima chica
    buys = od.cboe_unusual_buys(raw, now=NOW, min_volume=1000, min_premium=250000)
    assert len(buys) == 1 and buys[0]["strike"] == 380 and buys[0]["is_call"]
    assert not reports.gld_market_open(datetime(2026, 10, 25, 15, 30, tzinfo=timezone.utc))  # domingo
    assert reports.gld_market_open(datetime(2026, 10, 27, 15, 30, tzinfo=timezone.utc))      # martes 11:30 NY


if __name__ == "__main__":
    test_parseo(); test_niveles(); test_flujos_y_agrupacion(); test_horarios(); test_compras_gld()
    print("OK: modulo de opciones")
