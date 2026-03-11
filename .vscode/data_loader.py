import pandas as pd
from datetime import datetime
from config import START_DATE


def fetch_data(kite, token):

    data = kite.historical_data(
        token,
        START_DATE,
        datetime.now(),
        "minute"
    )

    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])

    return df