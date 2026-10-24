# BTA TD9 Bot

Bot que revisa BTCUSDT (Binance) cada 15 minutos con la misma lógica del indicador **BTA TD9 Confluencias** de TradingView y te avisa por **Telegram** cuando hay una señal **CONFIRMADA**: un 9 con vela de confirmación y al menos 4 de 6 confluencias.

Corre gratis en **GitHub Actions**. No depende de tu PC ni de tu plan de TradingView.

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
2. En el repo: **Add file → Upload files** y arrastra `bot.py`, `td9.py`, `config.json`, `state.json`, `requirements.txt`, `README.md` y la carpeta `tests`. Luego presiona **Commit changes**.
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
- `bot.py`: descarga las velas de Binance, detecta señales nuevas y las envía a Telegram.
- `state.json`: señales ya enviadas, para no repetirlas. El workflow lo actualiza solo.
- `.github/workflows/td9-bot.yml`: el horario en GitHub Actions.

> GitHub puede atrasar unos minutos las ejecuciones programadas. En repos públicos sin actividad por 60 días, desactiva el horario; si pasa, basta con reactivarlo en Actions.

*Herramienta de análisis. No es asesoría financiera.*
