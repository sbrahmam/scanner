import pandas as pd
import numpy as np
import requests
import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import matplotlib as mpl
mpl.use('Agg')
import mplfinance as mpf
from kite_auth import get_kite_client
import traceback

# ===============================
# LOAD CONFIG & ENV
# ===============================
load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Minervini SEPA requires at least 200+ days of data for the Trend Template
START_DATE = datetime.now() - timedelta(days=365) 
TOP_RESULTS = 15
NIFTY_TOKEN = 256265 # Nifty 50 for Relative Strength

os.makedirs("charts", exist_ok=True)
os.makedirs("output", exist_ok=True)

# ===============================
# TREND TEMPLATE & SEPA LOGIC
# ===============================
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


def check_trend_template(df):
    """
    Mark Minervini's 8 Key Trend Template Requirements
    """
    if len(df) < 200:
        return False, "Insufficient Data"

    close = df["close"].iloc[-1]
    
    # Moving Averages
    sma50 = df["close"].rolling(window=50).mean()
    sma150 = df["close"].rolling(window=150).mean()
    sma200 = df["close"].rolling(window=200).mean()
    
    s50, s150, s200 = sma50.iloc[-1], sma150.iloc[-1], sma200.iloc[-1]

    # 1. Price is above SMA 150 and SMA 200
    cond_1 = close > s150 and close > s200
    
    # 2. 150 SMA is above the 200 SMA
    cond_2 = s150 > s200
    
    # 3. 200 SMA is trending up for at least 1 month
    # (Check if current 200 SMA is higher than it was 22 trading days ago)
    cond_3 = s200 > sma200.iloc[-22]
    
    # 4. 50 SMA is above both 150 and 200 SMAs
    cond_4 = s50 > s150 and s50 > s200
    
    # 5. Price is above the 50 SMA
    cond_5 = close > s50
    
    # 6. Price is at least 30% above 52-week low
    low_52wk = df["low"].iloc[-252:].min()
    cond_6 = close >= (low_52wk * 1.30)
    
    # 7. Price is within 25% of 52-week high
    high_52wk = df["high"].iloc[-252:].max()
    cond_7 = close >= (high_52wk * 0.75)

    if all([cond_1, cond_2, cond_3, cond_4, cond_5, cond_6, cond_7]):
        return True, "Stage 2 Confirmed"
    return False, "Failed Trend Template"

def detect_vcp_structure(df):
    """
    Analyzes volatility contraction (VCP) over the last 3-6 months
    """
    recent_high = df["high"].iloc[-60:].max()
    recent_low = df["low"].iloc[-60:].min()
    
    # Minervini bases are usually 10% to 35% deep
    depth = (recent_high - recent_low) / recent_high
    if depth < 0.08 or depth > 0.40:
        return None
    
    return {"pivot": recent_high, "depth": depth}

def volume_analysis(df):
    """
    Checks for Volume Dry-up (VDU) followed by Breakout Volume
    """
    current_vol = df["volume"].iloc[-1]
    avg_vol_50 = df["volume"].rolling(50).mean().iloc[-1]
    
    # Volume Dry-up: Volume should be low in the tightest part of the base
    vdu = df["volume"].iloc[-5:-1].median() < avg_vol_50
    
    # Breakout Volume: 50% above average
    vol_spike = current_vol > (avg_vol_50 * 1.5)
    
    return vdu, vol_spike

# ===============================
# DATA & EXECUTION
# ===============================

import threading
api_lock = threading.Lock()

def fetch_data(kite, token):
    with api_lock:
        time.sleep(0.35)
        try:
            data = kite.historical_data(token, START_DATE, datetime.now(), "day")
            if not data: return None
            return pd.DataFrame(data)
        except Exception as e:
            print(f"Error: {e}")
            return None

def analyze_stock(idx, total, kite, symbol, token, nifty_df, existing_symbols):
    fail_reason = ""
    status = ""
    try:
        df = fetch_data(kite, token)
        if df is None:
            fail_reason = "No Data / API Error"
            return None
            
        if len(df) < 200:
            fail_reason = f"Not Enough Data ({len(df)} < 200)"
            return None

        # 1. SEPA Trend Template
        is_stage_2, reason = check_trend_template(df)
        if not is_stage_2:
            fail_reason = f"Trend Failed: {reason}"
            return None

        # 2. VCP Base Structure
        base = detect_vcp_structure(df)
        if not base:
            fail_reason = "No VCP Structure"
            return None
        
        # 3. Volume Characteristics
        vdu, vol_spike = volume_analysis(df)
        
        price = df["close"].iloc[-1]
        pivot = base["pivot"]
        dist = (pivot - price) / pivot * 100
        
        # Minervini Entry: At or slightly above the pivot with high volume
        is_breakout = (price >= pivot * 0.99) and vol_spike
        is_setup = (dist < 3.5) and vdu # Tightening but hasn't broken yet

        if is_breakout or is_setup:
            status = "🚀 BREAKOUT" if is_breakout else "⌛ SETUP"
            if symbol not in existing_symbols:
                chart_path = save_sepa_chart(symbol, df, pivot, status)
                print(f"[{idx}/{total}] {symbol} - {status} - Chart saved.")
            else:
                print(f"[{idx}/{total}] {symbol} - {status} - Already logged.")
                
            return (symbol, status, pivot, dist, price)
        
        fail_reason = "No Breakout/Setup Volume"
        return None

    except Exception as e:
        traceback.print_exc()
        fail_reason = f"Exception: {e}"
        return None
        
    finally:
        if status != "":
            print(f"[{idx}/{total}] {symbol} Found: {status}")
        else:
            print(f"[{idx}/{total}] {symbol} Skipped: {fail_reason}".strip())

# ===============================
# VISUALIZATION (SEPA STYLE)
# ===============================

def save_sepa_chart(symbol, df, pivot, status):
    # Slice the dataframe to 150 days for plotting
    plot_df = df.tail(150).copy()
    plot_df.index = pd.to_datetime(plot_df["date"])
    plot_df.index.name = "Date"

    # Indicators for the Trend Template
    # We calculate on full DF but slice for the chart to match dimensions
    full_sma50 = df["close"].rolling(window=50).mean()
    full_sma150 = df["close"].rolling(window=150).mean()
    full_sma200 = df["close"].rolling(window=200).mean()

    # Slicing indicators to match the 150-day plot window
    sma50 = full_sma50.tail(150)
    sma150 = full_sma150.tail(150)
    sma200 = full_sma200.tail(150)
    ST = supertrend(plot_df, 7, 3)
    # Base visual reference lines
    base_high = [pivot] * 150
    base_low = [df["low"].iloc[-60:].min()] * 150

    ST = ST.tail(150)
    bull = ST.where(plot_df["close"] > ST)
    bear = ST.where(plot_df["close"] < ST)
    
    addplots = [
        mpf.make_addplot(sma50, color="black", width=1),
        mpf.make_addplot(sma150, color="blue", width=1.2),
        mpf.make_addplot(sma200, color="magenta", width=1.5),
        mpf.make_addplot(base_high, color="brown", width=1.2, linestyle="--"),
        mpf.make_addplot(base_low, color="brown", width=1.2, linestyle="--")
    ]
    
    if bull.notna().any():
        addplots.append(mpf.make_addplot(bull, color="green", width=.8))
    if bear.notna().any():
        addplots.append(mpf.make_addplot(bear, color="red", width=.8))

    # Breakout marker logic
    if "BREAKOUT" in status:
        marker_data = [np.nan] * 150
        marker_data[-1] = plot_df["low"].iloc[-1]*0.998
        addplots.append(mpf.make_addplot(marker_data, type="scatter", marker="^", markersize=40, color="lime"))

    filename = f"charts/{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    
    min_p = plot_df["low"].min()
    max_p = plot_df["high"].max()
    margin_p = (max_p - min_p) * 0.02
    
    ylim = (min_p - margin_p, max_p + margin_p)

    import matplotlib as mpl
    mpl.rcParams['font.size'] = 6
    mpl.rcParams['grid.color'] = '#101010'
    mpl.rcParams['grid.linestyle'] = '--'
    
    marketcolors = mpf.make_marketcolors(up='g', down='r',
                           edge='inherit',
                           wick='inherit',
                           volume='inherit')
    
    custom_style = mpf.make_mpf_style(
        base_mpf_style="yahoo", 
        marketcolors=marketcolors,
        facecolor="#b0b0b0", 
        figcolor="#b0b0b0",
        edgecolor="black"
    )

    future_date = plot_df.index[-1] + pd.Timedelta(days=6)

    fig, axes = mpf.plot(
        plot_df,
        type="candle",
        style=custom_style,
        volume=True,
        addplot=addplots,
        panel_ratios=(8,1.5),   # main chart larger
        figsize=(10,6),         # compact size
        tight_layout=True,
        scale_padding=dict(left=0.3, right=0.8, top=0.8, bottom=0.5),
        title=f"{symbol}",
        datetime_format='%b %d',
        xrotation=15,
        ylim=ylim,
        xlim=(0, len(plot_df) + 6),  # add extra space after last candle
        returnfig=True
    )
    
    # Add legend for indicators with correct colors
    ax = axes[0]
    from matplotlib.lines import Line2D
    
    legend_elements = [
        Line2D([0], [0], color='black', lw=1, label='SMA50'),
        Line2D([0], [0], color='blue', lw=1.2, label='SMA150'),
        Line2D([0], [0], color='magenta', lw=1.5, label='SMA200'),
        Line2D([0], [0], color='brown', lw=1.2, linestyle='--', label='Base Level'),
        Line2D([0], [0], color='green', lw=0.8, label='SuperTrend Up'),
        Line2D([0], [0], color='red', lw=0.8, label='SuperTrend Down'),
    ]
    
    if "BREAKOUT" in status:
        legend_elements.append(Line2D([0], [0], marker='^', color='w', markerfacecolor='lime', markersize=8, label='Breakout Signal'))
    
    ax.legend(handles=legend_elements, loc='upper left', framealpha=0.9, fontsize='xx-small')
    fig.savefig(filename)
    return filename


# ===============================
# TELEGRAM & MAIN
# ===============================

def send_telegram(msg):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except: pass

def run_sepa_scanner():
    print(f"Starting Minervini SEPA Scanner (Daily TF) - {datetime.now()}")
    kite = get_kite_client(API_KEY, API_SECRET)
    
    # Load universe (Ensure you have data/nifty300.csv)
    instruments = pd.read_csv("data/instruments_cache.csv")
    #with open("data/nifty300.csv", "r") as f:
    with open("data/test.csv", "r") as f:
        symbols = [line.split(",")[0].strip() for line in f if line.strip()]
    
    universe = instruments[(instruments["exchange"] == "NSE") & (instruments["tradingsymbol"].isin(symbols))]
    nifty_df = fetch_data(kite, NIFTY_TOKEN)

    # Determine filename
    os.makedirs("output", exist_ok=True)
    date_str = datetime.now().strftime("%Y%b%d").lower()
    filename = f"output/sepa_{date_str}.txt"
    
    existing_symbols = set()
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                if "Price:" in line or "Dist:" in line: continue
                if ":" in line:
                    # Extract symbol from "🚀 BREAKOUT: SYMBOL"
                    existing_symbols.add(line.split(":")[1].strip())

    results = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = [executor.submit(analyze_stock, i, len(universe), kite, row["tradingsymbol"], row["instrument_token"], nifty_df, existing_symbols) 
                   for i, row in enumerate(universe.to_dict('records'), 1)]
        for f in as_completed(futures):
            res = f.result()
            if res: results.append(res)

    if results:
        new_results = [r for r in results if r[0] not in existing_symbols]
        
        if not new_results:
            print("No new SEPA setups found.")
            return

        msg = "🎯 MINERVINI SEPA ALERTS (Daily)\n\n"
        for sym, stat, pvt, dist, price in new_results:
            msg += f"{stat}: {sym}\nPrice: {price} (Pivot: {pvt})\nDist: {dist:.2f}%\n---\n"
            
        print(msg)
        send_telegram(msg)
        
        # Append to log
        write_header = not os.path.exists(filename)
        with open(filename, "a", encoding="utf-8") as f:
            if write_header:
                f.write("🎯 MINERVINI SEPA ALERTS (Daily)\n\n")
            
            for sym, stat, pvt, dist, price in new_results:
                f.write(f"{stat}: {sym}\nPrice: {price} (Pivot: {pvt})\nDist: {dist:.2f}%\n---\n")
                
        print(f"Results saved to {filename}")

    else:
        print("No SEPA setups found today.")

if __name__ == "__main__":
    # Since this is Daily TF, you only need to run this once after market close or near EOD
    run_sepa_scanner()