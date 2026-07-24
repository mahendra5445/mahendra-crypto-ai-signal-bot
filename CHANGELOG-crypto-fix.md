# Crypto signal bot — 5.41% win rate ka asli karan

## Bug 1 — TP detect hi nahi hota tha (sabse bada)

`trade_monitor.py` har 120 second par `get_latest_price()` maangta tha.
Wo function aakhri **5 one-minute closes ka MEDIAN** deta hai.

Do problem:
- Median jaan-boojh kar spike hataata hai — aur TP aksar spike par lagta hai.
- Do poll ke beech TP touch ho kar wapas aa jaye to wo bilkul invisible tha.

SL ke saath ye problem nahi hoti: price SL ke paar jaakar wahin rehta hai,
to agla poll use pakad hi leta hai. Yaani **SL hamesha register hota tha,
TP aksar nahi**. Win rate isi wajah se random-walk baseline (~28%) se bhi
neeche 5.41% par gir gaya.

Fix: monitor ab last 3 one-minute candles ka **high/low** dekhta hai
(`get_recent_range()`), poll 60s ka. Dono taraf ek jaisa insaaf.

## Bug 2 — jeete hue trades "breakeven" gine ja rahe the

Status `"TP"` sirf **TP3** par set hota tha. TP1 hit karke breakeven par
band hua trade profit mein tha, par win rate use haar ginta tha.
Aapke 11 "Breakeven" trades asal mein **jeete** the.

Fix: TP1 tak pahunchne wala har trade ab win hai.

## Bug 3 — TP1 pahunchne layak hi nahi tha

TP1 = 2.5R, TP3 = 6R, 1-minute crypto signals par. Random walk par bhi
2.5R se pehle 1R lagne ka chance ~71% hai — design se hi 4 mein 3 haar.

Fix: TP1 = 1.2R, TP2 = 2R, TP3 = 3R.

## Safai

`risk_FIXED_OPTION1/2/3.py` — teeno files kisi ne import nahi ki thi
(dead code). Hata di gayi.

## Zaroori baat

Purane 84 signals ke stats **galat** the, sirf bot ke performance ki wajah
se nahi. Fix ke baad stats zero se dobara jama karo — purane numbers se
comparison mat karna.
