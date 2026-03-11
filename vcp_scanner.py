import pandas as pd
import numpy as np
import requests
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

from kite_auth import get_kite_client


# ==============================
# LOAD ENV VARIABLES
# ==============================

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TOP_RESULTS = 5
MIN_VOLUME = 500000

START_DATE = datetime.now() - timedelta(days=20)

NIFTY_TOKEN = 256265


# ==============================
# TELEGRAM ALERT
# ==============================

def send_telegram(msg):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg
    }

    try:
        requests.post(url, data=payload)
    except:
        pass


# ==============================
# DATA FUNCTIONS
# ==============================

def fetch_data(kite, token):

    data = kite.historical_data(
        token,
        START_DATE,
        datetime.now(),
        "minute"
    )

    if not data:
        return None

    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])
    #print(df.tail(3))
    return df


def resample(df, tf):

    df = df.set_index("date")
    ohlc = df.resample(tf).agg({
        "open":"first",
        "high":"max",
        "low":"min",
        "close":"last",
        "volume":"sum"
    })
    return ohlc.dropna()


# ==============================
# INDICATORS
# ==============================

def ema(series, period):

    return series.ewm(span=period).mean()


def relative_strength(stock, index):

    rs = stock["close"] / index["close"]
    return rs.iloc[-1] > rs.rolling(20).max().iloc[-2]




# ==============================
# VCP DETECTION
# ==============================

def detect_base(df):

    df = df.copy()

    window = 30

    base = df.iloc[-window:]

    high = base["high"].max()
    low = base["low"].min()

    depth = (high - low) / high

    # acceptable base depth
    if depth < 0.05 or depth > 0.35:
        return None

    # price near highs
    last_close = base["close"].iloc[-1]

    near_high = last_close > high * 0.85

    if not near_high:
        return None

    pivot = high

    return {
        "pivot": pivot,
        "depth": depth
    }


def vcp_tightening(df):

    df = df.copy()

    df["range"] = df["high"] - df["low"]

    r1 = df["range"].iloc[-5:].mean()
    r2 = df["range"].iloc[-10:-5].mean()
    r3 = df["range"].iloc[-20:-10].mean()

    contraction_count = 0

    if r1 < r2:
        contraction_count += 1

    if r2 < r3:
        contraction_count += 1

    return contraction_count


def distance_from_pivot(price, pivot):

    return (pivot - price) / pivot * 100



def detect_vcp(df):

    df = df.copy()
    df["range"] = df["high"] - df["low"]

    # Average ranges
    r1 = df["range"].iloc[-5:].mean()
    r2 = df["range"].iloc[-10:-5].mean()
    r3 = df["range"].iloc[-20:-10].mean()

    # Allow partial contraction
    contraction = (r1 < r2) or (r2 < r3)

    # Allow slightly further from highs
    near_high = df["close"].iloc[-1] > df["high"].rolling(30).max().iloc[-1] * 0.85

    # Volume drying slightly
    vol_recent = df["volume"].iloc[-5:].mean()
    vol_old = df["volume"].iloc[-20:].mean()

    volume_dry = vol_recent < vol_old * 1.2

    return contraction and near_high and volume_dry


# ==============================
# BREAKOUT
# ==============================

def breakout(df):

    pivot = df["high"].rolling(20).max().iloc[-2]

    close = df["close"].iloc[-1]

    # breakout
    if close > pivot:
        return "BREAKOUT"

    # very close to pivot
    if close > pivot * 0.97:
        return "NEAR"

    return None


# ==============================
# RANKING
# ==============================

def score_stock(df):

    momentum = df["close"].iloc[-1] / df["close"].iloc[-20]

    tightness = df["high"].iloc[-20:].max() - df["low"].iloc[-20:].min()

    score = momentum * 100 - tightness

    return score


# ==============================
# LOAD NIFTY300
# ==============================

def load_universe():

    # Load NIFTY300 symbol list (symbols are stored as column headers)
    nifty_df = pd.read_csv("data/nifty300.csv")

    symbols = [col.strip() for col in nifty_df.columns]

    # Load instrument cache
    inst = pd.read_csv("data/instruments_cache.csv")

    # Keep only NSE equities
    inst = inst[(inst["exchange"] == "NSE")]

    # Match symbols
    universe = inst[inst["tradingsymbol"].isin(symbols)]

    # Keep only required columns
    universe = universe[["tradingsymbol", "instrument_token"]]

    universe.columns = ["symbol", "token"]

    return universe


# ==============================
# PROCESS ONE STOCK
# ==============================

def analyze_stock(kite, symbol, token, nifty_75):

    try:

        df = fetch_data(kite, token)
        print(symbol, "checked")
        if df is None:
            return None

        df75 = resample(df, "75min")
        df15 = resample(df, "15min")

        if len(df75) < 30:
            return None

        if df75["volume"].iloc[-1] < MIN_VOLUME:
            return None

        df75["ema50"] = ema(df75["close"], 50)
        df75["ema200"] = ema(df75["close"], 200)

        trend = df75["ema50"].iloc[-1] > df75["ema200"].iloc[-1]

        '''
        if not trend:
            return None

        if not relative_strength(df75, nifty_75):
            return None

        if detect_vcp(df75):
            br = breakout(df15)
            if br:
                score = score_stock(df75)
                return (symbol, score, br)
        '''

        base = detect_base(df75)
        if not base:
            return None

        pivot = base["pivot"]

        contractions = vcp_tightening(df75)

        price = df75["close"].iloc[-1]
        distance = distance_from_pivot(price, pivot)
        if distance < 5:
            score = score_stock(df75) + contractions * 5
            return (symbol, score, pivot, distance)

    except:
        return None


# ==============================
# MAIN SCANNER
# ==============================

def run_scanner():

    print("🚀 Running Fast VCP Scanner")

    kite = get_kite_client(API_KEY, API_SECRET)

    universe = load_universe()

    nifty_df = fetch_data(kite, NIFTY_TOKEN)

    nifty_75 = resample(nifty_df, "75min")

    results = []

    with ThreadPoolExecutor(max_workers=10) as executor:

        futures = []

        for _, row in universe.iterrows():
            print("[DEBUG]..", row["symbol"])
            futures.append(

                executor.submit(
                    analyze_stock,
                    kite,
                    row["symbol"],
                    row["token"],
                    nifty_75
                )

            )

        for future in as_completed(futures):

            res = future.result()
            if res:
                results.append(res)


    results = sorted(results, key=lambda x: x[1], reverse=True)

    top = results[:TOP_RESULTS]

    if len(top) == 0:
        print("No setups found")
        return

    message = "📊 VCP BASE SCANNER\n\n"

for stock in top:

    message += f"{stock[0]} | Pivot {round(stock[2],2)} | {round(stock[3],2)}% away\n"

    print(message)

    send_telegram(message)


# ==============================
# RUN
# ==============================

if __name__ == "__main__":

    run_scanner()