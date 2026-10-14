const VERSION = "2.5.0";
const BOT_NAME = "RoninTrBot";

import { analyzeTimeframe, classifyRegime } from "./analysis.js";

/*
  RoninTrBot v2.5.0
  Cloudflare Worker
  Telegram Control Panel + Broadcast

  Required secret:
  TELEGRAM_BOT_TOKEN

  Variables:
  OWNER_TELEGRAM_ID
  ADMIN_TELEGRAM_IDS

  KV Binding:
  USERS_KV
*/

export default {
  async fetch(request, env) {
    try {
      if (request.method === "GET") {
        return json({
          service: BOT_NAME,
          version: VERSION,
          status: "online",
          engine: "offline",
          exchange: "disconnected",
          paperTrading: "ready",
          webhookAuth: env.WEBHOOK_SECRET ? "secret_verified" : "not_configured",
          broadcast: env.USERS_KV ? "ready" : "kv_missing"
        });
      }

      if (request.method !== "POST") {
        return json({ error: "Method Not Allowed" }, 405);
      }

      if (!env.TELEGRAM_BOT_TOKEN) {
        return json({
          error: "Missing TELEGRAM_BOT_TOKEN"
        }, 500);
      }

      // Webhook authentication: once WEBHOOK_SECRET is configured and the
      // Telegram webhook is (re)registered with secret_token=<WEBHOOK_SECRET>,
      // every update must carry the matching X-Telegram-Bot-Api-Secret-Token
      // header. Without it, forged POSTs could impersonate any user id
      // (including the owner) and trigger admin actions such as broadcast.
      if (env.WEBHOOK_SECRET) {
        const header = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
        if (header !== env.WEBHOOK_SECRET) {
          return json({ error: "Forbidden" }, 403);
        }
      }

      const contentLength = Number(request.headers.get("content-length") || "0");
      if (contentLength > 1024 * 1024) {
        return json({ error: "Payload Too Large" }, 413);
      }

      let update;
      try {
        update = await request.json();
      } catch {
        return json({ error: "Invalid JSON body" }, 400);
      }
      if (!update || typeof update !== "object") {
        return json({ error: "Invalid update payload" }, 400);
      }
      const bot = new Telegram(env.TELEGRAM_BOT_TOKEN);

      /* =========================
         MESSAGE
      ========================= */

      if (update.message) {
        const message = update.message;
        const chatId = message.chat.id;
        const user = message.from;
        const text = message.text || "";
        const role = getRole(user, env);

        /* ===== REGISTER USER ===== */

        if (
          env.USERS_KV &&
          message.chat?.type === "private"
        ) {
          await registerUser(
            env,
            chatId,
            user
          );
        }

        /* ===== BROADCAST INPUT ===== */

        if (
          text &&
          !text.startsWith("/") &&
          isAdmin(role) &&
          env.USERS_KV
        ) {
          const pendingKey =
            `broadcast:pending:${user.id}`;

          const pending =
            await env.USERS_KV.get(pendingKey);

          if (pending === "waiting") {

            const recipients =
              await countUsers(env);

            // Pending previews expire after 10 minutes: a stale "preview"
            // must never be confirmable later by accident.
            await env.USERS_KV.put(
              pendingKey,
              JSON.stringify({
                text: text.slice(0, 3900),
                status: "preview"
              }),
              { expirationTtl: 600 }
            );

            await bot.sendMessage(
              chatId,
              broadcastPreview(
                text,
                recipients
              ),
              broadcastConfirmKeyboard()
            );

            return json({ ok: true });
          }
        }

        /* ===== COMMANDS ===== */

        if (text === "/start") {

          await bot.sendMessage(
            chatId,
            dashboard(role),
            mainKeyboard(role)
          );
        }

        else if (text === "/panel") {

          await bot.sendMessage(
            chatId,
            dashboard(role),
            mainKeyboard(role)
          );
        }

        else if (text === "/status") {

          await bot.sendMessage(
            chatId,
            systemStatus(role),
            statusKeyboard(role)
          );
        }

        else if (text.trim().split(/\s+/)[0].split("@")[0] === "/market") {

          const parts = text.trim().split(/\s+/);
          const args = parts.slice(1);
          if (args.length > 2) {
            await bot.sendMessage(chatId, "Usage: /market [symbol] [15m|1h|4h]\nExample: /market btc_usdt 15m", backKeyboard());
          } else {
            const symbol = args[0] || "btc_usdt";
            const interval = args[1] || "15m";
            const market = await marketDataPage(symbol, interval);
            if (market.imageBlob || market.chartUrl) {
              const sent = await bot.sendPhoto(chatId, market.imageBlob || market.chartUrl, market.caption, backKeyboard());
              if (!sent?.ok) await bot.sendMessage(chatId, market.text || "Chart image failed to send.", backKeyboard());
            } else {
              await bot.sendMessage(chatId, market.text || String(market), marketAssetKeyboard());
            }
          }
        }

        else if (text === "/help") {

          await bot.sendMessage(
            chatId,
            helpText(role),
            backKeyboard()
          );
        }

        return json({ ok: true });
      }

      /* =========================
         CALLBACK
      ========================= */

      if (update.callback_query) {

        const query = update.callback_query;
        const user = query.from;

        const chatId =
          query.message?.chat?.id;

        const messageId =
          query.message?.message_id;

        const action = query.data;

        const role =
          getRole(user, env);

        if (!chatId || !messageId) {
          await bot.answerCallbackQuery(
            query.id
          );

          return json({ ok: true });
        }

        if (!isAllowed(action, role)) {

          await bot.answerCallbackQuery(
            query.id,
            "⛔ Access denied",
            true
          );

          return json({ ok: true });
        }

        await bot.answerCallbackQuery(query.id);

        const assetMatch = /^market_asset_(btc_usdt|eth_usdt|sol_usdt|xrp_usdt|bnb_usdt|doge_usdt)$/.exec(action || "");
        if (assetMatch) {
          const symbol = assetMatch[1];
          await bot.editMessageText(chatId, messageId, marketTimeframePrompt(symbol), marketTimeframeKeyboard(symbol));
          return json({ ok: true, marketStep: "timeframe", symbol });
        }
        const timeframeMatch = /^market_tf_(btc_usdt|eth_usdt|sol_usdt|xrp_usdt|bnb_usdt|doge_usdt)_(15m|1h|4h)$/.exec(action || "");
        if (timeframeMatch) {
          const symbol = timeframeMatch[1];
          const interval = timeframeMatch[2];
          await bot.editMessageText(chatId, messageId, "⏳ Loading " + symbol.toUpperCase() + " · " + interval + " chart…", backKeyboard());
          const market = await marketDataPage(symbol, interval);
          if (market.imageBlob || market.chartUrl) {
            const sent = await bot.sendPhoto(chatId, market.imageBlob || market.chartUrl, market.caption, backKeyboard());
            if (!sent?.ok) await bot.sendMessage(chatId, "Chart image failed to send. Please retry from Market Data.", backKeyboard());
            return json({ ok: true, chartSent: !!sent?.ok, symbol, interval });
          }
          await bot.editMessageText(chatId, messageId, market.text || "Market data unavailable.", marketAssetKeyboard());
          return json({ ok: true, chartSent: false, symbol, interval });
        }

        const strategyAsset = /^strategy_asset_(btc_usdt|eth_usdt|sol_usdt|xrp_usdt|bnb_usdt|doge_usdt)$/.exec(action || "");
        if (strategyAsset) {
          const symbol = strategyAsset[1];
          await bot.editMessageText(chatId, messageId, strategyTimeframePrompt(symbol), strategyTimeframeKeyboard(symbol));
          return json({ ok: true, strategyStep: "timeframe", symbol });
        }
        const strategyTf = /^strategy_tf_(btc_usdt|eth_usdt|sol_usdt|xrp_usdt|bnb_usdt|doge_usdt)_(15m|1h|4h)$/.exec(action || "");
        if (strategyTf) {
          const page = await strategyAnalysisPage(strategyTf[1], strategyTf[2]);
          await bot.editMessageText(chatId, messageId, page, strategyAssetKeyboard());
          return json({ ok: true, strategyStep: "analysis", symbol: strategyTf[1] });
        }

        let text;
        let keyboard;

        switch (action) {

          /* ===== MAIN ===== */

          case "home":

            text = dashboard(role);
            keyboard = mainKeyboard(role);

            break;

          case "status":

            text = systemStatus(role);
            keyboard = statusKeyboard(role);

            break;

          case "trades":

            text = tradesPage();
            keyboard = backKeyboard();

            break;

          case "charts":
            text = marketAssetPrompt();
            keyboard = marketAssetKeyboard();
            break;

          case "strategy":

            text = strategyAssetPrompt();
            keyboard = strategyAssetKeyboard();

            break;

          case "settings":

            text = settingsPage();
            keyboard = backKeyboard();

            break;

          case "management":

            text = managementPage();
            keyboard = managementKeyboard(role);

            break;

          case "refresh":

            text = dashboard(role);
            keyboard = mainKeyboard(role);

            break;


          /* ===== PAPER ===== */

          case "paper":

            text = paperTradingPage(role);
            keyboard = paperTradingKeyboard(role);

            break;

          case "paper_stats":

            text = paperStatisticsPage();
            keyboard = paperBackKeyboard();

            break;

          case "paper_positions":

            text = paperPositionsPage();
            keyboard = paperBackKeyboard();

            break;

          case "paper_history":

            text = paperHistoryPage();
            keyboard = paperBackKeyboard();

            break;

          case "paper_pause":

            text = actionMessage(
              "PAPER TRADING",
              "⏸ PAPER MODE PAUSED"
            );

            keyboard =
              paperTradingKeyboard(role);

            break;

          case "paper_resume":

            text = actionMessage(
              "PAPER TRADING",
              "▶️ PAPER MODE RESUMED"
            );

            keyboard =
              paperTradingKeyboard(role);

            break;


          /* ===== MANAGEMENT ===== */

          case "start_engine":

            text = actionMessage(
              "ENGINE",
              "⚙️ Trading Engine start requested"
            );

            keyboard =
              managementKeyboard(role);

            break;

          case "stop_engine":

            text = actionMessage(
              "ENGINE",
              "⛔ Trading Engine stop requested"
            );

            keyboard =
              managementKeyboard(role);

            break;

          case "emergency":

            text = actionMessage(
              "EMERGENCY",
              "🚨 Emergency stop requested"
            );

            keyboard =
              managementKeyboard(role);

            break;


          /* ===== BROADCAST ===== */

          case "broadcast":

            if (!env.USERS_KV) {

              text = actionMessage(
                "BROADCAST",
                "❌ USERS_KV binding is not configured."
              );

              keyboard =
                managementKeyboard(role);

              break;
            }

            await env.USERS_KV.put(
              `broadcast:pending:${user.id}`,
              "waiting",
              { expirationTtl: 600 }
            );

            text = `
🥷 RoninTrBot • BROADCAST
━━━━━━━━━━━━━━━━━━

📢 Send the message you want
to broadcast.

✍️ Just type the message
in your next message.

━━━━━━━━━━━━━━━━━━

👑 Access
${role}
`;

            keyboard =
              broadcastCancelKeyboard();

            break;


          case "broadcast_cancel":

            if (env.USERS_KV) {

              await env.USERS_KV.delete(
                `broadcast:pending:${user.id}`
              );
            }

            text = actionMessage(
              "BROADCAST",
              "❌ Broadcast cancelled."
            );

            keyboard =
              managementKeyboard(role);

            break;


          case "broadcast_confirm": {

            if (!env.USERS_KV) {

              text = actionMessage(
                "BROADCAST",
                "❌ USERS_KV binding is not configured."
              );

              keyboard =
                managementKeyboard(role);

              break;
            }

            const pendingKey =
              `broadcast:pending:${user.id}`;

            const raw =
              await env.USERS_KV.get(
                pendingKey
              );

            if (!raw) {

              text = actionMessage(
                "BROADCAST",
                "⚠️ No pending broadcast found."
              );

              keyboard =
                managementKeyboard(role);

              break;
            }

            let data;

            try {

              data = JSON.parse(raw);

            } catch {

              data = null;
            }

            if (
              !data?.text ||
              data.status !== "preview"
            ) {

              text = actionMessage(
                "BROADCAST",
                "⚠️ Invalid broadcast state."
              );

              keyboard =
                managementKeyboard(role);

              break;
            }

            const result =
              await broadcastMessage(
                env,
                bot,
                data.text
              );

            await env.USERS_KV.delete(
              pendingKey
            );

            text = `
🥷 RoninTrBot • BROADCAST
━━━━━━━━━━━━━━━━━━

📢 Broadcast completed

👥 Total
${result.total}

✅ Sent
${result.sent}

❌ Failed
${result.failed}

━━━━━━━━━━━━━━━━━━

${
  result.failed > 0
    ? "⚠️ Failed users were removed."
    : "🟢 All messages sent successfully."
}
`;

            keyboard =
              managementKeyboard(role);

            break;
          }


          default:

            text = dashboard(role);
            keyboard = mainKeyboard(role);
        }

        await bot.editMessageText(
          chatId,
          messageId,
          text,
          keyboard
        );

        return json({ ok: true });
      }

      return json({ ok: true });

    } catch (error) {

      console.error("ronin-panel error:", error);
      return json({ ok: false, error: "internal error" }, 500);
    }
  }
};


/* =====================================================
   TELEGRAM
===================================================== */

class Telegram {

  constructor(token) {

    this.url =
      `https://api.telegram.org/bot${token}`;
  }

  async call(method, body) {

    const response =
      await fetch(
        `${this.url}/${method}`,
        {
          method: "POST",
          headers: {
            "Content-Type":
              "application/json"
          },
          body:
            JSON.stringify(body)
        }
      );

    return await response.json();
  }

  async sendMessage(
    chatId,
    text,
    replyMarkup
  ) {

    const body = {
      chat_id: chatId,
      text
    };

    if (replyMarkup) {
      body.reply_markup =
        replyMarkup;
    }

    return this.call(
      "sendMessage",
      body
    );
  }

  async sendPhoto(chatId, photo, caption, replyMarkup) {
    if (photo instanceof Blob) {
      const form = new FormData();
      form.append("chat_id", String(chatId));
      form.append("photo", photo, "market-chart.png");
      if (caption) form.append("caption", caption);
      if (replyMarkup) form.append("reply_markup", JSON.stringify(replyMarkup));
      const response = await fetch(this.url + "/sendPhoto", { method: "POST", body: form });
      return await response.json();
    }
    const body = { chat_id: chatId, photo: photo, caption: caption };
    if (replyMarkup) body.reply_markup = replyMarkup;
    return this.call("sendPhoto", body);
  }

  async editMessageText(
    chatId,
    messageId,
    text,
    replyMarkup
  ) {

    const body = {
      chat_id: chatId,
      message_id: messageId,
      text
    };

    if (replyMarkup) {
      body.reply_markup =
        replyMarkup;
    }

    return this.call(
      "editMessageText",
      body
    );
  }

  async answerCallbackQuery(
    id,
    text = null,
    showAlert = false
  ) {

    const body = {
      callback_query_id: id
    };

    if (text) {

      body.text = text;
      body.show_alert = showAlert;
    }

    return this.call(
      "answerCallbackQuery",
      body
    );
  }
}


/* =====================================================
   ROLES
===================================================== */

function getRole(user, env) {

  const id =
    String(user?.id || "");

  const owner =
    String(
      env.OWNER_TELEGRAM_ID || ""
    );

  const admins =
    String(
      env.ADMIN_TELEGRAM_IDS || ""
    )
      .split(",")
      .map(x => x.trim())
      .filter(Boolean);

  if (id === owner) {
    return "OWNER";
  }

  if (admins.includes(id)) {
    return "ADMIN";
  }

  return "MEMBER";
}


function isAdmin(role) {

  return (
    role === "OWNER" ||
    role === "ADMIN"
  );
}


function isAllowed(
  action,
  role
) {

  const publicActions = [
    "home", "status", "trades", "charts", "strategy", "paper",
    "paper_stats", "paper_positions", "paper_history", "refresh"
  ];

  if (/^market_asset_(btc_usdt|eth_usdt|sol_usdt|xrp_usdt|bnb_usdt|doge_usdt)$/.test(action || "") ||
      /^market_tf_(btc_usdt|eth_usdt|sol_usdt|xrp_usdt|bnb_usdt|doge_usdt)_(15m|1h|4h)$/.test(action || "")) return true;
  if (/^strategy_asset_(btc_usdt|eth_usdt|sol_usdt|xrp_usdt|bnb_usdt|doge_usdt)$/.test(action || "") ||
      /^strategy_tf_(btc_usdt|eth_usdt|sol_usdt|xrp_usdt|bnb_usdt|doge_usdt)_(15m|1h|4h)$/.test(action || "")) return true;

  const adminActions = [

    "settings",
    "management",

    "broadcast",
    "broadcast_confirm",
    "broadcast_cancel",

    "start_engine",
    "stop_engine",
    "emergency",

    "paper_pause",
    "paper_resume"
  ];

  if (
    publicActions.includes(action)
  ) {
    return true;
  }

  if (
    adminActions.includes(action) &&
    isAdmin(role)
  ) {
    return true;
  }

  return false;
}


/* =====================================================
   DASHBOARD
===================================================== */

function dashboard(role) {

  return `
🥷 RoninTrBot
━━━━━━━━━━━━━━━━━━

🟢 SYSTEM ONLINE

Access
${roleEmoji(role)} ${role}

Core Status

🤖 Bot          ● ONLINE
🖥 Panel        ● ONLINE
⚙️ Engine       ○ OFFLINE
🔌 LBank        ● PUBLIC READ-ONLY
🧪 Paper        ● READY

━━━━━━━━━━━━━━━━━━

Trading

📈 Trades       0
📊 Market Data   READY

Analysis

🧠 Strategy     STANDBY
🛡 Risk         STANDBY

━━━━━━━━━━━━━━━━━━
🥷 RoninTrBot Control Center
`;
}


/* =====================================================
   SYSTEM
===================================================== */

function systemStatus(role) {

  return `
⚡ RoninTrBot • SYSTEM
━━━━━━━━━━━━━━━━━━

🤖 Bot
ONLINE

🖥 Cloudflare Panel
ONLINE

⚙️ Trading Engine
OFFLINE

🔌 LBank
PUBLIC READ-ONLY

🧠 Strategy Engine
WAITING

🛡 Risk Manager
WAITING

📊 Market Data
ON-DEMAND

🗄 Database
WAITING

🧪 Paper Trading
READY

📢 Broadcast
${"USERS_KV" in {} ? "READY" : "CONFIGURED"}

━━━━━━━━━━━━━━━━━━

Access
${roleEmoji(role)} ${role}

System Health: 🟢 PANEL OK
`;
}


/* =====================================================
   TRADES
===================================================== */

function tradesPage() {

  return `
📈 RoninTrBot • TRADES
━━━━━━━━━━━━━━━━━━

Open Positions
0

Last Trade
—

Today's PnL
—

Win Rate
—

Profit Factor
—

━━━━━━━━━━━━━━━━━━

⚙️ Trading Engine
OFFLINE

No live trades yet.
`;
}


/* =====================================================
   CHARTS
===================================================== */

function marketAssetPrompt() {
  return "📊 RoninTrBot · MARKET DATA\n━━━━━━━━━━━━━━━━━━\nChoose an asset to view public LBank spot-market data.\n\n🔒 Read-only · No orders will be placed.";
}
function marketAssetKeyboard() {
  return { inline_keyboard: [
    [button("🟠 BTC / USDT", "market_asset_btc_usdt"), button("💠 ETH / USDT", "market_asset_eth_usdt")],
    [button("🟣 SOL / USDT", "market_asset_sol_usdt"), button("💧 XRP / USDT", "market_asset_xrp_usdt")],
    [button("🟡 BNB / USDT", "market_asset_bnb_usdt"), button("🐕 DOGE / USDT", "market_asset_doge_usdt")],
    [button("🏠 Dashboard", "home")]
  ]};
}
function marketTimeframePrompt(symbol) {
  return "📊 " + symbol.toUpperCase().replace("_", "/") + "\nChoose a timeframe for the combined candlestick, volume and KDJ chart.";
}
function marketTimeframeKeyboard(symbol) {
  return { inline_keyboard: [
    [button("15 minutes", "market_tf_" + symbol + "_15m"), button("1 hour", "market_tf_" + symbol + "_1h"), button("4 hours", "market_tf_" + symbol + "_4h")],
    [button("↩ Choose asset", "charts"), button("🏠 Dashboard", "home")]
  ]};
}
async function marketDataPage(symbol = "btc_usdt", interval = "15m") {
  const intervals = { "15m": { type: "minute15", seconds: 900 }, "1h": { type: "hour1", seconds: 3600 }, "4h": { type: "hour4", seconds: 14400 } };
  const supported = ["btc_usdt", "eth_usdt", "sol_usdt", "xrp_usdt", "bnb_usdt", "doge_usdt"];
  symbol = String(symbol || "").toLowerCase();
  if (!supported.includes(symbol)) return { text: "Unsupported asset. Choose one from the Market Data menu." };
  if (!intervals[interval]) return { text: "Unsupported timeframe. Choose 15m, 1h, or 4h." };
  try {
    const now = Math.floor(Date.now() / 1000);
    const size = 100;
    // LBank interprets `time` as the starting timestamp, not the current time.
    const startTime = now - intervals[interval].seconds * size;
    const params = new URLSearchParams({ symbol, type: intervals[interval].type, size: String(size), time: String(startTime) });
    const response = await fetch("https://api.lbkex.com/v2/kline.do?" + params.toString(), { headers: { Accept: "application/json" }, signal: AbortSignal.timeout(7000) });
    if (!response.ok) throw new Error("http");
    const payload = await response.json();
    if (!Array.isArray(payload) && (String(payload?.error_code ?? "0") !== "0" || !["true", "True", true, undefined].includes(payload?.result))) throw new Error("api");
    const rows = Array.isArray(payload) ? payload : payload?.data;
    if (!Array.isArray(rows) || rows.length < 20) throw new Error("empty");
    const candles = rows.map(row => {
      if (!Array.isArray(row) || row.length !== 6) throw new Error("shape");
      let ts = Number(row[0]); if (ts > 1000000000000) ts = Math.floor(ts / 1000);
      const values = row.slice(1).map(Number);
      if (!Number.isFinite(ts) || ts <= 0 || values.some(v => !Number.isFinite(v))) throw new Error("number");
      const [open, high, low, close, volume] = values;
      if (Math.min(open, high, low, close) <= 0 || volume < 0 || high < Math.max(open, close, low) || low > Math.min(open, close, high)) throw new Error("ohlc");
      return { ts, open, high, low, close, volume };
    }).sort((a,b)=>a.ts-b.ts);
    const latest = candles[candles.length-1];
    if (latest.ts > now + intervals[interval].seconds || now-latest.ts > intervals[interval].seconds*3) throw new Error("stale");
    const stamp = new Date(latest.ts*1000).toISOString().replace("T", " ").slice(0,16) + " UTC";
    // Stochastic KDJ: 9-period RSV, then K and D smoothed with the standard 1/3 recurrence.
    let k=50, d=50; const kd=[];
    for (let i=0;i<candles.length;i++) {
      const window=candles.slice(Math.max(0,i-8),i+1);
      const highest=Math.max(...window.map(c=>c.high)); const lowest=Math.min(...window.map(c=>c.low));
      const rsv=highest===lowest?50:((candles[i].close-lowest)/(highest-lowest))*100;
      k=(2*k+rsv)/3; d=(2*d+k)/3; kd.push({k,d,j:3*k-2*d});
    }
    const fmt=n=>Number(n).toLocaleString("en-US",{maximumFractionDigits:8});
    const candleData=candles.map(c=>({x:c.ts*1000,o:c.open,h:c.high,l:c.low,c:c.close}));
    const volumeData=candles.map(c=>({x:c.ts*1000,y:c.volume}));
    const kData=candles.map((c,i)=>({x:c.ts*1000,y:kd[i].k}));
    const dData=candles.map((c,i)=>({x:c.ts*1000,y:kd[i].d}));
    const jData=candles.map((c,i)=>({x:c.ts*1000,y:kd[i].j}));
    const chartConfig={type:"candlestick",data:{datasets:[
      {type:"candlestick",label:"Price",data:candleData,yAxisID:"price",color:{up:"#22c55e",down:"#ef4444",unchanged:"#94a3b8"},borderColor:{up:"#22c55e",down:"#ef4444",unchanged:"#94a3b8"}},
      {type:"bar",label:"Volume",data:volumeData,yAxisID:"volume",backgroundColor:candles.map(c=>c.close>=c.open?"rgba(34,197,94,0.35)":"rgba(239,68,68,0.35)"),borderWidth:0},
      {type:"line",label:"K",data:kData,yAxisID:"kdj",borderColor:"#38bdf8",pointRadius:0,borderWidth:2},
      {type:"line",label:"D",data:dData,yAxisID:"kdj",borderColor:"#fbbf24",pointRadius:0,borderWidth:2},
      {type:"line",label:"J",data:jData,yAxisID:"kdj",borderColor:"#c084fc",pointRadius:0,borderWidth:2}
    ]},options:{responsive:false,plugins:{legend:{display:true,labels:{color:"#e2e8f0",boxWidth:14}},title:{display:true,text:symbol.toUpperCase().replace("_","/")+" · "+interval+" · PRICE / VOLUME / KDJ",color:"#f8fafc",font:{size:19}},subtitle:{display:true,text:"Last: "+fmt(latest.close)+"  |  K "+kd[kd.length-1].k.toFixed(1)+"  D "+kd[kd.length-1].d.toFixed(1)+"  J "+kd[kd.length-1].j.toFixed(1),color:"#cbd5e1"}},scales:{x:{type:"timeseries",ticks:{color:"#cbd5e1",maxTicksLimit:8},grid:{color:"#253044"}},price:{type:"linear",position:"right",ticks:{color:"#cbd5e1"},grid:{color:"#253044"}},volume:{type:"linear",position:"left",display:false,grid:{display:false},beginAtZero:true},kdj:{type:"linear",position:"left",display:false,min:0,max:100,grid:{display:false}}}}};
    const chartResponse = await fetch("https://quickchart.io/chart", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "image/png" },
      body: JSON.stringify({ width: 1100, height: 760, devicePixelRatio: 2, format: "png", backgroundColor: "#111827", version: "4", chart: chartConfig }),
      signal: AbortSignal.timeout(15000)
    });
    if (!chartResponse.ok || !(chartResponse.headers.get("content-type") || "").toLowerCase().includes("image/")) throw new Error("chart_render");
    const imageBlob = await chartResponse.blob();
    if (!imageBlob.size || imageBlob.size > 10 * 1024 * 1024) throw new Error("chart_size");
    const caption="📊 "+symbol.toUpperCase().replace("_","/")+" · LBank Spot · "+interval+"\nLast close: "+fmt(latest.close)+"\nKDJ(9): K "+kd[kd.length-1].k.toFixed(1)+" · D "+kd[kd.length-1].d.toFixed(1)+" · J "+kd[kd.length-1].j.toFixed(1)+"\nCandle: "+stamp+"\n🔒 Public market data only · No order placed";
    return {text:caption,caption,imageBlob};
  } catch (error) { return {text:"Chart generation failed (" + String(error?.message || "unknown").slice(0,80) + "). No trading action was taken."}; }
}
function chartsPage() {

  return `
📊 RoninTrBot • CHARTS
━━━━━━━━━━━━━━━━━━

Chart Engine
🟡 STANDBY

Latest Snapshot
—

Symbol
—

Timeframe
—

Entry
—

Stop Loss
—

Take Profit
—

━━━━━━━━━━━━━━━━━━

📈 Live charts will appear
when the Trading Engine
is connected.
`;
}


/* =====================================================
   STRATEGY
===================================================== */

function strategyAssetPrompt() {
  return "🧠 RoninTrBot • STRATEGY\n━━━━━━━━━━━━━━━━━━\n\nChoose the asset for live regime + signal analysis (paper-only, no orders):";
}

function strategyAssetKeyboard() {
  return {
    inline_keyboard: [
      [button("₿ BTC", "strategy_asset_btc_usdt"), button("Ξ ETH", "strategy_asset_eth_usdt"), button("◎ SOL", "strategy_asset_sol_usdt")],
      [button("✕ XRP", "strategy_asset_xrp_usdt"), button("BNB", "strategy_asset_bnb_usdt"), button("Ð DOGE", "strategy_asset_doge_usdt")],
      [button("⬅️ Back", "home")],
    ],
  };
}

function strategyTimeframePrompt(symbol) {
  return "🧠 " + symbol.toUpperCase().replace("_", "/") + "\nChoose the timeframe:";
}

function strategyTimeframeKeyboard(symbol) {
  return {
    inline_keyboard: [
      [button("15m", "strategy_tf_" + symbol + "_15m"), button("1h", "strategy_tf_" + symbol + "_1h"), button("4h", "strategy_tf_" + symbol + "_4h")],
      [button("⬅️ Back", "strategy")],
    ],
  };
}

/* Live strategy analysis, powered by the JS port of the Python core
   (workers/ronin-panel/analysis.js — CI-verified parity with app/strategy). */
async function strategyAnalysisPage(symbol = "btc_usdt", interval = "1h") {
  try {
    const now = Math.floor(Date.now() / 1000);
    const size = 100;
    const meta = { "15m": { type: "minute15", seconds: 900 }, "1h": { type: "hour1", seconds: 3600 }, "4h": { type: "hour4", seconds: 14400 } }[interval];
    if (!meta) return "Unsupported timeframe.";
    const params = new URLSearchParams({
      symbol: String(symbol).toLowerCase(), type: meta.type,
      size: String(size), time: String(now - meta.seconds * size),
    });
    const resp = await fetch("https://api.lbkex.com/v2/kline.do?" + params.toString(), {
      headers: { Accept: "application/json" }, signal: AbortSignal.timeout(7000),
    });
    if (!resp.ok) throw new Error("http");
    const payload = await resp.json();
    const rows = Array.isArray(payload) ? payload : payload?.data;
    if (!Array.isArray(rows) || rows.length < 51) throw new Error("empty");
    const candles = rows.map((row) => {
      let ts = Number(row[0]); if (ts > 1000000000000) ts = Math.floor(ts / 1000);
      return {
        timestamp: ts, open: Number(row[1]), high: Number(row[2]),
        low: Number(row[3]), close: Number(row[4]), volume: Number(row[5]),
      };
    });
    const analysis = analyzeTimeframe(interval, candles);
    const regime = classifyRegime(candles);
    const dirEmoji = analysis.direction === "long" ? "🟢 LONG" : analysis.direction === "short" ? "🔴 SHORT" : "⚪ NEUTRAL";
    const regimeEmoji = regime === "trend" ? "📈 TREND" : regime === "range" ? "〰️ RANGE" : "⚠️ HIGH VOLATILITY";
    const fmt = (n) => Number(n).toLocaleString("en-US", { maximumFractionDigits: 8 });
    return "🧠 RoninTrBot • STRATEGY (live)\n━━━━━━━━━━━━━━━━━━\n\n"
      + "Asset\n" + String(symbol).toUpperCase().replace("_", "/") + " · " + interval + "\n\n"
      + "Regime\n" + regimeEmoji + "\n\n"
      + "Signal\n" + dirEmoji + "\n\n"
      + "Score\n" + analysis.score + " / 100"
      + (analysis.eligible ? "  ✅ above threshold 70" : "  🔒 below threshold 70")
      + "\n\nRSI(14)\n" + analysis.rsi.toFixed(2) + "\n\nATR(14)\n" + fmt(analysis.atr)
      + "\n\n━━━━━━━━━━━━━━━━━━\n"
      + analysis.reasons.map((r) => "• " + r).join("\n")
      + "\n\n🔒 Analysis only · No order placed";
  } catch (error) {
    return "🧠 Strategy analysis unavailable (" + String(error?.message || "error").slice(0, 60) + "). No action taken.";
  }
}

function strategyPage() {

  return `
🧠 RoninTrBot • STRATEGY
━━━━━━━━━━━━━━━━━━

Status
🟡 STANDBY

Regime
—

Active Strategy
—

Score
— / 100

AI Filter
🔴 OFFLINE

Minimum Score
70

━━━━━━━━━━━━━━━━━━

Rule-Based Engine
WAITING
`;
}


/* =====================================================
   SETTINGS
===================================================== */

function settingsPage() {

  return `
⚙️ RoninTrBot • SETTINGS
━━━━━━━━━━━━━━━━━━

Risk / Trade
5%

Position Mode
ISOLATED

Strategy
AUTO

Minimum Score
70 / 100

AI
STANDBY

Execution
PAPER

━━━━━━━━━━━━━━━━━━

Live configuration will
be connected later.
`;
}


/* =====================================================
   MANAGEMENT
===================================================== */

function managementPage() {

  return `
🛡 RoninTrBot • MANAGEMENT
━━━━━━━━━━━━━━━━━━

Trading Engine
🔴 OFFLINE

Open Positions
0

Orders
0

Paper Trading
🟢 READY

━━━━━━━━━━━━━━━━━━

Controls

▶️ Start Engine
⛔ Stop Engine
🚨 Emergency Stop
📢 Broadcast

━━━━━━━━━━━━━━━━━━

Live execution controls
will activate after the
Trading Engine integration.
`;
}


/* =====================================================
   PAPER TRADING
===================================================== */

function paperTradingPage(role) {

  return `
🧪 RoninTrBot • PAPER TRADING
━━━━━━━━━━━━━━━━━━

Status
🟢 READY

Mode
PAPER

💰 Balance
—

📊 Equity
—

💵 Realized PnL
—

📈 Unrealized PnL
—

🎯 Win Rate
—

📊 Profit Factor
—

📉 Max Drawdown
—

📈 Positions
0

📜 Trades
0

━━━━━━━━━━━━━━━━━━

Access
${roleEmoji(role)} ${role}

Real orders are NOT sent.
`;
}


function paperStatisticsPage() {

  return `
📊 RoninTrBot • PAPER STATS
━━━━━━━━━━━━━━━━━━

Total Trades
0

Wins
0

Losses
0

Win Rate
—

Total PnL
—

Average Win
—

Average Loss
—

Profit Factor
—

Max Drawdown
—

Expectancy
—

Average Duration
—

Fees
—

Funding
—

━━━━━━━━━━━━━━━━━━

Strategy Performance
—

Regime Performance
—

Symbol Performance
—

Long / Short
—
`;
}


function paperPositionsPage() {

  return `
📈 RoninTrBot • PAPER POSITIONS
━━━━━━━━━━━━━━━━━━

Open Positions
0

━━━━━━━━━━━━━━━━━━

No paper positions.

The Paper Executor will
simulate entries, exits,
SL, TP and PnL using
market data.
`;
}


function paperHistoryPage() {

  return `
📜 RoninTrBot • PAPER HISTORY
━━━━━━━━━━━━━━━━━━

Trade History
0 trades

━━━━━━━━━━━━━━━━━━

No simulated trades yet.

When Paper Trading Engine
is connected, every trade
will be logged here.
`;
}


/* =====================================================
   HELP
===================================================== */

function helpText(role) {

  return `
🥷 RoninTrBot • HELP
━━━━━━━━━━━━━━━━━━

Your Role
${role}

/start
Open RoninTrBot panel

/panel
Open control center

/status
System status

/market [symbol] [15m|1h|4h]
Public LBank market data

/help
Show this page

━━━━━━━━━━━━━━━━━━

Architecture

LBank
↓
Market Data
↓
Analysis
↓
Strategies
↓
Risk
↓
Paper / Live Execution
↓
Charts
↓
Database
↓
Telegram

━━━━━━━━━━━━━━━━━━

RoninTrBot v${VERSION}
`;
}


/* =====================================================
   ACTION
===================================================== */

function actionMessage(
  title,
  message
) {

  return `
🥷 RoninTrBot • ${title}
━━━━━━━━━━━━━━━━━━

${message}

━━━━━━━━━━━━━━━━━━

⚠️ Current system is
still in panel mode.
`;
}


/* =====================================================
   KEYBOARDS
===================================================== */

function mainKeyboard(role) {

  const keyboard = [

    [
      button(
        "📈 Trades",
        "trades"
      ),

      button(
        "📊 Market Data",
        "charts"
      )
    ],

    [
      button(
        "⚡ System",
        "status"
      ),

      button(
        "🧠 Strategy",
        "strategy"
      )
    ],

    [
      button(
        "🧪 Paper",
        "paper"
      ),

      button(
        "⚙️ Settings",
        "settings"
      )
    ],

    [
      button(
        "🛡 Management",
        "management"
      ),

      button(
        "🔄 Refresh",
        "refresh"
      )
    ]
  ];

  if (!isAdmin(role)) {

    keyboard[2] = [

      button(
        "🧪 Paper",
        "paper"
      ),

      button(
        "📊 Paper Stats",
        "paper_stats"
      )
    ];
  }

  return {
    inline_keyboard:
      keyboard
  };
}


function statusKeyboard(role) {

  const keyboard = [

    [
      button(
        "🧪 Paper Trading",
        "paper"
      )
    ],

    [
      button(
        "📈 Trades",
        "trades"
      ),

      button(
        "🧠 Strategy",
        "strategy"
      )
    ],

    [
      button(
        "🏠 Dashboard",
        "home"
      )
    ]
  ];

  if (isAdmin(role)) {

    keyboard.splice(
      2,
      0,
      [
        button(
          "🛡 Management",
          "management"
        )
      ]
    );
  }

  return {
    inline_keyboard:
      keyboard
  };
}


function managementKeyboard(role) {

  const keyboard = [

    [
      button(
        "▶️ Start Engine",
        "start_engine"
      ),

      button(
        "⛔ Stop Engine",
        "stop_engine"
      )
    ],

    [
      button(
        "🧪 Paper Trading",
        "paper"
      )
    ],

    [
      button(
        "📢 Broadcast",
        "broadcast"
      )
    ],

    [
      button(
        "🚨 Emergency Stop",
        "emergency"
      )
    ],

    [
      button(
        "🏠 Dashboard",
        "home"
      )
    ]
  ];

  return {
    inline_keyboard:
      keyboard
  };
}


function paperTradingKeyboard(role) {

  const keyboard = [

    [
      button(
        "📊 Statistics",
        "paper_stats"
      ),

      button(
        "📈 Positions",
        "paper_positions"
      )
    ],

    [
      button(
        "📜 History",
        "paper_history"
      ),

      button(
        "🔄 Refresh",
        "paper"
      )
    ]
  ];

  if (isAdmin(role)) {

    keyboard.push([

      button(
        "⏸ Pause",
        "paper_pause"
      ),

      button(
        "▶️ Resume",
        "paper_resume"
      )
    ]);
  }

  keyboard.push([

    button(
      "🏠 Dashboard",
      "home"
    )
  ]);

  return {
    inline_keyboard:
      keyboard
  };
}


function paperBackKeyboard() {

  return {
    inline_keyboard: [

      [
        button(
          "🧪 Paper Trading",
          "paper"
        )
      ],

      [
        button(
          "🏠 Dashboard",
          "home"
        )
      ]
    ]
  };
}


function backKeyboard() {

  return {
    inline_keyboard: [

      [
        button(
          "🏠 Dashboard",
          "home"
        )
      ]
    ]
  };
}


/* =====================================================
   BROADCAST KEYBOARDS
===================================================== */

function broadcastConfirmKeyboard() {

  return {
    inline_keyboard: [

      [
        button(
          "✅ SEND",
          "broadcast_confirm"
        ),

        button(
          "❌ CANCEL",
          "broadcast_cancel"
        )
      ]
    ]
  };
}


function broadcastCancelKeyboard() {

  return {
    inline_keyboard: [

      [
        button(
          "❌ Cancel",
          "broadcast_cancel"
        )
      ],

      [
        button(
          "🏠 Dashboard",
          "home"
        )
      ]
    ]
  };
}


function button(
  text,
  callback
) {

  return {
    text,
    callback_data:
      callback
  };
}


/* =====================================================
   BROADCAST STORAGE
===================================================== */

async function registerUser(
  env,
  chatId,
  user
) {

  if (!env.USERS_KV) {
    return;
  }

  const key =
    `user:${chatId}`;

  // KV write throttle: one record per user per hour is enough for
  // broadcast targeting; every-message writes double KV cost.
  const existing = await env.USERS_KV.get(key, "json");
  if (existing?.updatedAt && Date.now() - Number(existing.updatedAt) < 3600_000) {
    return;
  }

  const data = {

    chatId:
      String(chatId),

    telegramId:
      String(
        user?.id ||
        chatId
      ),

    username:
      user?.username ||
      "",

    firstName:
      user?.first_name ||
      "",

    active: true,

    updatedAt:
      Date.now()
  };

  await env.USERS_KV.put(
    key,
    JSON.stringify(data)
  );
}


async function countUsers(env) {

  if (!env.USERS_KV) {
    return 0;
  }

  let count = 0;
  let cursor;

  do {

    const options = {
      prefix: "user:",
      limit: 1000
    };

    if (cursor) {
      options.cursor =
        cursor;
    }

    const result =
      await env.USERS_KV.list(
        options
      );

    count +=
      result.keys.length;

    cursor =
      result.list_complete
        ? undefined
        : result.cursor;

  } while (cursor);

  return count;
}


async function getUsers(env) {

  const users = [];

  let cursor;

  do {

    const options = {
      prefix: "user:",
      limit: 1000
    };

    if (cursor) {
      options.cursor =
        cursor;
    }

    const result =
      await env.USERS_KV.list(
        options
      );

    for (
      const item of result.keys
    ) {

      const data =
        await env.USERS_KV.get(
          item.name,
          "json"
        );

      if (data?.chatId) {

        users.push(data);
      }
    }

    cursor =
      result.list_complete
        ? undefined
        : result.cursor;

  } while (cursor);

  return users;
}


/* =====================================================
   BROADCAST ENGINE
===================================================== */

async function broadcastMessage(
  env,
  bot,
  message
) {

  const users =
    await getUsers(env);

  let sent = 0;
  let failed = 0;

  const failedIds = [];

  for (
    const user of users
  ) {

    try {

      const result =
        await bot.sendMessage(
          user.chatId,
          message
        );

      if (result?.ok) {

        sent++;

      } else {

        failed++;

        failedIds.push(
          user.chatId
        );

        // Remove the user only when Telegram reports 403 (bot blocked).
        // Transient failures (429/5xx/network) must not silently shrink
        // the audience.
        if (result?.error_code === 403) {
          await env.USERS_KV.delete(
            `user:${user.chatId}`
          );
        }
      }

    } catch (error) {

      failed++;

      failedIds.push(
        user.chatId
      );

      // Network/code error: keep the user; it may be transient.
    }

    /*
      ~25 messages/sec
      to stay below Telegram's
      normal broadcast limit.
    */

    await sleep(40);
  }

  return {

    total:
      users.length,

    sent,

    failed,

    failedIds
  };
}


function sleep(ms) {

  return new Promise(
    resolve =>
      setTimeout(
        resolve,
        ms
      )
  );
}


function broadcastPreview(
  message,
  recipients
) {

  return `
📢 RoninTrBot • BROADCAST PREVIEW
━━━━━━━━━━━━━━━━━━

${message}

━━━━━━━━━━━━━━━━━━

👥 Recipients
${recipients}

⚠️ This message will be
sent to all registered users.

━━━━━━━━━━━━━━━━━━
`;
}


/* =====================================================
   HELPERS
===================================================== */

function roleEmoji(role) {

  if (role === "OWNER") {
    return "👑";
  }

  if (role === "ADMIN") {
    return "🛡️";
  }

  return "👤";
}


function json(
  data,
  status = 200
) {

  return new Response(
    JSON.stringify(
      data,
      null,
      2
    ),
    {
      status,
      headers: {
        "Content-Type":
          "application/json"
      }
    }
  );
}