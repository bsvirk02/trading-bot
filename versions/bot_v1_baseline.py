import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Pull data
btc = yf.download('BTC-USD', start='2020-01-01', end='2024-12-31', interval='1d')

if isinstance(btc.columns, pd.MultiIndex):
    btc.columns = btc.columns.get_level_values(0)

close = btc['Close'].squeeze()
daily_returns = close.pct_change()

# Original signals
sma_short = close.rolling(window=5).mean()
sma_long = close.rolling(window=150).mean()

# Trend filter — 200 day MA
# Only trade when price is ABOVE 200 day MA (bull market confirmed)
sma_200 = close.rolling(window=200).mean()
in_bull_market = (close > sma_200).astype(int)

# Combined signal — only buy when crossover AND in bull market
signal_raw = (sma_short > sma_long).astype(int)
signal_filtered = signal_raw * in_bull_market

# Returns
strategy_returns = daily_returns * signal_filtered.shift(1)
btc_cumulative = (1 + daily_returns).cumprod()
strategy_cumulative = (1 + strategy_returns).cumprod()

# Stats
total_return = round((strategy_cumulative.iloc[-1] - 1) * 100, 1)
bh_return = round((btc_cumulative.iloc[-1] - 1) * 100, 1)
max_drawdown = round((strategy_cumulative / strategy_cumulative.cummax() - 1).min() * 100, 1)
trades = int(signal_filtered.diff().abs().sum() / 2)

print(f"--- 2020-2024 Full Period Results ---")
print(f"Strategy Return: {total_return}%")
print(f"Buy & Hold Return: {bh_return}%")
print(f"Max Drawdown: {max_drawdown}%")
print(f"Total Trades: {trades}")

# Plot
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))

# Price + MAs
ax1.plot(close, label='BTC Price', color='orange', alpha=0.6)
ax1.plot(sma_short, label='5 Day MA', color='blue', alpha=0.8)
ax1.plot(sma_long, label='150 Day MA', color='red', alpha=0.8)
ax1.plot(sma_200, label='200 Day MA (Trend Filter)', color='purple', alpha=0.8, linestyle='--')
ax1.set_title('BTC Price with Trend Filter Added')
ax1.legend()
ax1.grid(True)

# Portfolio
ax2.plot(btc_cumulative, label='Buy & Hold', color='orange')
ax2.plot(strategy_cumulative, label='MA Strategy + Trend Filter', color='blue')
ax2.set_title('Portfolio Performance 2020-2024 — $1000 Starting Capital')
ax2.legend()
ax2.grid(True)

plt.tight_layout()
plt.show()