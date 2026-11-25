#!/usr/bin/env python3
"""미국 주식 종가 수집.

tickers.txt의 종목 시세를 받아 data/에 저장한다. 시세만 저장하고 계좌·수량 정보는 없다.
- data/latest.csv        종목별 최근 종가·전일 대비·50/200일선·수익률·52주 고가
- data/closes_recent.csv 최근 30거래일 종가 (날짜 x 종목)
- data/meta.json         갱신 시각·기준 거래일·빠진 종목
1차 출처는 Yahoo Finance(yfinance), 비거나 오래된 종목은 Stooq로 다시 시도한다.
"""
import json
import sys
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ET = ZoneInfo("America/New_York")

# Stooq 기호가 Yahoo와 다른 것
STOOQ_MAP = {"^GSPC": "^spx", "^NDX": "^ndx", "KRW=X": "usdkrw"}
# 기준일(SPY 날짜)과 날짜가 달라도 되는 것 (24시간 거래)
NO_STALE_CHECK = {"KRW=X"}

COLUMNS = [
    "ticker", "date", "final", "close", "prev_date", "prev_close", "chg_pct",
    "sma50", "sma200", "vs_sma200_pct", "ret_1m_pct", "ret_3m_pct",
    "ret_12m_pct", "high_52w", "source",
]


def read_tickers():
    out = []
    for line in (ROOT / "tickers.txt").read_text(encoding="utf-8").splitlines():
        t = line.split("#", 1)[0].strip().upper()
        if t and t not in out:
            out.append(t)
    return out


def norm(s):
    """날짜 인덱스를 시간대 없는 날짜로 맞추고 빈 값·중복을 뺀다."""
    idx = pd.to_datetime(s.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    s = pd.Series(pd.to_numeric(s.values, errors="coerce"), index=idx.normalize())
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.dropna()


def fetch_yahoo(tickers):
    import yfinance as yf

    res = {}
    try:
        data = yf.download(
            tickers, period="2y", interval="1d", auto_adjust=False,
            group_by="ticker", threads=True, progress=False,
        )
    except Exception as e:  # 네트워크·차단 등
        print("yahoo download failed:", e, file=sys.stderr)
        return res
    if data is None or data.empty:
        return res
    for t in tickers:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if t not in data.columns.get_level_values(0):
                    continue
                s = data[t]["Close"]
            else:
                s = data["Close"]
            s = norm(s)
            if len(s):
                res[t] = s
        except Exception as e:
            print("yahoo parse failed:", t, e, file=sys.stderr)
    return res


def fetch_stooq(t):
    import requests

    sym = STOOQ_MAP.get(t, t.lower() + ".us")
    r = requests.get(
        f"https://stooq.com/q/d/l/?s={sym}&i=d",
        timeout=20, headers={"User-Agent": "Mozilla/5.0"},
    )
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    if "Close" not in df.columns or df.empty:
        raise ValueError(r.text[:80])
    s = pd.Series(df["Close"].values, index=pd.to_datetime(df["Date"]))
    return norm(s).iloc[-520:]


def pct(a, b):
    if a is None or not b:
        return None
    return round((a / b - 1) * 100, 2)


def summarize(s, now_et):
    close = float(s.iloc[-1])
    last_date = s.index[-1].date()
    row = {"date": last_date.isoformat(), "close": round(close, 4)}
    if len(s) >= 2:
        prev = float(s.iloc[-2])
        row.update(prev_date=s.index[-2].date().isoformat(),
                   prev_close=round(prev, 4), chg_pct=pct(close, prev))

    def sma(n):
        return round(float(s.iloc[-n:].mean()), 4) if len(s) >= n else None

    def ret(n):
        return pct(close, float(s.iloc[-1 - n])) if len(s) > n else None

    row["sma50"] = sma(50)
    row["sma200"] = sma(200)
    row["vs_sma200_pct"] = pct(close, row["sma200"])
    row["ret_1m_pct"] = ret(21)
    row["ret_3m_pct"] = ret(63)
    row["ret_12m_pct"] = ret(252)
    row["high_52w"] = round(float(s.iloc[-252:].max()), 4)
    # 오늘(미국 날짜) 장 마감 전이면 장중 값
    partial = last_date == now_et.date() and (now_et.hour, now_et.minute) < (16, 5)
    row["final"] = 0 if partial else 1
    return row


def main(fetch_yahoo=fetch_yahoo, fetch_stooq=fetch_stooq, now_et=None):
    DATA.mkdir(exist_ok=True)
    tickers = read_tickers()
    now_et = now_et or datetime.now(ET)

    series = fetch_yahoo(tickers)
    source = {t: "yahoo" for t in series}
    ref = series.get("SPY")
    ref_date = ref.index[-1] if ref is not None and len(ref) else None

    for t in tickers:
        s = series.get(t)
        stale = (
            s is None or len(s) < 2
            or (ref_date is not None and t not in NO_STALE_CHECK and s.index[-1] < ref_date)
        )
        if not stale:
            continue
        try:
            s2 = fetch_stooq(t)
            if len(s2) and (s is None or s2.index[-1] >= s.index[-1]):
                series[t], source[t] = s2, "stooq"
        except Exception as e:
            print("stooq failed:", t, e, file=sys.stderr)
        time.sleep(0.5)

    rows, missing = [], []
    for t in tickers:
        s = series.get(t)
        if s is None or not len(s):
            missing.append(t)
            continue
        rows.append({"ticker": t, **summarize(s, now_et), "source": source[t]})

    if not rows:
        print("no data at all — keeping previous files", file=sys.stderr)
        sys.exit(1)

    pd.DataFrame(rows).reindex(columns=COLUMNS).to_csv(DATA / "latest.csv", index=False)

    recent = pd.DataFrame({t: series[t] for t in tickers if t in series})
    recent = recent.sort_index().tail(30).round(4)
    recent.index = [d.date().isoformat() for d in recent.index]
    recent.index.name = "date"
    recent.to_csv(DATA / "closes_recent.csv")

    spy = next((r for r in rows if r["ticker"] == "SPY"), None)
    meta = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market_date": spy["date"] if spy else max(r["date"] for r in rows),
        "market_final": spy["final"] if spy else None,
        "tickers": len(tickers),
        "ok": len(rows),
        "missing": missing,
    }
    (DATA / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
