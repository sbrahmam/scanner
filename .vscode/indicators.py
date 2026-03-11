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