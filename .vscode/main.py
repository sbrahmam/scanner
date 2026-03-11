import pandas as pd

from kite_auth import get_kite_client
from config import *

from data_loader import fetch_data
from indicators import resample
from vcp_detector import detect_vcp
from breakout import breakout
from ranking import score_stock
from telegram_alert import send_alert


kite = get_kite_client(API_KEY, API_SECRET)

symbols = pd.read_csv("data/nifty300.csv")

results = []

for _, row in symbols.iterrows():

    symbol = row["symbol"]
    token = row["token"]

    try:

        df = fetch_data(kite, token)

        df75 = resample(df, "75min")
        df15 = resample(df, "15min")

        if detect_vcp(df75):

            if breakout(df15):

                score = score_stock(df75)

                results.append((symbol, score))

    except:
        continue


results = sorted(results, key=lambda x: x[1], reverse=True)

top = results[:TOP_RESULTS]


message = "🔥 VCP BREAKOUT SCANNER\n\n"

for s in top:
    message += f"{s[0]}\n"

print(message)

send_alert(message)