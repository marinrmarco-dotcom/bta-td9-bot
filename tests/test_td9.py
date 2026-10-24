"""
Pruebas del motor contra lo que muestra TradingView (BINANCE:BTCUSDT).
Ejecutar:  python -m pytest -q   (o simplemente: python tests/test_td9.py)
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from td9 import analyze  # noqa: E402

DATA = ROOT / "tests" / "data"


def load(name, ms):
    rows = [list(map(float, r)) for r in csv.reader(open(DATA / name))]
    return [dict(open_time=int(r[0]) * 1000, close_time=int(r[0]) * 1000 + int(ms) - 1,
                 high=r[1], low=r[2], close=r[3]) for r in rows]


WEEK = load("btc_1w.csv", 7 * 86_400_000)[:-1]   # la ultima semana estaba abierta
H4 = load("btc_4h.csv", 4 * 3_600_000)


def test_semanal_igual_a_tradingview():
    # Cuadro en TradingView 1S (24-oct-2026): 1/6, confirma < 79800, invalida > 87395.67, Esperando vela
    _, st = analyze(WEEK, WEEK)
    v = st["venta"]
    assert st["up"] == 9
    assert v["verdict"] == "Esperando vela"
    assert v["score"] == 1
    assert v["confirm"] == 79800
    assert v["invalid"] == 87395.67


def test_4h_venta_21_oct_igual_a_tradingview():
    # TradingView 4H: etiqueta VENTA el 21-oct, confirma < 84057.59, invalida > 84898.00
    signals, _ = analyze(H4, WEEK)
    ventas = [s for s in signals if s.side == "VENTA"]
    assert len(ventas) == 1
    s = ventas[0]
    assert s.confirm_level == 84057.59
    assert s.invalid_level == 84898
    assert s.score >= 4


if __name__ == "__main__":
    test_semanal_igual_a_tradingview()
    test_4h_venta_21_oct_igual_a_tradingview()
    print("OK: el motor coincide con TradingView")
