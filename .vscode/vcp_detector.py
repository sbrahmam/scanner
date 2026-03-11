import pandas as pd


def detect_vcp(df):

    df["range"] = df["high"] - df["low"]

    contraction1 = df["range"].iloc[-5:].mean()
    contraction2 = df["range"].iloc[-10:-5].mean()
    contraction3 = df["range"].iloc[-15:-10].mean()

    volatility_contracting = (
        contraction1 < contraction2
        and contraction2 < contraction3
    )

    near_high = df["close"].iloc[-1] > df["high"].rolling(30).max().iloc[-1] * 0.9

    return volatility_contracting and near_high