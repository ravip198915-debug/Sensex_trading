API_KEY ="fjerr9ltddn6pciy"
ACCESS_TOKEN ="J1kjPC1lbImMOB7X8jm837yXas1Lc57E"

# ==========================================================
# ULTRA-PRO OPTION BUYING – LIVE TRADE VERSION (FINAL)
# (LOGIC, FORMAT, CONDITIONS UNCHANGED)
# ==========================================================

import asyncio
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from kiteconnect import KiteConnect, KiteTicker
from datetime import datetime, date, time as dtime, timedelta
import time, winsound, threading, sys
from colorama import init
import logging
import json
import os

STATE_FILE = "trade_state.json"

logging.getLogger("websocket").setLevel(logging.CRITICAL)
logging.getLogger("kiteconnect").setLevel(logging.CRITICAL)
logging.getLogger("kiteconnect.ticker").setLevel(logging.ERROR)

init(autoreset=True)

GREEN="\033[92m"; RED="\033[91m"; YELLOW="\033[93m"
BLUE="\033[94m"; RESET="\033[0m"


# ================= TELEGRAM =================

import telebot

BOT_TOKEN = "8565948222:AAHym1kW4PCTMVAcPvZNLpKjzpsbdDWryjg"
CHAT_ID = 1412356698


bot = telebot.TeleBot(BOT_TOKEN, threaded=True)


def send_telegram(msg):


    try:
        bot.send_message(CHAT_ID, msg)
    except Exception as e:
        print("Telegram error:", e)

# ================= TELEGRAM COMMANDS =================

@bot.message_handler(commands=['status'])
def bot_status(message):

    if message.chat.id != CHAT_ID:
        return

    if trade_open:

        entry = trade.get("prem_entry",0)
        sl = trade.get("prem_sl",0)
        target = trade.get("prem_target",0)

        msg = (
            f"📊 BOT STATUS\n"
            f"Active Trade: {ACTIVE_SYMBOL}\n"
            f"Entry: {entry}\n"
            f"SL: {sl}\n"
            f"Target: {target}"
        )

    else:

        msg = "📊 BOT STATUS\nNo active trade"

    bot.reply_to(message, msg)


@bot.message_handler(commands=['stopbot'])
def stop_bot(message):

    global SCRIPT_RUNNING

    if message.chat.id != CHAT_ID:
        return

    SCRIPT_RUNNING = False

    try:
        kws.close()
    except:
        pass

    bot.reply_to(message,"🛑 Bot stopped")

def telegram_polling():
    while True:
        try:
            bot.infinity_polling(
                timeout=10,
                long_polling_timeout=5
            )
        except Exception as e:
            print("Telegram reconnect:", str(e)[:100])
            time.sleep(3)


threading.Thread(target=telegram_polling, daemon=True).start()

#start

@bot.message_handler(commands=['start'])
def start_command(message):

    if message.chat.id != CHAT_ID:
        return

    msg = (
        "🤖 OPTION BUYING BOT ONLINE\n"
        f"Date: {date.today()}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')}"
    )

    bot.reply_to(message, msg)

#/exit

@bot.message_handler(commands=['exit'])
def manual_exit(message):

    global trade_open, ENTRY_LOCK

    if message.chat.id != CHAT_ID:
        return

    if trade_open:

        place_live_exit(ACTIVE_SYMBOL)

        trade_open = False
        ENTRY_LOCK = False

        bot.reply_to(message, "⚠ Manual exit executed")

    else:
        bot.reply_to(message, "No active trade")


@bot.message_handler(commands=['forceexit'])
def force_exit(message):

    global trade_open, ENTRY_LOCK, SCRIPT_RUNNING

    if message.chat.id != CHAT_ID:
        return

    if trade_open:

        try:
            place_live_exit(ACTIVE_SYMBOL)
        except:
            pass

        trade_open = False
        ENTRY_LOCK = False
        save_state()

        bot.reply_to(message, "🚨 FORCE EXIT EXECUTED")

    else:
        bot.reply_to(message, "No active trade")

#/pnl

@bot.message_handler(commands=['pnl'])
def pnl_status(message):

    if message.chat.id != CHAT_ID:
        return

    if trade_open and option_ltp and "prem_entry" in trade:

        entry = trade["prem_entry"]
        current = option_ltp

        points = round(current - entry, 2)
        rupees = round(points * LOT_SIZE, 2)

        msg = (
            "💰 LIVE TRADE\n"
            f"Symbol: {ACTIVE_SYMBOL}\n"
            f"Entry: {entry}\n"
            f"Current: {current}\n"
            f"P&L: ₹{rupees}"
        )

    else:
        msg = "No active trade"

    bot.reply_to(message, msg)



# ================= CONFIG =================
MODE="LIVE"
PRODUCT="MIS"

ORDER_TYPE="MARKET"

SPOT_TOKEN=265
LOT_SIZE=20

PREM_SL_PTS=60
PREM_TGT_PTS=100

LAST_ENTRY_TIME=dtime(15,15)
FORCE_EXIT_TIME=dtime(15,20)



# ================= GLOBALS =================
spot_ltp=None
option_ltp=None
trade_open=False
ACTIVE_OPTION_TOKEN=None
ACTIVE_SYMBOL=None
ORDER_PLACED=False
BLOCK_MSG_SHOWN=False
day_closed=False
SCRIPT_RUNNING=True
ENTRY_LOCK = False
LAST_TICK_TIME = time.time()
ENTRY_TIME = None

AUTO_SIGNAL="NO TRADE"
allowed_side=None
SIGNAL_LOCKED = False
MA_SIDE=None
CPR_TYPE=None
CPR_WIDE_THRESHOLD=0.6
NARROW_THRESHOLD=0.25

trade={}
candle={"high":None,"low":None}
candle_done=False

TRADE_COUNT=0
FIRST_TRADE_SIDE=None
FIRST_TRADE_RESULT=None
DAY_MODE=None

PAPER_POSITION_QTY=0

# ================= RISK CONTROL =================

DAY_PNL = 0
MAX_DAILY_LOSS = -2500

# ================= KITE =================
kite=KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

print("Token test:",kite.profile()["user_name"])
print("Downloading instruments...")
INSTRUMENTS=kite.instruments("BFO")
print("BFO instruments loaded")

# ================= HEADER =================
def print_header():
    print(f"{GREEN}MODE: OPTION BUYING SCRIPT - LIVE TRADE{RESET}")
    print(f"{BLUE}Execution Date: {date.today()} | {datetime.now().strftime('%H:%M:%S')}{RESET}")

send_telegram(
f"🚀 OPTION BUYING BOT STARTED\n"
f"Date: {date.today()}\n"
f"Time: {datetime.now().strftime('%H:%M:%S')}"
)

# ================= SOUND =================
def sound_entry(): winsound.Beep(1200,300)
def sound_sl(): winsound.Beep(600,700)
def sound_target(): winsound.Beep(1500,250)

# ================= CPR + AUTO SIGNAL (FINAL CLEAN VERSION) =================
def calculate_auto_signal():

    global AUTO_SIGNAL,allowed_side,MA_SIDE,CPR_TYPE,DAY_MODE,SIGNAL_LOCKED

    if SIGNAL_LOCKED:
        return

    today = date.today()

# ===== PREVIOUS TRADING DAY (SAFE VERSION) =====
    # ===== PREVIOUS TRADING DAY (SAFE VERSION) =====
    data_prev = []
    prev_used = None

    for i in range(1,6):

        prev = today - timedelta(days=i)

        # Skip weekends
        if prev.weekday() >= 5:
            continue

        try:
            data_prev = kite.historical_data(
                SPOT_TOKEN,
                datetime.combine(prev, dtime(9,15)),
                datetime.combine(prev, dtime(15,30)),
                "5minute"
            )
        except:
            data_prev = []

        if data_prev:
            prev_used = prev
            break


    if not data_prev:
        print("Previous day data not available — AUTO SIGNAL skipped")
        return

    print(f"Using previous trading day data: {prev_used}")

    PDH = max(i["high"] for i in data_prev)
    PDL = min(i["low"] for i in data_prev)
    PDC = data_prev[-1]["close"]


    # ===== CPR CALCULATION =====
    pivot = (PDH + PDL + PDC) / 3
    BC = (PDH + PDL) / 2
    TC = (pivot - BC) + pivot

    cpr_width = abs(TC - BC) / pivot * 100


    # ===== CPR TYPE (MATCHES EXCEL STRUCTURE) =====
    if cpr_width < NARROW_THRESHOLD:
        CPR_TYPE = "NARROW"
    elif cpr_width > CPR_WIDE_THRESHOLD:
        CPR_TYPE = "WIDE"
    else:
        CPR_TYPE = "NORMAL"


    # ===== DAILY MA20 =====
    hist = kite.historical_data(
        SPOT_TOKEN,
        today - timedelta(days=60),
        today,
        "day"
    )

    closes = [i["close"] for i in hist[:-1]]

    if len(closes) < 20:
        print("Not enough data for MA20")
        return

    ma20 = sum(closes[-20:]) / 20
    MA_SIDE = "Above" if spot_ltp > ma20 else "Below"

    # ===== AUTO SIGNAL (EXACT EXCEL LOGIC) =====

    if CPR_TYPE == "WIDE":
        AUTO_SIGNAL = "NO TRADE"
        allowed_side = None
        DAY_MODE = "NOTRADE"

    else:

        if (PDC > ma20) and (cpr_width < NARROW_THRESHOLD) and (PDC > TC):
            AUTO_SIGNAL = "CE BUY DAY"
            allowed_side = "BOTH"
            DAY_MODE = "BUYDAY"

        elif (PDC < ma20) and (cpr_width < NARROW_THRESHOLD) and (PDC < BC):
            AUTO_SIGNAL = "PE BUY DAY"
            allowed_side = "BOTH"
            DAY_MODE = "BUYDAY"

        else:
            AUTO_SIGNAL = "NO TRADE"
            DAY_MODE = "NOTRADE"

            if MA_SIDE == "Above":
                allowed_side = None
            else:
                allowed_side = "PE"

    print(f"[AUTO SIGNAL] CPR={CPR_TYPE} | 20MA={MA_SIDE} | SIGNAL={AUTO_SIGNAL} | Allowed={allowed_side}")

    SIGNAL_LOCKED = True

    send_telegram(
    f"📊 AUTO SIGNAL\n"
    f"CPR: {CPR_TYPE}\n"
    f"20MA: {MA_SIDE}\n"
    f"Signal: {AUTO_SIGNAL}\n"
    f"Allowed: {allowed_side}"
    )

#=================get_atm_from_expiry()================

def get_atm_from_expiry(spot, side, expiry):

    options = [
        i for i in INSTRUMENTS
        if i["name"] == "SENSEX"
        and i["expiry"] == expiry
        and i["instrument_type"] == side
    ]

    if not options:
        return None, None

    # ✅ Correct strike comparison (VERY IMPORTANT)
    atm = min(options, key=lambda x: abs(x["strike"] - spot))

    symbol = atm["exchange"] + ":" + atm["tradingsymbol"]

    print(f"Spot: {spot}")
    print(f"Selected Strike: {atm['strike']}")
    print(f"Expiry: {expiry}")
    print(f"Symbol: {symbol}")

    return symbol, atm["instrument_token"]

#==============get_atm_option() (NEXT WEEK → CURRENT WEEK LOGIC)========

def get_atm_option(spot, side):

    today = date.today()

    expiries = sorted(set(
        i["expiry"] for i in INSTRUMENTS
        if i["name"] == "SENSEX" and i["expiry"] >= today
    ))

    if not expiries:
        return None

    current_expiry = expiries[0]
    next_expiry = expiries[1] if len(expiries) > 1 else expiries[0]

    # ================= NEXT WEEK FIRST =================
    sym, tok = get_atm_from_expiry(spot, side, next_expiry)

    if sym:
        print(f"Checking NEXT WEEK liquidity...")
        if is_liquid(sym):
            print(f"{GREEN}Using NEXT WEEK ATM{RESET}")
            return sym, tok
        else:
            print(f"{YELLOW}Next week illiquid, switching...{RESET}")

    # ================= CURRENT WEEK =================
    sym, tok = get_atm_from_expiry(spot, side, current_expiry)

    if sym:
        print(f"Checking CURRENT WEEK liquidity...")
        if is_liquid(sym):
            print(f"{YELLOW}Using CURRENT WEEK ATM{RESET}")
            return sym, tok

    print(f"{RED}No liquid ATM found{RESET}")
    return None



#============= is_liquid() ============

def is_liquid(symbol):

    try:
        q = kite.quote([symbol]).get(symbol)

        if not q:
            print(f"{RED}No quote data for {symbol}{RESET}")
            return False

        depth = q.get("depth", {})

        buy_depth = depth.get("buy", [])
        sell_depth = depth.get("sell", [])

        # ✅ Depth safety check
        if not buy_depth or not sell_depth:
            print(f"{YELLOW}No market depth → {symbol}{RESET}")
            return False

        bid = buy_depth[0].get("price", 0)
        ask = sell_depth[0].get("price", 0)
        oi = q.get("oi", 0)

        # ✅ Price safety
        if bid == 0 or ask == 0:
            print(f"{YELLOW}Invalid bid/ask → {symbol}{RESET}")
            return False

        spread = ask - bid

        print(f"Checking Liquidity → {symbol} | OI:{oi} | Spread:{spread:.2f}")

        # ✅ Liquidity condition (you can tune this)
        if oi >= 1000 and spread <= 3:
            return True

        print(f"{YELLOW}Illiquid → OI:{oi}, Spread:{spread:.2f}{RESET}")
        return False

    except Exception as e:
        print(f"{RED}Liquidity error for {symbol}: {e}{RESET}")
        return False


# ================= LIVE ORDER SYSTEM =================

def place_live_buy(sym):

    global trade_open, ENTRY_LOCK

    try:

        exchange, tradingsymbol = sym.split(":")

        order_id = kite.place_order(
            variety = kite.VARIETY_REGULAR,
            exchange = exchange,
            tradingsymbol = tradingsymbol,
            transaction_type = kite.TRANSACTION_TYPE_BUY,
            quantity = LOT_SIZE,
            product = PRODUCT,
            order_type = ORDER_TYPE
        )

        print(f"{GREEN}[LIVE BUY] {sym} | Qty={LOT_SIZE} | OrderID={order_id}{RESET}")

        # Wait until order is filled
        fill_price = None

        for i in range(10):

            time.sleep(1)

            try:
                order_details = kite.order_history(order_id)
                status = order_details[-1]["status"]

                if status == "COMPLETE":

                    fill_price = order_details[-1]["average_price"]
                    trade_open = True
                    ENTRY_LOCK = True

                    global ENTRY_TIME
                    ENTRY_TIME = time.time()
                    
                    break

                if status == "REJECTED":

                    print("Order Rejected")
                    trade_open = False
                    ENTRY_LOCK = False
                    return

            except:
                pass


        if fill_price is None:

            print("Order not filled yet")
            send_telegram("⚠ Order placed but not filled")

            trade_open = False
            ENTRY_LOCK = False
            return


        print(f"{GREEN}Order Filled Price: {fill_price}{RESET}")

        trade["prem_entry"] = fill_price
        trade["prem_sl"] = fill_price - PREM_SL_PTS
        trade["prem_target"] = fill_price + PREM_TGT_PTS

        send_telegram(
            f"🟢 LIVE BUY FILLED\n"
            f"Symbol: {sym}\n"
            f"Entry Price: {fill_price}\n"
            f"SL: {trade['prem_sl']}\n"
            f"Target: {trade['prem_target']}"
        )

    except Exception as e:
        print("LIVE BUY FAILED:", e)


def place_live_exit(sym):

    try:

        exchange, tradingsymbol = sym.split(":")

        order_id = kite.place_order(
            variety = kite.VARIETY_REGULAR,
            exchange = exchange,
            tradingsymbol = tradingsymbol,
            transaction_type = kite.TRANSACTION_TYPE_SELL,
            quantity = LOT_SIZE,
            product = PRODUCT,
            order_type = ORDER_TYPE
        )

        print(f"{RED}[LIVE EXIT] {sym} | OrderID={order_id}{RESET}")

        send_telegram(
            f"🔴 LIVE EXIT\n"
            f"Symbol: {sym}\n"
            f"OrderID: {order_id}"
        )

    except Exception as e:
        print("LIVE EXIT FAILED:", e)

# ================= FETCH =================
def fetch_spot():
    global spot_ltp
    try:
        spot_ltp=kite.ltp(["BSE:SENSEX"])["BSE:SENSEX"]["last_price"]
    except: pass

def fetch_option_ltp():
    global option_ltp
    try:
        ltp_data = kite.ltp([ACTIVE_SYMBOL])
        option_ltp = ltp_data[ACTIVE_SYMBOL]["last_price"]
    except Exception as e:
        print("Option LTP fetch error:", e)

# ================= 9:30 CANDLE =================
def fetch_930_candle():
    global candle_done
    if candle_done: return

    today=date.today()
    data=kite.historical_data(
        SPOT_TOKEN,
        datetime.combine(today,dtime(9,30)),
        datetime.combine(today,dtime(9,35)),
        "5minute"
    )

    if data:
        candle["high"]=data[0]["high"]
        candle["low"]=data[0]["low"]
        candle_done=True
        print(f"{GREEN}Fetched 9:30 candle{RESET}")
        send_telegram("✅ 9:30 Candle Captured — Bot Running")
        calculate_auto_signal()


#===================SAVE FUNCTION================
def save_state():
    state = {
        "trade_open": trade_open,
        "ACTIVE_SYMBOL": ACTIVE_SYMBOL,
        "ACTIVE_OPTION_TOKEN": ACTIVE_OPTION_TOKEN,
        "trade": trade,
        "TRADE_COUNT": TRADE_COUNT,
        "FIRST_TRADE_RESULT": FIRST_TRADE_RESULT,
        "FIRST_TRADE_SIDE": FIRST_TRADE_SIDE,
        "DAY_MODE": DAY_MODE
    }

    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

    print("State Saved")

# ================= STATE LOAD FUNCTION =================
def load_state():
    global trade_open, ACTIVE_SYMBOL, ACTIVE_OPTION_TOKEN
    global trade, TRADE_COUNT, FIRST_TRADE_RESULT
    global FIRST_TRADE_SIDE, DAY_MODE

    if not os.path.exists(STATE_FILE):
        return

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    trade_open = state.get("trade_open", False)
    ACTIVE_SYMBOL = state.get("ACTIVE_SYMBOL")
    ACTIVE_OPTION_TOKEN = state.get("ACTIVE_OPTION_TOKEN")
    trade = state.get("trade", {})
    TRADE_COUNT = state.get("TRADE_COUNT", 0)
    FIRST_TRADE_RESULT = state.get("FIRST_TRADE_RESULT")
    FIRST_TRADE_SIDE = state.get("FIRST_TRADE_SIDE")
    DAY_MODE = state.get("DAY_MODE")

    print("Previous Session Restored")


# ================= CORE ENGINE =================
def on_ticks(ws, ticks):

    global LAST_TICK_TIME
    
    

    global trade_open,ACTIVE_OPTION_TOKEN,ACTIVE_SYMBOL,ENTRY_LOCK
    global ORDER_PLACED,spot_ltp,option_ltp,day_closed
    global TRADE_COUNT,FIRST_TRADE_SIDE,FIRST_TRADE_RESULT
    global BLOCK_MSG_SHOWN,SCRIPT_RUNNING,LAST_TICK_TIME
    LAST_TICK_TIME = time.time()

    try:

        if not SCRIPT_RUNNING:
            return

        if ws is None:
            return

        now = datetime.now().time()

        # ===== PRICE UPDATE =====
        for t in ticks:

            if t.get("instrument_token") == SPOT_TOKEN:
                spot_ltp = t["last_price"]

            if ACTIVE_OPTION_TOKEN and t.get("instrument_token") == ACTIVE_OPTION_TOKEN:
                option_ltp = t["last_price"]

        LAST_TICK_TIME = time.time()

        if not candle_done or day_closed:
            return


        # ======================================================
        # ================= FORCE EXIT =========================
        # ======================================================
        if now >= FORCE_EXIT_TIME:

            if trade_open:

                place_live_exit(ACTIVE_SYMBOL)

                trade_open = False
                ENTRY_LOCK = False

                save_state()

                print("Force Exit Time Hit")

                fetch_option_ltp()
                exit_price = option_ltp
                entry_price = trade.get("prem_entry",0)

                points_pnl = round(exit_price-entry_price,2)
                rupees_pnl = round(points_pnl*LOT_SIZE,2)

                if TRADE_COUNT == 0:
                    if points_pnl < 0:
                        FIRST_TRADE_RESULT = "SL"
                    else:
                        FIRST_TRADE_RESULT = "TARGET"

                side_text = ACTIVE_SYMBOL[-2:]
                pnl_color = GREEN if points_pnl >= 0 else RED

                print(f"{YELLOW}Entry Premium : {entry_price}{RESET}")
                print(f"{YELLOW}Exit Premium  : {exit_price}{RESET}")
                print(f"{pnl_color}P&L : {points_pnl} pts | ₹{rupees_pnl}{RESET}")

                print(f"{BLUE}[TRADE#{TRADE_COUNT+1}] {side_text} | "
                      f"Entry={entry_price} | Exit={exit_price} | "
                      f"{points_pnl:+}pts | ₹{rupees_pnl} | FORCE_EXIT{RESET}")

                TRADE_COUNT += 1
                save_state()

            print("Market closed — stopping bot")

            day_closed = True
            SCRIPT_RUNNING = False
            kws.close()
            return


        # ======================================================
        # ================= ENTRY LOGIC ========================
        # ======================================================
        if not trade_open and not ENTRY_LOCK and spot_ltp and now < LAST_ENTRY_TIME:

            if DAY_MODE == "NOTRADE" and TRADE_COUNT >= 1:
                return

            if DAY_MODE == "BUYDAY" and TRADE_COUNT == 1 and FIRST_TRADE_RESULT != "SL":
                return

            side = None

            if spot_ltp >= candle["high"] + 5:

                if allowed_side in ["CE", "BOTH"]:
                    side = "CE"

                else:
                    if not BLOCK_MSG_SHOWN:
                        print(f"{YELLOW}ENTRY BLOCKED — CE not allowed{RESET}")
                        BLOCK_MSG_SHOWN = True
                    return

            elif spot_ltp <= candle["low"] - 5:

                if allowed_side in ["PE", "BOTH"]:
                    side = "PE"

                else:
                    if not BLOCK_MSG_SHOWN:
                        print(f"{YELLOW}ENTRY BLOCKED — PE not allowed{RESET}")
                        BLOCK_MSG_SHOWN = True
                    return

            else:
                return


            if DAY_MODE == "BUYDAY" and TRADE_COUNT == 1:
                if side == FIRST_TRADE_SIDE:
                    return


            opt = get_atm_option(spot_ltp, side)

            if opt is None:

                if not BLOCK_MSG_SHOWN:
                    print(f"{RED}ATM strike not available — waiting...{RESET}")
                    BLOCK_MSG_SHOWN = True
                return

            BLOCK_MSG_SHOWN = False

            sym, tok = opt

            ACTIVE_OPTION_TOKEN = tok
            ACTIVE_SYMBOL = sym

            ws.subscribe([tok])
            ws.set_mode(ws.MODE_LTP, [tok])


            trade.clear()
            save_state()

            if TRADE_COUNT == 0:
                FIRST_TRADE_SIDE = side

            place_live_buy(sym)
            sound_entry()


        # ======================================================
        # ================= TRADE MANAGEMENT ===================
        # ======================================================
        if trade_open and "prem_sl" in trade:

            if ENTRY_TIME is not None and time.time() - ENTRY_TIME < 3:
                return

            if option_ltp is None:
                return

            if "prem_sl" not in trade:

                trade["prem_sl"] = trade["prem_entry"] - PREM_SL_PTS
                trade["prem_target"] = trade["prem_entry"] + PREM_TGT_PTS

                save_state()

                if ACTIVE_SYMBOL.endswith("CE"):
                    spot_sl = spot_ltp - 40
                    spot_target = spot_ltp + 40
                else:
                    spot_sl = spot_ltp + 40
                    spot_target = spot_ltp - 40


                print(f"{BLUE}PAPER BUY {ACTIVE_SYMBOL[-2:]} (Trade-{TRADE_COUNT+1}) {ACTIVE_SYMBOL}{RESET}")
                print(f"Time: {datetime.now().strftime('%H:%M:%S')}")

                print(f"Spot Entry : {round(spot_ltp,2)} | "
                      f"Spot SL : {round(spot_sl,2)} | "
                      f"Spot Target : {round(spot_target,2)}")

                print(f"{GREEN}Premium Entry: {round(trade['prem_entry'],2)} | "
                      f"Premium SL: {round(trade['prem_sl'],2)} | "
                      f"Premium Target: {round(trade['prem_target'],2)}{RESET}")

                # TELEGRAM ENTRY ALERT
                send_telegram(
                    f"🟢 ENTRY ALERT\n"
                    f"Trade: {TRADE_COUNT+1}\n"
                    f"Symbol: {ACTIVE_SYMBOL}\n"
                    f"Date: {date.today()}\n"
                    f"Time: {datetime.now().strftime('%H:%M:%S')}\n"
                    f"Entry Premium: {round(trade['prem_entry'],2)}\n"
                    f"SL: {round(trade['prem_sl'],2)}\n"
                    f"Target: {round(trade['prem_target'],2)}"
                )

                return


            if option_ltp <= trade["prem_sl"]:

                reason = "SL"
                sound_sl()

            elif option_ltp >= trade["prem_target"]:

                reason = "TARGET"
                sound_target()

            else:
                return


            place_live_exit(ACTIVE_SYMBOL)

            print(f"{YELLOW}Exit Trade-{TRADE_COUNT+1} - {reason}{RESET}")
            print(f"Spot Exit : {round(spot_ltp,2)} | Premium Exit : {round(option_ltp,2)}")

            points_pnl = round(option_ltp - trade["prem_entry"], 2)
            rupees_pnl = round(points_pnl * LOT_SIZE, 2)

            global DAY_PNL
            DAY_PNL += rupees_pnl

            # Daily loss protection
            if DAY_PNL <= MAX_DAILY_LOSS:

                print("⚠ MAX DAILY LOSS HIT — STOPPING BOT")

                send_telegram(
                    f"⚠ MAX DAILY LOSS HIT\n"
                    f"Loss: ₹{DAY_PNL}\n"
                    f"Bot stopped for safety"
                )
             
                day_closed = True
                SCRIPT_RUNNING = False
                kws.close()
                return

            pnl_color = GREEN if rupees_pnl >= 0 else RED
            print(f"{pnl_color}Trade {TRADE_COUNT+1} P&L : {rupees_pnl}{RESET}")

            send_telegram(
                f"🔴 EXIT ALERT\n"
                f"Trade: {TRADE_COUNT+1}\n"
                f"Symbol: {ACTIVE_SYMBOL}\n"
                f"Reason: {reason}\n"
                f"Exit Premium: {round(option_ltp,2)}\n"
                f"P&L: ₹{rupees_pnl}"
            )

            trade_open = False
            ENTRY_LOCK = False
            ORDER_PLACED = False
            TRADE_COUNT += 1

            save_state()

            if TRADE_COUNT == 1:
                FIRST_TRADE_RESULT = reason


            if DAY_MODE == "NOTRADE":

                day_closed = True
                print(f"{GREEN}DAY COMPLETED{RESET}")
                SCRIPT_RUNNING = False
                kws.close()
                return


            if DAY_MODE == "BUYDAY":

                if reason == "TARGET":

                    day_closed = True
                    print(f"{GREEN}DAY COMPLETED{RESET}")
                    SCRIPT_RUNNING = False
                    kws.close()
                    return

                if TRADE_COUNT >= 2:

                    day_closed = True
                    SCRIPT_RUNNING = False
                    kws.close()
                    return

    except Exception as e:

        print("Tick Processing Error:", e)
# ================= START =================
print_header()

load_state()

# Reset state if new day
if os.path.exists(STATE_FILE):
    file_date = datetime.fromtimestamp(os.path.getmtime(STATE_FILE)).date()
    if file_date != date.today():
        print("New trading day detected — resetting state")
        os.remove(STATE_FILE)
        trade_open=False
        TRADE_COUNT=0   

# ================= CRASH RECOVERY PRICE CHECK =================
if trade_open and ACTIVE_OPTION_TOKEN:

    try:
        ltp = kite.ltp([ACTIVE_OPTION_TOKEN])
        current_price = list(ltp.values())[0]["last_price"]

        print(f"Recovery Check Price: {current_price}")

        if current_price <= trade.get("prem_sl", -1):
            print("Recovered SL condition detected")
            trade_open = False
            save_state()

        elif current_price >= trade.get("prem_target", 999999):
            print("Recovered TARGET condition detected")
            trade_open = False
            save_state()

    except Exception as e:
        print("Recovery price check failed:", e)


# ===== websocket handlers =====

def on_connect(ws, response):
    print("WebSocket connected")

    # Subscribe spot
    ws.subscribe([SPOT_TOKEN])
    ws.set_mode(ws.MODE_LTP, [SPOT_TOKEN])

    # Restore active trade subscription
    if ACTIVE_OPTION_TOKEN:
        ws.subscribe([ACTIVE_OPTION_TOKEN])
        ws.set_mode(ws.MODE_LTP, [ACTIVE_OPTION_TOKEN])
        print("Restored active trade subscription")


def on_close(ws, code, reason):
    print(f"WebSocket Closed | Code:{code} | Reason:{reason}")


def on_error(ws, code, reason):
    print(f"WebSocket Error | Code:{code} | Reason:{reason}")


# ===== websocket init =====

kws = KiteTicker(API_KEY, ACCESS_TOKEN)
kws.on_ticks = on_ticks
kws.on_connect = on_connect
kws.on_close = on_close
kws.on_error = on_error 

# ===== AUTO RECONNECT START =====

def start_kws():
    while SCRIPT_RUNNING:
        try:
            kws.connect(threaded=True)
            while SCRIPT_RUNNING:
                time.sleep(1)
        except Exception as e:
            print("Reconnect error:", e)
            time.sleep(5)

threading.Thread(target=start_kws, daemon=True).start()





# ================= HEARTBEAT =================
def heartbeat():
    while SCRIPT_RUNNING:
        try:
            fetch_spot()

            now = datetime.now().time()

            # ✅ Only run during market hours
            if dtime(9,30) <= now <= dtime(15,30):
                if not candle_done and now > dtime(9,35):
                    fetch_930_candle()

        except Exception as e:
            print("Heartbeat error:", e)

        time.sleep(1)

threading.Thread(target=heartbeat, daemon=True).start()

#============Watchdog============

def tick_watchdog():
    global LAST_TICK_TIME

    while SCRIPT_RUNNING:
        try:
            now_time = datetime.now().time()

            # ✅ Run watchdog ONLY during market hours
            if dtime(9,15) <= now_time <= dtime(15,30):

                now = time.time()

                if now - LAST_TICK_TIME > 10:
                    print("⚠ No ticks detected — forcing reconnect")

                    try:
                        kws.close()
                    except:
                        pass

                    LAST_TICK_TIME = time.time()

        except Exception as e:
            print("Watchdog error:", e)

        time.sleep(5)


threading.Thread(target=tick_watchdog, daemon=True).start()



# ================= KEEP SCRIPT ALIVE =================
while SCRIPT_RUNNING:
    time.sleep(1)

print("Script exited cleanly")
sys.exit(0)