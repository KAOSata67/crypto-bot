import os
import time
import json
import threading
from datetime import datetime

import requests
import pandas as pd

# ==================================================
# TELEGRAM AYARLARI
# ==================================================
TELEGRAM_TOKEN = "9075132522:AAE4Q3fkgmxYwCa1fbGv0CCKgVPtYvmm_Gc"
CHAT_ID = "8744182299"

# ==================================================
# BOT AYARLARI
# ==================================================
BASE_URL = "https://api.binance.com/api/v3/klines"
TIMEFRAME = "1h"
CANDLE_LIMIT = 250
CHECK_INTERVAL = 60
REQUEST_TIMEOUT = 15

# BOS son kaç kapanmış mumda aranacak?
BOS_SEARCH_CANDLES = 3

# ==================================================
# SMC AYARLARI
# ==================================================
SMC_LOOKBACK = 20
USE_SMC_LOOKBACK = True

# ==================================================
# SİNYAL AYARLARI
# ==================================================
RISK_REWARD = 2.0
SIGNAL_FILE = "signals.json"
ENABLE_TEST_COMMAND = True
TELEGRAM_POLL_INTERVAL = 2

# ==================================================
# COİN LİSTESİ
# ==================================================
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "AVAXUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "LTCUSDT",
    "TRXUSDT", "TONUSDT", "DOTUSDT",
]

session = requests.Session()


def load_signals():
    if os.path.exists(SIGNAL_FILE):
        try:
            with open(SIGNAL_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
                return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            print("Sinyal kayıt dosyası okunamadı; boş kayıtla devam ediliyor.")
    return {}


def save_signals(data):
    temp_file = SIGNAL_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)
    os.replace(temp_file, SIGNAL_FILE)


active_signals = load_signals()


def telegram_configured():
    return bool(TELEGRAM_TOKEN and CHAT_ID)


def send_telegram_message(message, chat_id=None):
    if not telegram_configured():
        print("Telegram ayarlanmamış.")
        return False

    target_chat = str(chat_id if chat_id is not None else CHAT_ID)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        response = session.post(
            url,
            data={"chat_id": target_chat, "text": message, "parse_mode": "Markdown"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            print("Telegram bildirimi gönderildi.")
            return True
        print("Telegram API hatası:", result.get("description", "Bilinmeyen hata"))
    except requests.RequestException as error:
        print("Telegram bağlantı hatası:", error)
    return False


def telegram_command_listener():
    if not telegram_configured():
        return

    offset = None
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    print("Telegram /test komut dinleyicisi aktif.")

    while True:
        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            response = session.get(url, params=params, timeout=30)
            response.raise_for_status()
            result = response.json()

            if not result.get("ok"):
                time.sleep(TELEGRAM_POLL_INTERVAL)
                continue

            for update in result.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message") or update.get("edited_message")
                if not message:
                    continue

                text = (message.get("text") or "").strip().lower()
                chat_id = str(message.get("chat", {}).get("id", ""))
                if chat_id != str(CHAT_ID):
                    continue

                if text in ("/test", "/test@" + os.getenv("TELEGRAM_BOT_USERNAME", "").lower()):
                    send_telegram_message(
                        "✅ *Test başarılı!*\n\nTelegram bağlantısı çalışıyor.\nBot sinyal taramasına devam ediyor.",
                        chat_id=chat_id,
                    )

        except requests.RequestException as error:
            print("Telegram komut bağlantı hatası:", error)
            time.sleep(TELEGRAM_POLL_INTERVAL)
        except Exception as error:
            print("Telegram komut dinleyici hatası:", error)
            time.sleep(TELEGRAM_POLL_INTERVAL)


def get_data(symbol):
    params = {"symbol": symbol, "interval": TIMEFRAME, "limit": CANDLE_LIMIT}
    response = session.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    columns = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(data, columns=columns)
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    for column in ["open_time", "close_time"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    now_ms = int(time.time() * 1000)
    df = df[df["close_time"] < now_ms].copy()
    df.dropna(subset=["open", "high", "low", "close", "open_time"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def calculate_ema(df):
    df = df.copy()
    df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
    return df


def find_swings(df, end_index=None):
    if end_index is None:
        end_index = len(df) - 1

    data = df.iloc[: end_index + 1].copy()
    if USE_SMC_LOOKBACK:
        data = data.tail(SMC_LOOKBACK + 4)
    data = data.reset_index(drop=True)

    if len(data) < 5:
        return None, None

    highs = data["high"].tolist()
    lows = data["low"].tolist()
    swing_highs, swing_lows = [], []

    for i in range(2, len(data) - 2):
        if (highs[i] > highs[i - 1] and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]):
            swing_highs.append({"price": float(highs[i])})
        if (lows[i] < lows[i - 1] and lows[i] < lows[i - 2]
                and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]):
            swing_lows.append({"price": float(lows[i])})

    return (
        swing_highs[-1] if swing_highs else None,
        swing_lows[-1] if swing_lows else None,
    )


def detect_bos(df):
    first_candidate = max(1, len(df) - BOS_SEARCH_CANDLES)
    detected = None

    for idx in range(first_candidate, len(df)):
        previous = df.iloc[idx - 1]
        current = df.iloc[idx]
        swing_high, swing_low = find_swings(df, end_index=idx)

        if swing_high is not None:
            level = swing_high["price"]
            if (previous["close"] <= level
                    and current["close"] > level
                    and current["EMA20"] > current["EMA50"]):
                detected = {
                    "direction": "LONG",
                    "breakout_index": idx,
                    "swing_high": swing_high,
                    "swing_low": swing_low,
                }

        if swing_low is not None:
            level = swing_low["price"]
            if (previous["close"] >= level
                    and current["close"] < level
                    and current["EMA20"] < current["EMA50"]):
                detected = {
                    "direction": "SHORT",
                    "breakout_index": idx,
                    "swing_high": swing_high,
                    "swing_low": swing_low,
                }

    return detected


def format_price(price):
    return f"{price:.8f}".rstrip("0").rstrip(".")


def format_candle_time(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M")


def create_signal(symbol, df, detected):
    current = df.iloc[-1]
    breakout = df.iloc[detected["breakout_index"]]
    direction = detected["direction"]
    entry = float(current["close"])

    latest_high, latest_low = find_swings(df, end_index=len(df) - 1)

    if direction == "LONG":
        stop_loss = float(latest_low["price"]) if latest_low else entry * 0.98
        if stop_loss >= entry:
            stop_loss = entry * 0.98
        risk = entry - stop_loss
        take_profit = entry + risk * RISK_REWARD
    else:
        stop_loss = float(latest_high["price"]) if latest_high else entry * 1.02
        if stop_loss <= entry:
            stop_loss = entry * 1.02
        risk = stop_loss - entry
        take_profit = entry - risk * RISK_REWARD

    breakout_time = int(breakout["open_time"])
    current_time = int(current["open_time"])
    message = f"""
🚨 *SMC + EMA SİNYALİ*

🪙 *Coin:* {symbol}
📊 *Yön:* {direction}
⏱ *Zaman Dilimi:* {TIMEFRAME}

━━━━━━━━━━━━━━━━

💰 *Güncel Giriş:* `{format_price(entry)}`
🛑 *Stop Loss:* `{format_price(stop_loss)}`
🎯 *Take Profit:* `{format_price(take_profit)}`

━━━━━━━━━━━━━━━━

📈 *EMA20:* {format_price(float(current['EMA20']))}
📉 *EMA50:* {format_price(float(current['EMA50']))}
🔍 *SMC:* BOS kırılımı tespit edildi.
🕯 *Kırılım mumu:* {format_candle_time(breakout_time)}
🕒 *Sinyal kontrol mumu:* {format_candle_time(current_time)}
⚖️ *Risk / Ödül:* 1:{RISK_REWARD:g}

ℹ️ BOS son {BOS_SEARCH_CANDLES} kapanmış mumda aranır.
Yalnızca kapanmış mumlar kullanılır.
Otomatik işlem açılmaz; bu yalnızca analiz sinyalidir.
"""
    return message, breakout_time


def scan_symbol(symbol):
    try:
        df = get_data(symbol)
        if len(df) < 60:
            print(f"{symbol}: Yetersiz veri ({len(df)} mum).")
            return

        df = calculate_ema(df)
        detected = detect_bos(df)
        if detected is None:
            print(f"{symbol}: Son {BOS_SEARCH_CANDLES} mumda uygun BOS yok.")
            return

        direction = detected["direction"]
        message, breakout_time = create_signal(symbol, df, detected)
        signal_key = f"{symbol}_{direction}"

        if active_signals.get(signal_key) == breakout_time:
            print(f"{symbol}: Bu kırılım için bildirim zaten gönderilmiş.")
            return

        print(f"{symbol}: {direction} sinyali bulundu!")
        if send_telegram_message(message):
            active_signals[signal_key] = breakout_time
            save_signals(active_signals)

    except requests.RequestException as error:
        print(f"{symbol} veri bağlantı hatası: {error}")
    except Exception as error:
        print(f"{symbol} taranırken hata: {error}")


def main():
    print("================================")
    print("       SMC + EMA SIGNAL BOT")
    print("================================")
    print("Zaman Dilimi:", TIMEFRAME)
    print("SMC Lookback:", SMC_LOOKBACK if USE_SMC_LOOKBACK else "Sınırsız")
    print("BOS arama penceresi:", BOS_SEARCH_CANDLES, "kapanmış mum")
    print("EMA filtresi: EMA20 > EMA50 (LONG) / EMA20 < EMA50 (SHORT)")
    print("Taranan coin:", len(SYMBOLS))
    print("Otomatik işlem: KAPALI")

    if ENABLE_TEST_COMMAND:
        threading.Thread(target=telegram_command_listener, daemon=True).start()

    send_telegram_message(
        "🚀 *SMC + EMA Sinyal Botu* başlatıldı.\n"
        f"Zaman dilimi: {TIMEFRAME}\n"
        f"BOS arama penceresi: Son {BOS_SEARCH_CANDLES} kapanmış mum\n"
        "EMA yön filtresi aktif.\n"
        "Otomatik işlem: Kapalı"
    )

    while True:
        start_time = time.time()
        print("\nYeni tarama başladı...")
        for symbol in SYMBOLS:
            print(f"Kontrol ediliyor: {symbol}")
            scan_symbol(symbol)
            time.sleep(0.2)

        elapsed = time.time() - start_time
        print(f"\nTarama tamamlandı. Süre: {elapsed:.2f} saniye")
        time.sleep(max(1, CHECK_INTERVAL - elapsed))


if __name__ == "__main__":
    main()