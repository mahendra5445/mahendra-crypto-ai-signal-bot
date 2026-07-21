# Railway Deploy Guide (Hindi)

## 1. Project banao
- Railway dashboard → New Project → Deploy from GitHub repo
  (ya "Empty Project" + code upload)

## 2. Environment Variables set karo
Project → Variables tab mein add karo:

| Variable   | Value              |
|------------|--------------------|
| BOT_TOKEN  | <apna telegram bot token, @BotFather se> |
| CHANNEL_ID | <apna channel ka @username ya numeric chat id> |
| DATA_DIR   | /data              |
| LOG_DIR    | /data/logs         |

## 3. Bot ko channel ka admin banao
Telegram app mein:
- Apne channel → Administrators → Add Admin
- Bot ka username search karke add karo
- **"Post Messages"** permission zaroor on rakho

## 4. Volume attach karo (IMPORTANT!)
Railway ka filesystem bhi ephemeral hai — har deploy/restart pe
files delete ho jaati hain. Volume ke bina trades.json har baar
reset ho jayega (open trades ka crash-recovery kaam nahi karega).

- Service pe right-click (ya service settings) → "Attach Volume"
- Mount path: `/data`
- Bas — ab DATA_DIR=/data ki wajah se saara data volume pe
  save hoga aur restarts/deploys pe bacha rahega.

Note: Volumes Railway ke Hobby plan ($5/month) pe milte hain.
Agar volume nahi lagate to bot phir bhi chalega, lekin har
restart pe trade history reset ho jayegi.

## 5. Deploy
Push/deploy karo — logs mein yeh dikhna chahiye:
```
[INIT] Background tasks started
🚀 Mahendra Crypto AI Signal starting…
```

## 6. Test
Telegram pe bot ko DM mein `/start` bhejo (bot online hone ka
confirmation), phir `/btc`, `/eth`, `/sol` jaisa koi manual command
try karo. Auto-signals apne aap channel mein aane lagenge (pehla
signal aane mein thoda time lag sakta hai — depends on market
conditions/score threshold).

Agar channel mein kuch nahi aa raha:
- Check karo `CHANNEL_ID` sahi hai (public channel ke liye `@username`
  format, `t.me/` prefix nahi)
- Check karo bot channel ka admin hai "Post Messages" permission ke
  saath
- Railway logs mein `[CHANNEL SEND ERROR]` ya `CHANNEL_ID not set`
  jaisi warning dhundo
