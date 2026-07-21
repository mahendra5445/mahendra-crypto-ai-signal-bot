# Mahendra Crypto AI Signal

Telegram bot jo 12 high-liquidity crypto coins — BTC, ETH, SOL, XRP, BNB,
DOGE, ADA, LINK, AVAX, TON, SUI, LTC — ke liye AI-scored trading signals
deta hai, technical indicators, multi-timeframe trend, smart-money
concepts (BOS/CHoCH/liquidity), aur candlestick patterns ko combine
karke, aur **seedha ek Telegram channel mein post karta hai**
(individual users ko `/start` karne ki zaroorat nahi).

## Original bot se kya different hai

Ye gold-ai-telegram-bot ka clone hai. Sab signal-scoring engine,
risk/SL/TP logic, aur background jobs same hain — do cheezein badli hain:

1. **Assets** — Gold/BTC/Oil/EUR-USD/USD-JPY/LINK/ATOM ki jagah ab 12
   crypto coins hain (upar list dekho).
2. **Delivery** — har registered user ko alag-alag message bhejne ki
   jagah, sab signals ek fixed Telegram **channel** (`CHANNEL_ID`) mein
   post hote hain — bot us channel ka admin hona chahiye ("Post
   Messages" permission ke saath).

## Kaise kaam karta hai

- Har **15 minute** mein bot saare configured coins (`config.py` →
  `ASSET_LIST`) ka data check karta hai (`auto_signal.py`).
- Signal 12 confirmation checks (EMA, ADX, Supertrend, VWAP, MACD,
  RSI, multi-timeframe trend, volume, ATR, liquidity, volume spike,
  candle confirmation) ke against score hota hai — sirf tab fire
  hota hai jab score aur confirmations dono threshold cross karein
  (`strategy.py` → `MIN_SCORE`, `MIN_CONFIRMATIONS`).
- Ek signal ke baad us coin ke liye **15-minute cooldown** lagta hai
  aur jab tak purana trade close nahi hota, naya signal nahi aata
  (ek time pe ek open trade per coin).
- Entry/SL/TP ATR-based hain (`risk.py`) — SL = 2.0× ATR, TP1/TP2/TP3
  = 2.5R / 4R / 6R. Har coin ka display/rounding precision uske
  `decimals` config se control hota hai — chhote-price coins (jaise
  XRP, DOGE) ke liye ye precision zaroori hai warna signal levels
  galat round ho jaate hain.
- Open trades `trade_monitor.py` har 2 minute mein price check karke
  track karta hai (SL / breakeven / TP1 / TP2 / TP3 hits) aur channel
  mein update post karta hai.
- `watchdog.py` har 5 minute mein check karta hai ki auto-signal loop
  zinda hai ya nahi (agar 40+ minute se koi cycle complete na hui ho,
  matlab kahin stuck/silently failed hai, to channel mein alert bhejta
  hai).
- `daily_summary.py` roz ek fixed time pe (default 18:00 server-local)
  us din ka combined + per-coin signal count aur win rate channel mein
  post karta hai.
- Sab trades `data/trades.json` mein persist hote hain (atomic
  writes).

## Assets

| Coin | Command | Yahoo Symbol | Binance Symbol | Decimals |
|------|---------|-------------|-----------------|----------|
| Bitcoin | `/btc` | `BTC-USD` | `BTCUSDT` | 2 |
| Ethereum | `/eth` | `ETH-USD` | `ETHUSDT` | 2 |
| Solana | `/sol` | `SOL-USD` | `SOLUSDT` | 2 |
| XRP | `/xrp` | `XRP-USD` | `XRPUSDT` | 4 |
| BNB | `/bnb` | `BNB-USD` | `BNBUSDT` | 2 |
| Dogecoin | `/doge` | `DOGE-USD` | `DOGEUSDT` | 5 |
| Cardano | `/ada` | `ADA-USD` | `ADAUSDT` | 4 |
| Chainlink | `/link` | `LINK-USD` | `LINKUSDT` | 4 |
| Avalanche | `/avax` | `AVAX-USD` | `AVAXUSDT` | 2 |
| Toncoin | `/ton` | `TON-USD` | `TONUSDT` | 3 |
| Sui | `/sui` | `SUI-USD` | `SUIUSDT` | 4 |
| Litecoin | `/ltc` | `LTC-USD` | `LTCUSDT` | 2 |

Naya coin add karna ho to bas `config.py` → `ASSETS` dict mein ek
entry add karo — baaki sab code (`data.py`, `main.py`,
`auto_signal.py`) generic hai aur apne aap naya coin pick kar leta hai.

## Commands

Ye commands bot ko **DM** mein bheje jaate hain (channel mein nahi) —
manual check ke liye, jaise ki testing:

| Command    | Kaam |
|------------|------|
| `/start`   | Bot online hai ya nahi confirm karo, command list dekho |
| `/btc`, `/eth`, `/sol`, `/xrp`, `/bnb`, `/doge`, `/ada`, `/link`, `/avax`, `/ton`, `/sui`, `/ltc` | Manual signal check us coin ka |
| `/signal`  | `/btc` jaisa hi |
| `/trend`   | 1M/5M/15M trend summary (BTC) |
| `/stats`   | Trade statistics (add coin name for one coin, e.g. `/stats sol`; no arg = combined + per-coin breakdown) |
| `/history` | Last 10 trades (add coin name to filter, e.g. `/history eth`) |

**Auto-signals, trade updates, watchdog alerts, aur daily summary**
sab automatically channel mein post hote hain — koi command nahi
chahiye.

## Setup

1. `pip install -r requirements.txt`
2. `.env.example` ko `.env` bana lo aur fill karo:
   - `BOT_TOKEN` — @BotFather se milega
   - `CHANNEL_ID` — jis channel mein post karna hai, uska `@username`
     (public) ya numeric chat id (private)
3. Bot ko us channel ka **admin** banao ("Post Messages" permission ke
   saath)
4. `python main.py`

Production deploy (Railway) ke liye `RAILWAY_SETUP.md` dekho — wahan
persistent volume attach karna zaroori hai warna restarts pe trade
history reset ho jayegi.

## Key files

| File | Kaam |
|------|------|
| `strategy.py` | Signal scoring engine — thresholds yahan hain |
| `risk.py` | SL/TP calculation (ATR-based, decimals-aware) |
| `auto_signal.py` | Background loop jo saare coins ke signals check/post karta hai |
| `trade_monitor.py` | Open trades ko SL/TP ke against track karta hai |
| `data.py` | Yahoo Finance se price data (+ Binance/Coinbase fallback), sab coins ke liye generic |
| `config.py` | `ASSETS` registry (12 coins) + `CHANNEL_ID` |
| `notify.py` | Channel-broadcast helper — sab jobs isi se message post karte hain |
| `watchdog.py` | Auto-signal loop stuck/dead detect karke alert bhejta hai |
| `daily_summary.py` | Roz ka signal/win-rate digest bhejta hai |

## Config tuning

Signal frequency aur risk:reward `strategy.py` aur `risk.py` ke top
par defined constants se control hote hain — waha comments mein
explain kiya gaya hai ki har value ka kya effect hai.

## Known limitations

- News filter (`news.py`) sirf high-impact **USD** events check karta
  hai — sab coins USD-priced hain, isliye ye sab par apply hota hai.
- `daily_summary.py` server ke local time-zone se chalta hai (jo
  Railway pe usually UTC hota hai) — `SUMMARY_HOUR` constant adjust
  karke apna preferred IST time set kar sakte ho.
- Agar `CHANNEL_ID` set nahi hai ya bot channel ka admin nahi hai, to
  signals silently skip ho jayenge (logs mein warning aayegi) — DM
  commands (`/btc` etc.) tab bhi kaam karenge.
