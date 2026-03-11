def score_stock(df):

    momentum = df["close"].iloc[-1] / df["close"].iloc[-20]

    tightness = (df["high"].iloc[-20:].max() - df["low"].iloc[-20:].min())

    score = momentum * 100 - tightness

    return score