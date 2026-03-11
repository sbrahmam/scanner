def breakout(df):

    high20 = df["high"].rolling(20).max().iloc[-2]

    price_break = df["close"].iloc[-1] > high20

    vol_avg = df["volume"].rolling(20).mean().iloc[-1]

    volume_expansion = df["volume"].iloc[-1] > vol_avg * 1.5

    return price_break and volume_expansion