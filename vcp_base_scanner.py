import pandas as pd
import numpy as np
import requests
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import matplotlib as mpl
mpl.use('Agg')
import mplfinance as mpf
from datetime import datetime
from kite_auth import get_kite_client
os.makedirs("charts", exist_ok=True)
import time
# ===============================
# LOAD ENV
# ===============================

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TOP_RESULTS = 10
START_DATE = datetime.now() - timedelta(days=30)
MIN_TURNOVER = 1e6

NIFTY_TOKEN = 256265


# ===============================
# TELEGRAM
# ===============================

def send_telegram(msg):

    if TELEGRAM_TOKEN is None:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg
    }

    try:
        requests.post(url, data=payload)
    except:
        pass

def save_chart(symbol, df, pivot=None):

    df = df.copy()

    df.index.name = "Date"

    # -----------------------------
    # Indicators
    # -----------------------------

    df["EMA50"] = df["close"].ewm(span=50).mean()
    df["EMA200"] = df["close"].ewm(span=200).mean()

    base_high = df["high"].iloc[-30:].max()
    base_low = df["low"].iloc[-30:].min()

    breakout = df["close"].iloc[-1] > pivot if pivot else False

    # -----------------------------
    # Plot overlays
    # -----------------------------

    addplots = [

        mpf.make_addplot(df["EMA50"], color="cyan", width=1),
        mpf.make_addplot(df["EMA200"], color="orange", width=1),

        mpf.make_addplot([base_high]*len(df), color="yellow", width=0.75),
        mpf.make_addplot([base_low]*len(df), color="yellow", width=0.75)

    ]

    if pivot:

        addplots.append(
            mpf.make_addplot([pivot]*len(df), color="red", width=0.75)
        )

    if breakout:

        addplots.append(
            mpf.make_addplot(
                [df["close"].iloc[-1]],
                type="scatter",
                markersize=60,
                marker="^",
                color="lime"
            )
        )

    # -----------------------------
    # File name
    # -----------------------------

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    filename = f"charts/{symbol}_{timestamp}.png"

    # -----------------------------
    # Plot
    # -----------------------------

    custom_style = mpf.make_mpf_style(base_mpf_style="nightclouds", gridcolor="#2a2a2a", gridstyle="--")
    mpf.plot(
        df,
        type="candle",
        style=custom_style,
        volume=True,
        figsize=(14,9),
        addplot=addplots,
        title=f"{symbol} | Pivot {pivot}",
        savefig=filename
    )

    print(f"Chart saved -> {filename}")

def add_vcp_visuals(df, legs):

    addplots = []

    for leg in legs:

        idx, peak, low, pullback = leg

        peak_series = [np.nan]*len(df)
        low_series = [np.nan]*len(df)

        peak_series[idx] = peak
        low_series[idx] = low

        # Peak marker
        addplots.append(
            mpf.make_addplot(
                peak_series,
                type="scatter",
                marker="v",
                color="magenta",
                markersize=30
            )
        )

        # Low marker
        addplots.append(
            mpf.make_addplot(
                low_series,
                type="scatter",
                marker="^",
                color="magenta",
                markersize=30
            )
        )

    return addplots


def prior_uptrend(df):

    recent_low = df["low"].iloc[-150:].min()
    price = df["close"].iloc[-1]

    advance = (price - recent_low) / recent_low

    return advance > 0.12


def save_debug_chart(symbol, df, nifty_df=None, pivot=None):

    df = df.copy()
    df.index.name = "Date"

    # =========================
    # Indicators
    # =========================

    df["EMA50"] = df["close"].ewm(span=50).mean()
    df["EMA200"] = df["close"].ewm(span=200).mean()
    df["supertrend"] = supertrend(df, 7, 3)

    base_high = df["high"].iloc[-30:].max()
    base_low = df["low"].iloc[-30:].min()

    # =========================
    # Relative Strength
    # =========================

    if nifty_df is not None:

        nifty_df = nifty_df.reindex(df.index, method="ffill")
        df["RS"] = df["close"] / nifty_df["close"]

    else:
        df["RS"] = np.nan

    # =========================
    # Plot overlays
    # =========================
    bull = df["supertrend"].where(df["close"] > df["supertrend"])
    bear = df["supertrend"].where(df["close"] < df["supertrend"])

    addplots = [

        mpf.make_addplot(df["EMA50"], color="cyan", width=.75),
        mpf.make_addplot(df["EMA200"], color="orange", width=.75),
        mpf.make_addplot(bull, color="lime", width=1.2),
        mpf.make_addplot(bear, color="red", width=1.2),
        mpf.make_addplot([base_high]*len(df), color="yellow", width=0.75),
        mpf.make_addplot([base_low]*len(df), color="yellow", width=0.75)

    ]

    # Pivot line
    if pivot is not None:

        addplots.append(
            mpf.make_addplot([pivot]*len(df), color="red", width=0.75)
        )

    # =========================
    # Breakout marker
    # =========================

    if pivot and df["close"].iloc[-1] > pivot:

        breakout_series = [np.nan]*len(df)
        breakout_series[-1] = df["close"].iloc[-1]

        addplots.append(
            mpf.make_addplot(
                breakout_series,
                type="scatter",
                marker="^",
                color="lime",
                markersize=60
            )
        )

    # =========================
    # VCP Legs
    # =========================

    legs = detect_vcp_legs(df)
    score = compute_vcp_score(df, legs, pivot, nifty_df)
    vcp_plots = add_vcp_visuals(df, legs)
    addplots.extend(vcp_plots)

    # =========================
    # RS Panel
    # =========================

    addplots.append(
        mpf.make_addplot(
            df["RS"],
            panel=2,
            color="white",
            ylabel="RS vs NIFTY"
        )
    )

    # =========================
    # File name
    # =========================

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    filename = f"charts/{symbol}_{timestamp}.png"

    # =========================
    # Plot
    # =========================

    min_p = df["low"].min()
    max_p = df["high"].max()
    margin_p = (max_p - min_p) * 0.02
    
    ylim = (min_p - margin_p, max_p + margin_p)

    title=f"{symbol} | Pivot {pivot} | VCP Score {score}"
    import matplotlib as mpl
    mpl.rcParams['font.size'] = 8
    custom_style = mpf.make_mpf_style(base_mpf_style="nightclouds", gridcolor="#2a2a2a", gridstyle="--")
    mpf.plot(
        df,
        type="candle",
        style=custom_style,
        volume=True,
        addplot=addplots,
        panel_ratios=(8,1.4,1.4),   # main chart larger
        figsize=(10,6),         # compact size
        tight_layout=True,
        scale_padding=dict(left=0.3, right=0.3, top=0.8, bottom=0.5),
        title=f"{symbol} | Pivot {pivot} | Score {score}",
        datetime_format='%b %d',
        xrotation=15,
        ylim=ylim,
        savefig=filename
    )

    print(f"Chart saved -> {filename}")
# ===============================
# DATA
# ===============================

import threading
api_lock = threading.Lock()

def fetch_data(kite, token):

    with api_lock:
        time.sleep(0.5)  # global rate limit protection for Kite (max 3/sec)
        try:
            data = kite.historical_data(
                token,
                START_DATE,
                datetime.now(),
                "5minute"
            )
        except Exception as e:
            print(f"Error fetching data for token {token}: {e}")
            return None

    if not data:
        return None

    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])

    return df


def resample(df, tf):

    df = df.set_index("date")

    ohlc = df.resample(tf).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum"
    })

    return ohlc.dropna()


# ===============================
# INDICATORS
# ===============================

def ema(series, period):

    return series.ewm(span=period).mean()


def relative_strength(stock, index):

    rs = stock["close"] / index["close"]

    return rs.iloc[-1] > rs.iloc[-10]


# ===============================
# BASE DETECTION
# ===============================

def detect_base(df):

    window = 120

    base = df.iloc[-window:]

    high = base["high"].max()
    low = base["low"].min()

    depth = (high - low) / high

    if depth < 0.05 or depth > 0.35:
        return None

    last_close = base["close"].iloc[-1]

    near_high = last_close > high * 0.85

    if not near_high:
        return None

    pivot = high

    return {
        "pivot": pivot,
        "depth": depth
    }


# ===============================
# VCP TIGHTENING
# ===============================

def vcp_tightening(df):

    df = df.copy()

    df["range"] = df["high"] - df["low"]

    r1 = df["range"].iloc[-5:].mean()
    r2 = df["range"].iloc[-10:-5].mean()
    r3 = df["range"].iloc[-20:-10].mean()

    contractions = 0

    if r1 < r2:
        contractions += 1

    if r2 < r3:
        contractions += 1

    return contractions

def detect_vcp_contractions(df):

    highs = df["high"].values
    lows = df["low"].values

    pivot = max(highs[-40:])

    pullbacks = []

    for i in range(len(df)-40, len(df)-5):

        if highs[i] > pivot * 0.97:

            local_low = min(lows[i:i+5])

            depth = (highs[i] - local_low) / highs[i]

            pullbacks.append(depth)

    if len(pullbacks) < 2:
        return 0

    # check contraction sequence
    contractions = 0

    for i in range(len(pullbacks)-1):

        if pullbacks[i+1] < pullbacks[i]:
            contractions += 1

    return contractions

def detect_vcp_legs(df, lookback=40, swing_window=4):

    highs = df["high"].values
    lows = df["low"].values

    legs = []

    start = max(0, len(df) - lookback)

    for i in range(start + swing_window, len(df) - swing_window):

        # Detect swing high
        if highs[i] == max(highs[i-swing_window:i+swing_window+1]):

            peak = highs[i]

            # Find pullback low after the peak
            future_range = lows[i:i+6]

            if len(future_range) == 0:
                continue

            low = min(future_range)

            pullback = (peak - low) / peak * 100

            legs.append((i, peak, low, pullback))

    # Keep only last few legs
    legs = legs[-4:]

    return legs

def compute_vcp_score(df, legs, pivot, nifty_df=None):

    score = 0

    # -----------------------------
    # Contractions
    # -----------------------------

    contractions = len(legs)

    score += min(contractions,3) * 10   # max 30

    # -----------------------------
    # Pullback shrinking
    # -----------------------------

    pullbacks = [leg[3] for leg in legs]

    shrinking = 0

    for i in range(len(pullbacks)-1):

        if pullbacks[i+1] < pullbacks[i]:
            shrinking += 1

    score += shrinking * 12.5  # max 25

    # -----------------------------
    # Tightness near pivot
    # -----------------------------

    recent_range = df["high"].iloc[-5:].max() - df["low"].iloc[-5:].min()
    base_range = df["high"].iloc[-30:].max() - df["low"].iloc[-30:].min()

    tightness = 1 - (recent_range / base_range)

    score += max(0, tightness) * 15

    # -----------------------------
    # Distance from pivot
    # -----------------------------

    price = df["close"].iloc[-1]

    distance = abs(pivot - price) / pivot

    score += max(0, (1 - distance*5)) * 15

    # -----------------------------
    # Relative strength
    # -----------------------------

    if nifty_df is not None:

        rs = df["close"] / nifty_df["close"]

        if rs.iloc[-1] > rs.iloc[-20]:

            score += 15

    return round(score,2)


def supertrend(df, period=7, multiplier=3):

    df = df.copy()

    hl2 = (df["high"] + df["low"]) / 2

    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift()),
            abs(df["low"] - df["close"].shift())
        )
    )

    df["atr"] = df["tr"].rolling(period).mean()

    upperband = hl2 + multiplier * df["atr"]
    lowerband = hl2 - multiplier * df["atr"]

    supertrend = [True] * len(df)

    for i in range(1, len(df)):

        if df["close"].iloc[i] > upperband.iloc[i-1]:
            supertrend[i] = True

        elif df["close"].iloc[i] < lowerband.iloc[i-1]:
            supertrend[i] = False

        else:
            supertrend[i] = supertrend[i-1]

            if supertrend[i] and lowerband.iloc[i] < lowerband.iloc[i-1]:
                lowerband.iloc[i] = lowerband.iloc[i-1]

            if not supertrend[i] and upperband.iloc[i] > upperband.iloc[i-1]:
                upperband.iloc[i] = upperband.iloc[i-1]

    df["supertrend"] = np.where(supertrend, lowerband, upperband)

    return df["supertrend"]

# DISTANCE FROM PIVOT
# ===============================

def distance_from_pivot(price, pivot):

    return (pivot - price) / pivot * 100


# ===============================
# RANKING
# ===============================

def score_stock(df, contractions):

    momentum = df["close"].iloc[-1] / df["close"].iloc[-20]
    tightness = df["high"].iloc[-20:].max() - df["low"].iloc[-20:].min()
    score = momentum * 100 - tightness + contractions * 5
    return score


# ===============================
# LOAD UNIVERSE
# ===============================

def load_universe():

    # NIFTY300 file (symbols on each line)
    with open("data/nifty300.csv", "r", encoding="utf-8") as f:
    #with open("data/test.csv", "r", encoding="utf-8") as f:
        symbols = [line.split(",")[0].strip() for line in f if line.strip()]
    instruments = pd.read_csv("data/instruments_cache.csv")
    instruments = instruments[instruments["exchange"] == "NSE"]
    universe = instruments[instruments["tradingsymbol"].isin(symbols)]
    universe = universe[["tradingsymbol", "instrument_token"]]
    universe.columns = ["symbol", "token"]

    return universe


def volume_dry_up(df):

    recent = df["volume"].iloc[-10:].median()
    base = df["volume"].iloc[-40:-10].median()

    return recent < base * 0.85

def rs_near_high(stock, index):

    rs = stock["close"] / index["close"]
    rs_high = rs.iloc[-200:].max()
    return rs.iloc[-1] > rs_high * 0.90

# ===============================
# ANALYZE STOCK
# ===============================

def tight_closes(df, lookback=5, threshold=0.04):

    high = df["high"].iloc[-lookback:].max()
    low = df["low"].iloc[-lookback:].min()

    return (high - low) / low <= threshold


def pivot_pressure(df, pivot):

    closes = df["close"].iloc[-5:]
    lows = df["low"].iloc[-5:]

    # closes not breaking down heavily
    upward_closes = closes.iloc[-1] >= closes.iloc[-3] * 0.99
    
    # lows are generally supportive
    higher_lows = lows.iloc[-1] >= lows.iloc[-3] * 0.99

    # price reasonably close to pivot
    near_pivot = closes.iloc[-1] > pivot * 0.94

    # Require 2 out of 3 conditions to pass instead of all 3
    conditions = [upward_closes, higher_lows, near_pivot]
    return sum(conditions) >= 2

def analyze_stock(idx, total, kite, symbol, token, nifty_75, existing_symbols):

    passes = []
    fail_reason = ""
    try:

        df = fetch_data(kite, token)
        if df is None:
            fail_reason = "API/Data Fetch Failed"
            return None

        df75 = resample(df, "75min")
        df15 = resample(df, "15min")

        if len(df75) < 40:
            fail_reason = "Not Enough Data (< 40 candles)"
            return None

        df75["ema50"] = ema(df75["close"], 50)
        df75["ema200"] = ema(df75["close"], 200)
        df75["ema150"] = ema(df75["close"], 150)

        df75["turnover"] = df75["close"] * df75["volume"]
        avg_turnover = df75["turnover"].iloc[-20:].mean()

        price = df75["close"].iloc[-1]

        trend = (
            price > df75["ema50"].iloc[-1] and
            price > df75["ema150"].iloc[-1] and
            price > df75["ema200"].iloc[-1] and
            df75["ema50"].iloc[-1] > df75["ema150"].iloc[-1] and
            df75["ema150"].iloc[-1] > df75["ema200"].iloc[-1]
        )

        if not trend:
            fail_reason = "❌First Check Failed Trend "
            return None
        passes.append("Trend✔")

        high_60 = df75["high"].iloc[-200:].max()

        if price < high_60 * 0.75:
            return None
        passes.append("Nr High✔")

        if not prior_uptrend(df75):
            return None
        passes.append("Pr Uptrend✔")

        if not rs_near_high(df75, nifty_75):
            return None
        passes.append("Rltve Str✔")

        base = detect_base(df75)
        if not base:
            return None
        passes.append("Base ✔")

        if not volume_dry_up(df75):
            return None
        passes.append("Vol Dry Up✔")

        recent_range = df75["high"].iloc[-5:].max() - df75["low"].iloc[-5:].min()
        base_range = df75["high"].iloc[-30:].max() - df75["low"].iloc[-30:].min()

        tightness = recent_range / base_range

        if tightness > 0.8:
            return None
        passes.append("Tightness ✔")

        if not tight_closes(df75):
            return None
        passes.append("Tight Closes ✔")

        price = df75["close"].iloc[-1]
        pivot = base["pivot"]

        distance = (pivot - price) / pivot * 100

        if distance > 5:
            return None
        passes.append("Distance ✔")

        if avg_turnover < MIN_TURNOVER:
            return None
        passes.append("Turnover ✔")        

        if not pivot_pressure(df75, pivot):
            return None
        passes.append("Pvt Prsure ✔")

        contractions = detect_vcp_contractions(df75)               

        df15["turnover"] = df15["close"] * df15["volume"]
        recent = df15["turnover"].iloc[-3:].mean()
        avg = df15["turnover"].iloc[-20:].mean()

        volume_expansion = recent > avg * 0.9

        if not volume_expansion:
            return None
        passes.append("Vol Expansion ✔")

        # momentum = df75["close"].iloc[-1] / df75["close"].iloc[-20]
        
        legs = detect_vcp_legs(df75)
        score = compute_vcp_score(df75, legs, pivot, nifty_75)
        '''
        breakout = df15["close"].iloc[-1] > pivot * 1.002

        if not breakout:
            return None
        passes.append("Breakout ✔")
        '''
        if symbol not in existing_symbols:
            save_debug_chart(symbol, df75, nifty_75, pivot)
            # save_chart(symbol, df75, pivot)
        return (symbol, score, pivot, distance, contractions)

    except Exception as e:
        import traceback
        print(f"Error in {symbol}:")
        traceback.print_exc()
        fail_reason = f"Exception: {e}"
        return None
        
    finally:
        if fail_reason == "" and passes:
            print(f"[{idx}/{total}] {symbol} {', '.join(passes)}")
        else:
            checks = ", ".join(passes) + " | " if passes else ""
            reason_str = fail_reason if fail_reason else ""
            print(f"[{idx}/{total}] {symbol} {checks}{reason_str}".strip())


# ===============================
# MAIN SCANNER
# ===============================

def run_scanner():

    print("Running VCP Base Scanner")

    kite = get_kite_client(API_KEY, API_SECRET)

    universe = load_universe()

    nifty_df = fetch_data(kite, NIFTY_TOKEN)
    nifty_75 = resample(nifty_df, "75min")

    os.makedirs("output", exist_ok=True)
    date_str = datetime.now().strftime("%Y%b%d").lower()
    filename = f"output/{date_str}.txt"
    
    existing_symbols = set()
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                if " , Pivot " in line:
                    existing_symbols.add(line.split(" , ")[0].strip())

    results = []

    with ThreadPoolExecutor(max_workers=2) as executor:

        futures = []

        total = len(universe)
        for idx, (_, row) in enumerate(universe.iterrows(), 1):

            futures.append(

                executor.submit(
                    analyze_stock,
                    idx,
                    total,
                    kite,
                    row["symbol"],
                    row["token"],
                    nifty_75,
                    existing_symbols
                )

            )

        for future in as_completed(futures):

            res = future.result()

            if res:
                results.append(res)

    results = sorted(results, key=lambda x: x[1], reverse=True)

    top = results[:TOP_RESULTS]

    if not top:

        print("No setups found")

        return

    new_stocks = [s for s in top if s[0] not in existing_symbols]

    if not new_stocks:
        print("No new setups found")
        return

    message = "📊 VCP BASE SCANNER\n\n"
    lines_to_save = []

    for stock in new_stocks:

        symbol = stock[0]
        pivot = round(stock[2], 2)
        score = round(stock[1], 2)
        distance = round(stock[3], 2)
        contractions = stock[4]

        line_str = f"{symbol} , Pivot {pivot} , {distance}% away , VCP {contractions} , Score {score}\n"
        message += line_str
        lines_to_save.append(line_str)

    print(message)

    send_telegram(message)

    write_header = not os.path.exists(filename)
    with open(filename, "a", encoding="utf-8") as f:
        if write_header:
            f.write("📊 VCP BASE SCANNER\n\n")
        f.writelines(lines_to_save)

    print(f"Results saved to {filename}")


# ===============================
# RUN
# ===============================

def start_scheduler():
    print("Scheduler started. Scanner will run every 15 mins between 10:31 and 15:16.")
    while True:
        now = datetime.now()
        
        # Check if we are in the trading window (10:30 to 15:16)
        # Note: We use 10:30 instead of 10:31 as the floor so 10:31 triggers correctly
        if (now.hour == 10 and now.minute >= 30) or (11 <= now.hour <= 14) or (now.hour == 15 and now.minute <= 16):
            
            # Run at the 1st, 16th, 31st, and 46th minute of each hour
            if now.minute in [1, 16, 31, 46]:
                print(f"--- Triggering Scanner at {now.strftime('%H:%M')} ---")
                run_scanner()
                # Sleep for 60 seconds to avoid running multiple times in the same minute
                time.sleep(60)
        
        # Check every 10 seconds
        time.sleep(10)

if __name__ == "__main__":

    start_scheduler()