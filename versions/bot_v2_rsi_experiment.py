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

# Moving averages
sma_short = close.rolling(window=5).mean()
sma_long = close.rolling(window=150).mean()
sma_200 = close.rolling(window=200).mean()

# Calculate RSI
delta = close.diff()
gain = delta.where(delta > 0, 0).rolling(window=14).mean()
loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
rs = gain / loss
rsi = 100 - (100 / (1 + rs))

# Combined signal
# Relaxed RSI threshold — only block if extremely overbought (above 80)
ma_signal = (sma_short > sma_long).astype(int)
bull_filter = (close > sma_200).astype(int)
rsi_filter = (rsi < 80).astype(int)

signal = ma_signal * bull_filter * rsi_filter

# Returns
strategy_returns = daily_returns * signal.shift(1)
btc_cumulative = (1 + daily_returns).cumprod()
strategy_cumulative = (1 + strategy_returns).cumprod()

# Stats
total_return = round((strategy_cumulative.iloc[-1] - 1) * 100, 1)
bh_return = round((btc_cumulative.iloc[-1] - 1) * 100, 1)
max_drawdown = round((strategy_cumulative / strategy_cumulative.cummax() - 1).min() * 100, 1)
trades = int(signal.diff().abs().sum() / 2)

print(f"--- 2020-2024 Results with Relaxed RSI Filter ---")
print(f"Strategy Return: {total_return}%")
print(f"Buy & Hold Return: {bh_return}%")
print(f"Max Drawdown: {max_drawdown}%")
print(f"Total Trades: {trades}")

# Plot
fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 12))

# Price
ax1.plot(close, label='BTC Price', color='orange', alpha=0.6)
ax1.plot(sma_short, label='5 Day MA', color='blue', alpha=0.8)
ax1.plot(sma_long, label='150 Day MA', color='red', alpha=0.8)
ax1.plot(sma_200, label='200 Day MA', color='purple', alpha=0.8, linestyle='--')
ax1.set_title('BTC Price')
ax1.legend()
ax1.grid(True)

# RSI
ax2.plot(rsi, label='RSI', color='green')
ax2.axhline(y=80, color='red', linestyle='--', label='Overbought (80)')
ax2.axhline(y=30, color='blue', linestyle='--', label='Oversold (30)')
ax2.set_title('RSI Indicator')
ax2.legend()
ax2.grid(True)
ax2.set_ylim(0, 100)

# Portfolio
ax3.plot(btc_cumulative, label='Buy & Hold', color='orange')
ax3.plot(strategy_cumulative, label='MA + Trend + RSI Strategy', color='blue')
ax3.set_title('Portfolio Performance 2020-2024')
ax3.legend()
ax3.grid(True)

plt.tight_layout()
plt.show()