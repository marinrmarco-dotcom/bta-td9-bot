# BTA TD9 Bot

Bot de Telegram que corre gratis en **GitHub Actions**, sin depender de tu PC ni de tu plan de TradingView. Hace tres cosas:

1. **Señales TD9:** revisa BTCUSDT (Binance) cada 15 minutos con la misma lógica del indicador **BTA TD9 Confluencias** y avisa cuando hay una señal **CONFIRMADA**: un 9 con vela de confirmación y al menos 4 de 6 confluencias.
2. **Reportes de opciones:**
   - **Diario a las 08:30 (hora Chile)** y **15 minutos antes de la apertura de Wall Street** (días hábiles).
   - **BTC (Deribit):** GEX neto, Call Wall, Put Wall, strike de mayor gamma, Gamma flip, Max Pain y el flujo de calls y puts compradas y vendidas, con su sesgo.
   - **XAUUSD (vía GLD, datos CBOE):** los mismos niveles, convertidos a XAUUSD con el ratio PAXG/GLD, más los contratos de GLD con volumen inusual.
3. **Alertas de operaciones grandes en BTC (Deribit):** compras o ventas de opciones con prima ≥ USD 500K o nocional ≥ USD 25M, en un máximo de 15 minutos desde que ocurren.

Todas las fuentes son públicas y gratuitas. No hace falta ninguna API key aparte del bot de Telegram.

**Límites de la versión gratuita:**
- En GLD no hay dirección del flujo (compra o venta). Eso requiere una API de pago, como Unusual Whales.
- Los datos de CBOE vienen con 15 minutos de retraso.
- El GEX usa la convención clásica (calls suman, puts restan); es una aproximación, igual que en las plataformas comerciales.

---

## Instalación (una sola vez, ~15 minutos)

### 1. Crear el bot de Telegram
1. En Telegram, abre **@BotFather** y envía `/newbot`.
2. Ponle un nombre (por ejemplo `BTA TD9 Alertas`) y un usuario que termine en `bot`.
3. BotFather te entrega un **token** (`123456:ABC...`). Guárdalo y **no lo compartas con nadie**, tampoco en chats.

> Si prefieres usar el bot que ya tienes para XAUUSD, sirve su mismo token.

### 2. Obtener tu chat_id
1. Abre tu bot nuevo en Telegram y envíale cualquier mensaje (por ejemplo "hola").
2. En tu navegador abre esta dirección, reemplazando `TU_TOKEN`:
   `https://api.telegram.org/botTU_TOKEN/getUpdates`
3. Busca `"chat":{"id":` y copia el número. Ese es tu **chat_id**.
   Para recibir los avisos en un grupo o canal, agrega el bot ahí y usa el id del grupo (empieza con `-100`).

### 3. Crear el repositorio en GitHub
1. En GitHub: **New repository** → nombre `bta-td9-bot`.
   - **Público** = minutos de Actions ilimitados (el token queda protegido igual, porque va como Secret).
   - **Privado** = tiene un límite mensual de minutos gratis. Correr cada 15 minutos puede superarlo; en ese caso cambia el cron a cada 30 minutos.
2. En el repo: **Add file → Upload files** y arrastra `bot.py`, `td9.py`, `options_data.py`, `reports.py`, `config.json`, `state.json`, `requirements.txt`, `README.md` y la carpeta `tests`. Luego presiona **Commit changes**.
3. El workflow va en una carpeta oculta, así que créalo a mano: **Add file → Create new file**. En el nombre escribe exactamente
   `.github/workflows/td9-bot.yml`
   y pega el contenido del archivo `td9-bot.yml` incluido en el zip (está en `.github/workflows/`). En Mac, con `Cmd+Shift+.` se ven los archivos ocultos en Finder. Luego presiona **Commit changes**.

### 4. Guardar el token y el chat_id como Secrets
En el repo: **Settings → Secrets and variables → Actions → New repository secret**
- `TELEGRAM_BOT_TOKEN` = el token de BotFather
- `TELEGRAM_CHAT_ID` = tu chat_id

### 5. Probar
1. Pestaña **Actions** → si te lo pide, presiona **"I understand my workflows, enable them"**.
2. Abre **BTA TD9 Bot → Run workflow** → modo **prueba**. Te debería llegar "✅ BTA TD9 Bot conectado".
3. Vuelve a ejecutarlo en modo **estado**: en el log del job aparece el cuadro de cada temporalidad (equivalente al de TradingView).

4. Opcional: ejecútalo en modo **reporte_diario** para recibir el reporte de opciones al tiro, sin esperar a mañana.

Desde ese momento corre solo cada 15 minutos.

---

## Configuración (`config.json`)

| Campo | Qué hace |
|---|---|
| `symbols` | Pares de Binance, por ejemplo `["BTCUSDT", "ETHUSDT"]` |
| `timeframes` | Temporalidad → su "temporalidad mayor" para la confluencia TD ≥ 7 |
| `params.min_score` | Confluencias mínimas (4 por defecto; 6 = solo señales completas) |
| `params.window` | Velas de validez tras el 9 |
| `notify_nine_formed` | `true` = también avisa cuando se forma un 9 (antes de confirmar) |
| `max_signal_age_bars` | No avisa señales más viejas que N velas (evita spam la primera vez) |
| `options.daily_report_time_cl` | Hora del reporte diario (hora Chile, por defecto `08:30`) |
| `options.daily_report` / `preopen_report` | `false` para apagar cada reporte |
| `options.btc_unusual_alerts` | `false` para apagar las alertas de operaciones grandes |
| `options.btc_min_premium_usd` / `btc_min_notional_usd` | Umbrales de operación grande. Súbelos si llegan demasiadas alertas |
| `options.gld_ratio_fallback` | Ratio XAU/GLD si no se puede leer PAXG (10.85) |

**Cómo leer el reporte:**
- **GEX positivo:** los market makers frenan los movimientos y el precio tiende a rangos.
- **GEX negativo:** los market makers amplifican los movimientos.
- **Gamma flip:** precio donde el GEX cambia de signo.
- **Call Wall / Put Wall:** strikes con más open interest (±20% del precio), que suelen actuar como resistencia o soporte.
- **Mayor gamma (pin):** strike que tiende a "atraer" el precio cerca de los vencimientos.

**Igual que en TradingView:** para que el cuadro del gráfico coincida con el bot, usa la misma "Temporalidad mayor (TD)" en el indicador. Por ejemplo, en el gráfico semanal ponla en 1M.

## Probar en tu computador (opcional)
```bash
pip install -r requirements.txt
python tests/test_td9.py        # verifica que el motor coincide con TradingView
python bot.py --status          # cuadro de cada temporalidad
python bot.py --dry-run         # muestra lo que enviaría, sin enviar
```

## Archivos
- `td9.py`: motor del indicador (la misma lógica del Pine, vela a vela, solo con velas cerradas).
- `bot.py`: descarga las velas de Binance, detecta señales nuevas y las envía a Telegram. Además dispara los reportes y las alertas de opciones.
- `options_data.py`: datos de Deribit (BTC) y CBOE (GLD), y el cálculo de GEX, walls, flip, max pain y flujos.
- `reports.py`: el texto de los reportes y alertas, y los horarios (hora Chile y Nueva York, con cambio de horario automático).
- `state.json`: señales ya enviadas, para no repetirlas. El workflow lo actualiza solo.
- `.github/workflows/td9-bot.yml`: el horario en GitHub Actions.

> GitHub puede atrasar unos minutos las ejecuciones programadas. En repos públicos sin actividad por 60 días, desactiva el horario; si pasa, basta con reactivarlo en Actions.

*Herramienta de análisis. No es asesoría financiera.*

---

## Activos y alertas de 9 (versión actual)

- **Activos:** BTCUSDT y ETHUSDT (Binance) y XAUUSD (velas reales de Twelve Data). El oro necesita el Secret `TWELVEDATA_API_KEY` (cuenta gratis en twelvedata.com); sin él, el bot omite XAUUSD sin fallar. Usa ~576 de las 800 consultas diarias gratis.
- **Alerta de 9 formado:** con `"notify_nine_formed": true` llega "⚠️ ALERTA · Se formó 9 de VENTA/COMPRA · ¡Estar atento!" con los niveles de confirmación e invalidación. `nine_timeframes` define en qué temporalidades se avisa (por ejemplo, quita `"15m"` si son demasiados).
- En `config.json`, la sección `sources` indica de dónde salen las velas de cada símbolo (por defecto Binance).
