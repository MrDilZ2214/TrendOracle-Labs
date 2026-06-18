#!/usr/bin/env python3
"""
agents/crypto_technical_analysis.py
Advanced Crypto Technical Analysis System
"""

import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in [ROOT_DIR,
           os.path.join(ROOT_DIR, "agents"),
           os.path.join(ROOT_DIR, "tools"),
           os.path.join(ROOT_DIR, "core")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime


class C:
    R   = '\033[0m'
    BLD = '\033[1m'
    RED = '\033[91m'
    GRN = '\033[92m'
    YLW = '\033[93m'
    BLU = '\033[94m'
    MGN = '\033[95m'
    CYN = '\033[96m'
    WHT = '\033[97m'
    GRY = '\033[90m'


SYMBOLS  = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT']
INTERVAL = '1h'
LIMIT    = 300
BASE_URL = 'https://api.bitget.com/api/v2/spot/market'

_INTERVAL_MAP = {
    "1m": "1min",  "3m": "5min",  "5m": "5min",
    "15m": "15min", "30m": "30min",
    "1h": "1h",    "2h": "1h",    "4h": "4h",
    "6h": "6h",    "12h": "12h",
    "1d": "1day",  "3d": "3day",  "1w": "1week", "1M": "1M",
}


def fetch_ohlcv(symbol: str, interval: str = INTERVAL, limit: int = LIMIT) -> pd.DataFrame | None:
    granularity = _INTERVAL_MAP.get(interval, interval)
    try:
        r = requests.get(
            f'{BASE_URL}/candles',
            params={'symbol': symbol, 'granularity': granularity, 'limit': min(limit, 1000)},
            timeout=10
        )
        r.raise_for_status()
        raw = r.json()['data']
        df = pd.DataFrame(raw, columns=[
            'ts', 'open', 'high', 'low', 'close', 'volume',
            'quoteVolume', 'usdtVolume'
        ])
        df['time'] = pd.to_datetime(df['ts'].astype('int64'), unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df.reset_index(drop=True)
    except Exception as e:
        print(f"{C.RED}[ERROR] {symbol}: {e}{C.R}")
        return None


def fetch_price(symbol: str) -> float | None:
    try:
        r = requests.get(f'{BASE_URL}/tickers', params={'symbol': symbol}, timeout=5)
        return float(r.json()['data'][0]['lastPr'])
    except:
        return None


class Indicators:

    @staticmethod
    def ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def sma(series: pd.Series, window: int) -> pd.Series:
        return series.rolling(window).mean()

    @staticmethod
    def rsi(close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain  = delta.where(delta > 0, 0.0).ewm(com=period - 1, adjust=False).mean()
        loss  = (-delta.where(delta < 0, 0.0)).ewm(com=period - 1, adjust=False).mean()
        rs    = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def macd(close: pd.Series, fast=12, slow=26, signal=9):
        ema_fast    = close.ewm(span=fast, adjust=False).mean()
        ema_slow    = close.ewm(span=slow, adjust=False).mean()
        macd_line   = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram   = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(close: pd.Series, window=20, std_dev=2.0):
        mid   = close.rolling(window).mean()
        std   = close.rolling(window).std()
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        width = (upper - lower) / mid * 100
        pct_b = (close - lower) / (upper - lower)
        return upper, mid, lower, width, pct_b

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        return tr.ewm(com=period - 1, adjust=False).mean()

    @staticmethod
    def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k=14, d=3):
        lowest  = low.rolling(k).min()
        highest = high.rolling(k).max()
        pct_k   = 100 * (close - lowest) / (highest - lowest + 1e-9)
        pct_d   = pct_k.rolling(d).mean()
        return pct_k, pct_d

    @staticmethod
    def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
        hh = high.rolling(period).max()
        ll  = low.rolling(period).min()
        return -100 * (hh - close) / (hh - ll + 1e-9)

    @staticmethod
    def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        direction = np.sign(close.diff().fillna(0))
        return (direction * volume).cumsum()

    @staticmethod
    def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
        typical = (high + low + close) / 3
        return (typical * volume).cumsum() / volume.cumsum()

    @staticmethod
    def pivot_points(high: pd.Series, low: pd.Series, close: pd.Series):
        pivot = (high.shift(1) + low.shift(1) + close.shift(1)) / 3
        r1 = 2 * pivot - low.shift(1)
        s1 = 2 * pivot - high.shift(1)
        r2 = pivot + (high.shift(1) - low.shift(1))
        s2 = pivot - (high.shift(1) - low.shift(1))
        return pivot, r1, r2, s1, s2

    @staticmethod
    def supertrend(high: pd.Series, low: pd.Series, close: pd.Series, period=10, multiplier=3.0):
        atr_val  = Indicators.atr(high, low, close, period)
        basic_ub = (high + low) / 2 + multiplier * atr_val
        basic_lb = (high + low) / 2 - multiplier * atr_val

        final_ub   = basic_ub.copy()
        final_lb   = basic_lb.copy()
        supertrend = pd.Series(index=close.index, dtype=float)

        for i in range(1, len(close)):
            final_ub.iloc[i] = basic_ub.iloc[i] if (basic_ub.iloc[i] < final_ub.iloc[i-1] or close.iloc[i-1] > final_ub.iloc[i-1]) else final_ub.iloc[i-1]
            final_lb.iloc[i] = basic_lb.iloc[i] if (basic_lb.iloc[i] > final_lb.iloc[i-1] or close.iloc[i-1] < final_lb.iloc[i-1]) else final_lb.iloc[i-1]

            if supertrend.iloc[i-1] == final_ub.iloc[i-1]:
                supertrend.iloc[i] = final_lb.iloc[i] if close.iloc[i] > final_ub.iloc[i] else final_ub.iloc[i]
            else:
                supertrend.iloc[i] = final_ub.iloc[i] if close.iloc[i] < final_lb.iloc[i] else final_lb.iloc[i]

        direction = pd.Series(np.where(close > supertrend, 1, -1), index=close.index)
        return supertrend, direction


class SignalEngine:

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self._compute_all()

    def _compute_all(self):
        df = self.df
        I  = Indicators

        df['ema9']   = I.ema(df['close'], 9)
        df['ema21']  = I.ema(df['close'], 21)
        df['ema50']  = I.ema(df['close'], 50)
        df['ema100'] = I.ema(df['close'], 100)
        df['ema200'] = I.ema(df['close'], 200)
        df['macd'], df['macd_sig'], df['macd_hist'] = I.macd(df['close'])
        df['st'], df['st_dir'] = I.supertrend(df['high'], df['low'], df['close'])

        df['rsi']   = I.rsi(df['close'])
        df['rsi6']  = I.rsi(df['close'], 6)
        df['stoch_k'], df['stoch_d'] = I.stochastic(df['high'], df['low'], df['close'])
        df['willr']  = I.williams_r(df['high'], df['low'], df['close'])

        df['bb_u'], df['bb_m'], df['bb_l'], df['bb_w'], df['bb_pct'] = \
            I.bollinger_bands(df['close'])
        df['atr'] = I.atr(df['high'], df['low'], df['close'])

        df['obv']  = I.obv(df['close'], df['volume'])
        df['vwap'] = I.vwap(df['high'], df['low'], df['close'], df['volume'])

        df['pivot'], df['r1'], df['r2'], df['s1'], df['s2'] = \
            I.pivot_points(df['high'], df['low'], df['close'])

        self.df = df

    def get_signals(self) -> dict:
        df   = self.df
        last = df.iloc[-1]
        prev = df.iloc[-2]
        p    = last['close']
        signals = {}
        scores  = []

        ema_bullish = (p > last['ema9'] > last['ema21'] > last['ema50'])
        ema_bearish = (p < last['ema9'] < last['ema21'] < last['ema50'])
        ema_sig = 'BUY' if ema_bullish else ('SELL' if ema_bearish else 'NEUTRAL')
        signals['EMA_Alignment'] = {
            'signal': ema_sig,
            'details': f"EMA9={last['ema9']:.1f}  EMA21={last['ema21']:.1f}  EMA50={last['ema50']:.1f}",
            'method': 'Trend Following'
        }
        scores.append(1 if ema_bullish else (-1 if ema_bearish else 0))

        macd_cross_up   = prev['macd'] < prev['macd_sig'] and last['macd'] > last['macd_sig']
        macd_cross_down = prev['macd'] > prev['macd_sig'] and last['macd'] < last['macd_sig']
        macd_bull = last['macd'] > last['macd_sig'] and last['macd_hist'] > 0
        macd_bear = last['macd'] < last['macd_sig'] and last['macd_hist'] < 0
        macd_sig_val = 'BUY' if macd_cross_up else ('SELL' if macd_cross_down else ('BUY' if macd_bull else ('SELL' if macd_bear else 'NEUTRAL')))
        signals['MACD'] = {
            'signal': macd_sig_val,
            'details': f"MACD={last['macd']:.4f}  Signal={last['macd_sig']:.4f}  Hist={last['macd_hist']:.4f}",
            'method': 'Trend Following'
        }
        scores.append(1 if macd_sig_val == 'BUY' else (-1 if macd_sig_val == 'SELL' else 0))

        rsi = last['rsi']
        rsi_sig = 'BUY' if rsi < 30 else ('SELL' if rsi > 70 else 'NEUTRAL')
        rsi_zone = '🔴 OVERSOLD' if rsi < 30 else ('🔵 OVERBOUGHT' if rsi > 70 else ('⚠️  WARN OVER' if rsi > 60 else ('⚠️  WARN UNDER' if rsi < 40 else '🟢 NEUTRAL')))
        signals['RSI'] = {
            'signal': rsi_sig,
            'details': f"RSI(14)={rsi:.2f}  [{rsi_zone}]",
            'method': 'Mean Reversion'
        }
        scores.append(1 if rsi_sig == 'BUY' else (-1 if rsi_sig == 'SELL' else 0))

        bb_pct = last['bb_pct']
        bb_sig = 'BUY' if bb_pct < 0.05 else ('SELL' if bb_pct > 0.95 else 'NEUTRAL')
        signals['Bollinger_Bands'] = {
            'signal': bb_sig,
            'details': f"Upper={last['bb_u']:.1f}  Mid={last['bb_m']:.1f}  Lower={last['bb_l']:.1f}  %B={bb_pct:.2f}",
            'method': 'Mean Reversion'
        }
        scores.append(1 if bb_sig == 'BUY' else (-1 if bb_sig == 'SELL' else 0))

        k, d = last['stoch_k'], last['stoch_d']
        pk, pd_ = prev['stoch_k'], prev['stoch_d']
        st_cross_up   = pk < pd_ and k > d and k < 30
        st_cross_down = pk > pd_ and k < d and k > 70
        st_sig = 'BUY' if st_cross_up else ('SELL' if st_cross_down else ('BUY' if k < 20 else ('SELL' if k > 80 else 'NEUTRAL')))
        signals['Stochastic'] = {
            'signal': st_sig,
            'details': f"%K={k:.2f}  %D={d:.2f}",
            'method': 'Momentum'
        }
        scores.append(1 if st_sig == 'BUY' else (-1 if st_sig == 'SELL' else 0))

        wr = last['willr']
        wr_sig = 'BUY' if wr < -80 else ('SELL' if wr > -20 else 'NEUTRAL')
        signals['Williams_R'] = {
            'signal': wr_sig,
            'details': f"W%R={wr:.2f}  ({'Oversold' if wr < -80 else 'Overbought' if wr > -20 else 'Neutral'})",
            'method': 'Momentum'
        }
        scores.append(1 if wr_sig == 'BUY' else (-1 if wr_sig == 'SELL' else 0))

        st_dir  = int(last['st_dir'])
        pst_dir = int(prev['st_dir'])
        st_trend = 'BUY' if st_dir == 1 else 'SELL'
        st_flip = '🔄 FLIP!' if st_dir != pst_dir else '→ Continuing'
        signals['SuperTrend'] = {
            'signal': st_trend,
            'details': f"ST={last['st']:.1f}  Dir={'↑ BULL' if st_dir==1 else '↓ BEAR'}  {st_flip}",
            'method': 'Trend Following'
        }
        scores.append(1 if st_trend == 'BUY' else -1)

        vwap_sig = 'BUY' if p > last['vwap'] else 'SELL'
        pct_from_vwap = (p - last['vwap']) / last['vwap'] * 100
        signals['VWAP'] = {
            'signal': vwap_sig,
            'details': f"VWAP={last['vwap']:.1f}  Price {'+' if pct_from_vwap>=0 else ''}{pct_from_vwap:.2f}% from VWAP",
            'method': 'Volume'
        }
        scores.append(1 if vwap_sig == 'BUY' else -1)

        obv_sma = df['obv'].rolling(20).mean()
        obv_sig = 'BUY' if last['obv'] > obv_sma.iloc[-1] else 'SELL'
        signals['OBV'] = {
            'signal': obv_sig,
            'details': f"OBV above SMA20={'Yes ↑' if obv_sig=='BUY' else 'No ↓'}  (Volume {'accumulation' if obv_sig=='BUY' else 'distribution'})",
            'method': 'Volume'
        }
        scores.append(1 if obv_sig == 'BUY' else -1)

        pivot = last['pivot']
        r1, r2, s1, s2 = last['r1'], last['r2'], last['s1'], last['s2']
        if p > r1:
            pp_sig, pp_zone = 'SELL', f'Above R1={r1:.1f} → Near R2={r2:.1f}'
        elif p > pivot:
            pp_sig, pp_zone = 'BUY',  f'Above Pivot={pivot:.1f} (bullish zone)'
        elif p > s1:
            pp_sig, pp_zone = 'SELL', f'Below Pivot={pivot:.1f} (bearish zone)'
        else:
            pp_sig, pp_zone = 'BUY',  f'Below S1={s1:.1f} → Near S2={s2:.1f}'
        signals['Pivot_Points'] = {
            'signal': pp_sig,
            'details': f"S2={s2:.1f}  S1={s1:.1f}  Pivot={pivot:.1f}  R1={r1:.1f}  R2={r2:.1f}  [{pp_zone}]",
            'method': 'Support/Resistance'
        }
        scores.append(1 if pp_sig == 'BUY' else -1)

        total      = sum(scores)
        n          = len(scores)
        buy_count  = scores.count(1)
        sell_count = scores.count(-1)
        neut_count = scores.count(0)
        pct_buy    = buy_count / n * 100
        pct_sell   = sell_count / n * 100

        if total >= 4:
            consensus = 'STRONG BUY'
        elif total >= 2:
            consensus = 'BUY'
        elif total <= -4:
            consensus = 'STRONG SELL'
        elif total <= -2:
            consensus = 'SELL'
        else:
            consensus = 'NEUTRAL'

        atr_val = last['atr']
        if 'BUY' in consensus:
            tp = p + 2.0 * atr_val
            sl = p - 1.5 * atr_val
            rr = (tp - p) / (p - sl)
        elif 'SELL' in consensus:
            tp = p - 2.0 * atr_val
            sl = p + 1.5 * atr_val
            rr = (p - tp) / (sl - p)
        else:
            tp = sl = rr = None

        return {
            'signals'   : signals,
            'scores'    : scores,
            'consensus' : consensus,
            'buy_count' : buy_count,
            'sell_count': sell_count,
            'neut_count': neut_count,
            'pct_buy'   : pct_buy,
            'pct_sell'  : pct_sell,
            'total_score': total,
            'tp'        : tp,
            'sl'        : sl,
            'rr'        : rr,
            'last'      : last,
            'atr'       : atr_val,
        }


def sig_color(signal: str) -> str:
    return C.GRN if 'BUY' in signal else (C.RED if 'SELL' in signal else C.YLW)


def signal_bar(buy_pct: float, sell_pct: float, width: int = 30) -> str:
    buy_blocks  = int(buy_pct / 100 * width)
    sell_blocks = int(sell_pct / 100 * width)
    neut_blocks = width - buy_blocks - sell_blocks
    bar  = C.GRN + '█' * buy_blocks
    bar += C.YLW + '░' * neut_blocks
    bar += C.RED + '█' * sell_blocks
    bar += C.R
    return bar


def print_header(symbol: str, price: float, interval: str):
    ts = datetime.now().strftime('%Y-%m-%d  %H:%M:%S')
    print(f"\n{C.BLD}{C.CYN}{'═'*65}{C.R}")
    print(f"{C.BLD}{C.WHT}  ⚡ ADVANCED CRYPTO TECHNICAL ANALYSIS  {C.GRY}│ {ts}{C.R}")
    print(f"{C.BLD}{C.CYN}{'═'*65}{C.R}")
    print(f"  {C.BLD}{C.WHT}Symbol :{C.R} {C.BLD}{C.YLW}{symbol}{C.R}   {C.BLD}{C.WHT}Price :{C.R} {C.BLD}${price:,.4f}{C.R}   {C.BLD}{C.WHT}TF :{C.R} {interval}")


def print_signals_table(result: dict):
    signals = result['signals']
    print(f"\n  {C.BLD}{C.CYN}┌─ INDIVIDUAL METHOD SIGNALS {'─'*34}┐{C.R}")
    print(f"  {C.CYN}│{C.R}  {'Method':<22} {'Signal':<10} {'Details'}{C.R}")
    print(f"  {C.CYN}├{'─'*61}┤{C.R}")
    prev_method = None
    for name, data in signals.items():
        method = data['method']
        if method != prev_method:
            print(f"  {C.CYN}│{C.R}  {C.GRY}{method}{C.R}")
            prev_method = method
        sc    = sig_color(data['signal'])
        label = data['signal']
        arrow = '▲' if 'BUY' in label else ('▼' if 'SELL' in label else '─')
        print(f"  {C.CYN}│{C.R}  {C.WHT}{name.replace('_',' '):<22}{C.R} {sc}{C.BLD}{arrow} {label:<8}{C.R} {C.GRY}{data['details']}{C.R}")
    print(f"  {C.CYN}└{'─'*61}┘{C.R}")


def print_consensus(result: dict, symbol: str, price: float):
    consensus  = result['consensus']
    buy_count  = result['buy_count']
    sell_count = result['sell_count']
    neut_count = result['neut_count']
    n          = buy_count + sell_count + neut_count
    pct_buy    = result['pct_buy']
    pct_sell   = result['pct_sell']
    tp         = result['tp']
    sl         = result['sl']
    rr         = result['rr']
    atr        = result['atr']

    sc  = sig_color(consensus)
    bar = signal_bar(pct_buy, pct_sell)

    print(f"\n  {C.BLD}{C.CYN}╔═ CONSENSUS SIGNAL {'═'*43}╗{C.R}")
    print(f"  {C.CYN}║{C.R}  Signal  : {sc}{C.BLD}  ▶  {consensus}  ◀{C.R}")
    print(f"  {C.CYN}║{C.R}  Score   : {C.GRN}BUY {buy_count}/{n}{C.R}  {C.RED}SELL {sell_count}/{n}{C.R}  {C.YLW}NEUTRAL {neut_count}{C.R}")
    print(f"  {C.CYN}║{C.R}  {bar}  {C.GRN}{pct_buy:.0f}%{C.R} vs {C.RED}{pct_sell:.0f}%{C.R}")
    print(f"  {C.CYN}║{C.R}  ATR(14) : ${atr:,.2f}")

    if tp and sl and rr:
        print(f"  {C.CYN}║{C.R}")
        print(f"  {C.CYN}║{C.R}  {C.BLD}{C.WHT}── Trade Setup (ATR-Based) ──{C.R}")
        print(f"  {C.CYN}║{C.R}  Entry   : ${price:,.4f}")
        print(f"  {C.CYN}║{C.R}  {C.GRN}TP      : ${tp:,.4f}{C.R}  (+{abs(tp-price)/price*100:.2f}%)")
        print(f"  {C.CYN}║{C.R}  {C.RED}SL      : ${sl:,.4f}{C.R}  (-{abs(sl-price)/price*100:.2f}%)")
        print(f"  {C.CYN}║{C.R}  R:R     : 1 : {rr:.2f}")

    print(f"  {C.CYN}╚{'═'*61}╝{C.R}")


def print_summary_row(symbol: str, price: float, consensus: str, buy: int, sell: int, n: int):
    sc  = sig_color(consensus)
    bar = signal_bar(buy/n*100, sell/n*100, 15)
    print(f"  {C.YLW}{symbol:<12}{C.R}  ${price:>12,.2f}  {sc}{C.BLD}{consensus:<13}{C.R}  {bar}  {C.GRN}{buy}{C.R}/{C.RED}{sell}{C.R}/{n}")


if __name__ == "__main__":
    print(f"\n{C.BLD}{C.CYN}{'═'*65}")
    print(f"  🚀 SCANNING {len(SYMBOLS)} SYMBOLS…")
    print(f"{'═'*65}{C.R}")

    results = {}
    for sym in SYMBOLS:
        df = fetch_ohlcv(sym)
        if df is None or df.empty:
            print(f"  {C.RED}[SKIP]{C.R} {sym} — no data")
            continue
        price = fetch_price(sym) or float(df.iloc[-1]["close"])
        engine = SignalEngine(df)
        result = engine.get_signals()
        results[sym] = (price, result)
        print_header(sym, price, INTERVAL)
        print_signals_table(result)
        print_consensus(result, sym, price)
        time.sleep(0.3)

    if len(results) > 1:
        print(f"\n{C.BLD}{C.CYN}{'═'*65}{C.R}")
        print(f"{C.BLD}{C.WHT}  📊 SUMMARY TABLE{C.R}")
        print(f"{C.CYN}{'═'*65}{C.R}")
        for sym, (price, r) in results.items():
            n = r['buy_count'] + r['sell_count'] + r['neut_count']
            print_summary_row(sym, price, r['consensus'], r['buy_count'], r['sell_count'], n)
        print(f"{C.CYN}{'═'*65}{C.R}")
