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

# Base signal
ma_signal = (sma_short > sma_long).astype(int)
bull_filter = (close > sma_200).astype(int)
signal = ma_signal * bull_filter

# Stop loss — exit if price drops 15% from entry price
stop_loss_pct = 0.15
position = 0
entry_price = 0
final_signal = []

for i in range(len(close)):
    current_price = close.iloc[i]
    raw_signal = signal.iloc[i]

    if position == 0 and raw_signal == 1:
        # Enter trade
        position = 1
        entry_price = current_price
        final_signal.append(1)

    elif position == 1:
        drawdown = (current_price - entry_price) / entry_price
        if drawdown < -stop_loss_pct:
            # Stop loss triggered
            position = 0
            entry_price = 0
            final_signal.append(0)
        elif raw_signal == 0:
            # MA signal says exit
            position = 0
            entry_price = 0
            final_signal.append(0)
        else:
            final_signal.append(1)
    else:
        final_signal.append(0)

final_signal = pd.Series(final_signal, index=close.index)

# Returns
strategy_returns = daily_returns * final_signal.shift(1)
btc_cumulative = (1 + daily_returns).cumprod()
strategy_cumulative = (1 + strategy_returns).cumprod()

# Stats
total_return = round((strategy_cumulative.iloc[-1] - 1) * 100, 1)
bh_return = round((btc_cumulative.iloc[-1] - 1) * 100, 1)
max_drawdown = round((strategy_cumulative / strategy_cumulative.cummax() - 1).min() * 100, 1)
trades = int(final_signal.diff().abs().sum() / 2)

print(f"--- 2020-2024 Results with Stop Loss ---")
print(f"Strategy Return: {total_return}%")
print(f"Buy & Hold Return: {bh_return}%")
print(f"Max Drawdown: {max_drawdown}%")
print(f"Total Trades: {trades}")

# Plot
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))

ax1.plot(close, label='BTC Price', color='orange', alpha=0.6)
ax1.plot(sma_short, label='5 Day MA', color='blue', alpha=0.8)
ax1.plot(sma_long, label='150 Day MA', color='red', alpha=0.8)
ax1.plot(sma_200, label='200 Day MA', color='purple', alpha=0.8, linestyle='--')
ax1.set_title('BTC Price with Stop Loss Strategy')
ax1.legend()
ax1.grid(True)

ax2.plot(btc_cumulative, label='Buy & Hold', color='orange')
ax2.plot(strategy_cumulative, label='MA + Trend + Stop Loss', color='blue')
ax2.set_title('Portfolio Performance 2020-2024')
ax2.legend()
ax2.grid(True)

plt.tight_layout()
plt.show()