"""SENSEX options CPR + Auto-signal breakout bot (5-minute).

Production-focused reference implementation for KiteConnect/KiteTicker.
- Strategy: CPR + 20MA auto signal.
- Entry: 9:30 candle breakout (+/-5 points on spot).
- Risk: option premium SL 60, target 100.
- Execution: ATM options, prefer next-week expiry, fallback current-week.
- Filters: OI >= 1000 and spread <= 3.
- State: JSON persistence + crash recovery.
- Alerts: Telegram entry/exit/pnl/status.
- Timezone: Asia/Kolkata (no system-time dependency).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect, KiteTicker

try:
    import telebot
except ImportError:  # Optional dependency
    telebot = None


# =========================
# CONFIG
# =========================
IST = ZoneInfo("Asia/Kolkata")
STATE_FILE = "trade_state.json"
LOG_LEVEL = os.getenv("BOT_LOG_LEVEL", "INFO").upper()

API_KEY = os.getenv("KITE_API_KEY", "")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0")) if os.getenv("TELEGRAM_CHAT_ID") else 0

UNDERLYING = "SENSEX"
SPOT_SYMBOL = "BSE:SENSEX"
PRODUCT = "MIS"
ORDER_VAR = "regular"
LOT_SIZE = int(os.getenv("LOT_SIZE", "20"))

CPR_NARROW_MAX = 0.2
CPR_NORMAL_MAX = 0.4
AUTO_SIGNAL_CPR_MAX = 0.25

PREMIUM_SL_POINTS = 60.0
PREMIUM_TARGET_POINTS = 100.0

ENTRY_BUFFER = 5.0
MIN_OI = 1000
MAX_SPREAD = 3.0

MARKET_OPEN = dt_time(9, 15)
CANDLE_TIME = dt_time(9, 30)
CANDLE_READY_AFTER = dt_time(9, 35)
LAST_ENTRY_TIME = dt_time(15, 15)
FORCE_EXIT_TIME = dt_time(15, 20)
MARKET_CLOSE = dt_time(15, 30)

POLL_SLEEP = 1.0


# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("sensex_cpr_bot")


@dataclass
class Trade:
    side: str  # CE / PE
    symbol: str
    token: int
    entry_price: float
    sl_price: float
    target_price: float
    qty: int
    entry_time_iso: str


@dataclass
class BotState:
    trade_date: str = ""
    cpr_type: str = ""
    cpr_width: float = 0.0
    pivot: float = 0.0
    bc: float = 0.0
    tc: float = 0.0
    ma20: float = 0.0
    ma_side: str = ""  # Above / Below
    auto_signal: str = "NO TRADE"
    day_mode: str = "NOTRADE"  # BUYDAY / NOTRADE
    allowed_side: Optional[str] = None  # CE / PE / BOTH / None

    candle_high: Optional[float] = None
    candle_low: Optional[float] = None
    candle_ready: bool = False

    trades_taken: int = 0
    first_trade_side: Optional[str] = None
    first_trade_result: Optional[str] = None  # SL / TARGET

    active_trade: Optional[Trade] = None

    day_closed: bool = False
    entry_lock: bool = False


class TelegramNotifier:
    def __init__(self) -> None:
        self.enabled = bool(telebot and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        self.bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=True) if self.enabled else None

    def send(self, msg: str) -> None:
        if not self.enabled:
            return
        try:
            self.bot.send_message(TELEGRAM_CHAT_ID, msg)
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)

    def register_handlers(self, app: "SensexCPRBot") -> None:
        if not self.enabled:
            logger.info("Telegram disabled (missing package/token/chat id).")
            return

        @self.bot.message_handler(commands=["status"])
        def _status(message):
            if message.chat.id != TELEGRAM_CHAT_ID:
                return
            self.bot.reply_to(message, app.status_text())

        @self.bot.message_handler(commands=["pnl"])
        def _pnl(message):
            if message.chat.id != TELEGRAM_CHAT_ID:
                return
            self.bot.reply_to(message, app.pnl_text())

        @self.bot.message_handler(commands=["stopbot"])
        def _stopbot(message):
            if message.chat.id != TELEGRAM_CHAT_ID:
                return
            app.running = False
            app.close_socket()
            self.bot.reply_to(message, "🛑 Bot stopped")

        @self.bot.message_handler(commands=["exit"])
        def _exit(message):
            if message.chat.id != TELEGRAM_CHAT_ID:
                return
            if app.state.active_trade:
                app.exit_trade("MANUAL_EXIT")
                self.bot.reply_to(message, "⚠️ Manual exit executed")
            else:
                self.bot.reply_to(message, "No active trade")

    def start_polling(self) -> None:
        if not self.enabled:
            return

        def _poll() -> None:
            while True:
                try:
                    self.bot.infinity_polling(timeout=10, long_polling_timeout=5)
                except Exception as exc:
                    logger.warning("Telegram polling reconnect: %s", exc)
                    time.sleep(3)

        threading.Thread(target=_poll, daemon=True).start()


class SensexCPRBot:
    def __init__(self) -> None:
        if not API_KEY or not ACCESS_TOKEN:
            raise ValueError("Set KITE_API_KEY and KITE_ACCESS_TOKEN environment variables.")

        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)

        self.state = BotState()
        self.running = True
        self.spot_ltp: Optional[float] = None
        self.option_ltp: Optional[float] = None

        self.spot_token: Optional[int] = None
        self.instruments = self.kite.instruments("BFO")
        self.quote_cache: Dict[str, dict] = {}

        self.kws = KiteTicker(API_KEY, ACCESS_TOKEN)
        self._configure_socket_handlers()

        self.notifier = TelegramNotifier()
        self.notifier.register_handlers(self)

        self._load_or_init_state()

    # ---------- utility ----------
    @staticmethod
    def ist_now() -> datetime:
        return datetime.now(tz=IST)

    def today(self) -> date:
        return self.ist_now().date()

    def now_time(self) -> dt_time:
        return self.ist_now().time().replace(tzinfo=None)

    # ---------- persistence ----------
    def save_state(self) -> None:
        payload = asdict(self.state)
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    def _load_or_init_state(self) -> None:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            data_date = data.get("trade_date")
            if data_date == str(self.today()):
                self.state = self._dict_to_state(data)
                logger.info("State restored for %s", data_date)
            else:
                logger.info("New day detected. Resetting persisted state.")
                self.state = BotState(trade_date=str(self.today()))
                self.save_state()
        else:
            self.state = BotState(trade_date=str(self.today()))
            self.save_state()

        if self.state.active_trade:
            logger.info("Crash recovery: active trade restored for %s", self.state.active_trade.symbol)

    @staticmethod
    def _dict_to_state(data: dict) -> BotState:
        active_trade_data = data.get("active_trade")
        active_trade = Trade(**active_trade_data) if active_trade_data else None
        data = {**data, "active_trade": active_trade}
        return BotState(**data)

    # ---------- strategy ----------
    def resolve_spot_token(self) -> None:
        # Token for historical data. 265 is BSE SENSEX index token, used as fallback.
        self.spot_token = 265

    def previous_trading_day_ohlc(self) -> Tuple[float, float, float]:
        if not self.spot_token:
            raise RuntimeError("Spot token not resolved")

        start = datetime.combine(self.today() - timedelta(days=14), dt_time(9, 15), tzinfo=IST)
        end = datetime.combine(self.today(), dt_time(15, 30), tzinfo=IST)
        daily = self.kite.historical_data(self.spot_token, start, end, "day")

        valid = [c for c in daily if datetime.fromisoformat(c["date"].replace("Z", "+00:00")).astimezone(IST).date() < self.today()]
        if not valid:
            raise RuntimeError("No previous day candle found")

        prev = valid[-1]
        return float(prev["high"]), float(prev["low"]), float(prev["close"])

    def calculate_cpr_and_signal(self) -> None:
        high, low, close = self.previous_trading_day_ohlc()

        pivot = (high + low + close) / 3.0
        bc = (high + low) / 2.0
        tc = (pivot - bc) + pivot
        width = abs(tc - bc) / pivot * 100.0

        if width <= CPR_NARROW_MAX:
            cpr_type = "NARROW"
        elif width <= CPR_NORMAL_MAX:
            cpr_type = "NORMAL"
        else:
            cpr_type = "WIDE"

        ma20 = self.calculate_ma20()
        ma_side = "Above" if close > ma20 else "Below"

        if close > ma20 and width < AUTO_SIGNAL_CPR_MAX and close > tc:
            auto_signal = "CE BUY DAY"
            day_mode = "BUYDAY"
            allowed_side: Optional[str] = "BOTH"
        elif close < ma20 and width < AUTO_SIGNAL_CPR_MAX and close < bc:
            auto_signal = "PE BUY DAY"
            day_mode = "BUYDAY"
            allowed_side = "BOTH"
        else:
            auto_signal = "NO TRADE"
            day_mode = "NOTRADE"
            allowed_side = None if ma_side == "Above" else "PE"

        if cpr_type == "WIDE":
            auto_signal = "NO TRADE"
            day_mode = "NOTRADE"
            allowed_side = None if ma_side == "Above" else "PE"

        self.state.cpr_type = cpr_type
        self.state.cpr_width = round(width, 4)
        self.state.pivot = round(pivot, 2)
        self.state.bc = round(bc, 2)
        self.state.tc = round(tc, 2)
        self.state.ma20 = round(ma20, 2)
        self.state.ma_side = ma_side
        self.state.auto_signal = auto_signal
        self.state.day_mode = day_mode
        self.state.allowed_side = allowed_side
        self.save_state()

        self.notifier.send(
            "\n".join(
                [
                    "📊 AUTO SIGNAL",
                    f"CPR Type: {cpr_type}",
                    f"CPR Width: {width:.4f}%",
                    f"MA20: {ma20:.2f}",
                    f"MA Side: {ma_side}",
                    f"Signal: {auto_signal}",
                    f"Allowed: {allowed_side}",
                ]
            )
        )

    def calculate_ma20(self) -> float:
        if not self.spot_token:
            raise RuntimeError("Spot token not resolved")

        start = datetime.combine(self.today() - timedelta(days=80), dt_time(9, 15), tzinfo=IST)
        end = datetime.combine(self.today(), dt_time(15, 30), tzinfo=IST)
        daily = self.kite.historical_data(self.spot_token, start, end, "day")

        closed_days = []
        for c in daily:
            d = datetime.fromisoformat(c["date"].replace("Z", "+00:00")).astimezone(IST).date()
            if d < self.today():
                closed_days.append(float(c["close"]))

        if len(closed_days) < 20:
            raise RuntimeError("Not enough daily candles for MA20")

        return sum(closed_days[-20:]) / 20.0

    def fetch_930_candle(self) -> None:
        if self.state.candle_ready:
            return
        if not self.spot_token:
            raise RuntimeError("Spot token not resolved")

        from_dt = datetime.combine(self.today(), CANDLE_TIME, tzinfo=IST)
        to_dt = datetime.combine(self.today(), CANDLE_READY_AFTER, tzinfo=IST)
        candles = self.kite.historical_data(self.spot_token, from_dt, to_dt, "5minute")
        if not candles:
            return

        first = candles[0]
        self.state.candle_high = float(first["high"])
        self.state.candle_low = float(first["low"])
        self.state.candle_ready = True
        self.save_state()
        self.notifier.send(
            f"✅ 9:30 candle captured | High={self.state.candle_high} Low={self.state.candle_low}"
        )

        if not self.state.cpr_type:
            self.calculate_cpr_and_signal()

    # ---------- instrument selection ----------
    def get_future_expiries(self) -> List[date]:
        expiries = sorted(
            {
                i["expiry"]
                for i in self.instruments
                if i["name"] == UNDERLYING and i["instrument_type"] in {"CE", "PE"} and i["expiry"] >= self.today()
            }
        )
        return expiries

    def find_atm_option(self, side: str) -> Optional[Tuple[str, int]]:
        if self.spot_ltp is None:
            return None

        expiries = self.get_future_expiries()
        if not expiries:
            return None

        current_week = expiries[0]
        next_week = expiries[1] if len(expiries) > 1 else expiries[0]

        for exp in [next_week, current_week]:
            candidate = self._atm_from_expiry(side, exp)
            if candidate and self.is_liquid(candidate[0]):
                return candidate
        return None

    def _atm_from_expiry(self, side: str, expiry: date) -> Optional[Tuple[str, int]]:
        strikes = [
            ins
            for ins in self.instruments
            if ins["name"] == UNDERLYING and ins["instrument_type"] == side and ins["expiry"] == expiry
        ]
        if not strikes:
            return None

        rounded = round(self.spot_ltp / 100.0) * 100.0
        atm = min(strikes, key=lambda x: abs(float(x["strike"]) - rounded))
        symbol = f"{atm['exchange']}:{atm['tradingsymbol']}"
        return symbol, int(atm["instrument_token"])

    def is_liquid(self, symbol: str) -> bool:
        try:
            q = self.kite.quote([symbol]).get(symbol, {})
            oi = float(q.get("oi") or 0)
            depth = q.get("depth") or {}
            buy = depth.get("buy") or []
            sell = depth.get("sell") or []
            if not buy or not sell:
                return False

            bid = float(buy[0].get("price") or 0)
            ask = float(sell[0].get("price") or 0)
            if bid <= 0 or ask <= 0:
                return False

            spread = ask - bid
            return oi >= MIN_OI and spread <= MAX_SPREAD
        except Exception as exc:
            logger.warning("Liquidity check failed for %s: %s", symbol, exc)
            return False

    # ---------- eligibility / trade rules ----------
    def breakout_side(self) -> Optional[str]:
        if self.spot_ltp is None or not self.state.candle_ready:
            return None

        if self.spot_ltp >= self.state.candle_high + ENTRY_BUFFER:
            return "CE"
        if self.spot_ltp <= self.state.candle_low - ENTRY_BUFFER:
            return "PE"
        return None

    def can_take_trade(self, side: str) -> bool:
        if self.state.day_closed or self.state.entry_lock or self.state.active_trade:
            return False

        now = self.now_time()
        if now >= LAST_ENTRY_TIME:
            return False

        # Rule: no trade when CPR is wide
        if self.state.cpr_type == "WIDE":
            return False

        # Side filter from auto-signal/day mode
        allowed = self.state.allowed_side
        if allowed not in {"BOTH", side}:
            return False

        if self.state.day_mode == "BUYDAY":
            if self.state.trades_taken >= 2:
                return False
            if self.state.trades_taken == 1:
                # second trade only after first SL and opposite side only
                if self.state.first_trade_result != "SL":
                    return False
                if side == self.state.first_trade_side:
                    return False

        else:  # NOTRADE mode
            # If MA side Above -> allowed_side already None => blocked above.
            # If MA side Below -> PE only and one trade/day
            if self.state.trades_taken >= 1:
                return False
            if self.state.ma_side == "Below" and side != "PE":
                return False

        return True

    # ---------- order flow ----------
    def place_buy(self, symbol: str, token: int, side: str) -> None:
        order_id = self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=symbol.split(":")[0],
            tradingsymbol=symbol.split(":")[1],
            transaction_type=self.kite.TRANSACTION_TYPE_BUY,
            quantity=LOT_SIZE,
            order_type=self.kite.ORDER_TYPE_MARKET,
            product=PRODUCT,
        )
        average = self._wait_order_complete(order_id)
        if average is None:
            raise RuntimeError(f"Buy order did not complete: {order_id}")

        trade = Trade(
            side=side,
            symbol=symbol,
            token=token,
            entry_price=average,
            sl_price=average - PREMIUM_SL_POINTS,
            target_price=average + PREMIUM_TARGET_POINTS,
            qty=LOT_SIZE,
            entry_time_iso=self.ist_now().isoformat(),
        )
        self.state.active_trade = trade
        self.state.entry_lock = True
        if self.state.trades_taken == 0:
            self.state.first_trade_side = side
        self.save_state()

        self.notifier.send(
            "\n".join(
                [
                    "🟢 ENTRY",
                    f"Symbol: {symbol}",
                    f"Side: {side}",
                    f"Entry: {average:.2f}",
                    f"SL: {trade.sl_price:.2f}",
                    f"Target: {trade.target_price:.2f}",
                ]
            )
        )

        self.subscribe_token(token)

    def place_sell(self, trade: Trade) -> Optional[float]:
        order_id = self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=trade.symbol.split(":")[0],
            tradingsymbol=trade.symbol.split(":")[1],
            transaction_type=self.kite.TRANSACTION_TYPE_SELL,
            quantity=trade.qty,
            order_type=self.kite.ORDER_TYPE_MARKET,
            product=PRODUCT,
        )
        return self._wait_order_complete(order_id)

    def _wait_order_complete(self, order_id: str, timeout_sec: int = 15) -> Optional[float]:
        start = time.time()
        while time.time() - start < timeout_sec:
            try:
                hist = self.kite.order_history(order_id)
                if not hist:
                    time.sleep(0.4)
                    continue
                status = hist[-1].get("status", "")
                if status == "COMPLETE":
                    return float(hist[-1].get("average_price") or 0)
                if status in {"REJECTED", "CANCELLED"}:
                    return None
            except Exception:
                pass
            time.sleep(0.4)
        return None

    def exit_trade(self, reason: str) -> None:
        trade = self.state.active_trade
        if not trade:
            return

        exit_px = self.place_sell(trade)
        if exit_px is None:
            logger.error("Exit failed for %s", trade.symbol)
            return

        pnl_points = exit_px - trade.entry_price
        pnl_value = pnl_points * trade.qty

        self.state.trades_taken += 1
        if self.state.trades_taken == 1:
            self.state.first_trade_result = "TARGET" if reason == "TARGET" else "SL"

        self.notifier.send(
            "\n".join(
                [
                    "🔴 EXIT",
                    f"Symbol: {trade.symbol}",
                    f"Reason: {reason}",
                    f"Entry: {trade.entry_price:.2f}",
                    f"Exit: {exit_px:.2f}",
                    f"P&L: {pnl_value:.2f}",
                ]
            )
        )

        self.state.active_trade = None
        self.state.entry_lock = False

        if self.state.day_mode == "BUYDAY":
            if self.state.trades_taken == 1 and reason == "TARGET":
                self.state.day_closed = True
            elif self.state.trades_taken >= 2:
                self.state.day_closed = True
        else:
            if self.state.trades_taken >= 1:
                self.state.day_closed = True

        self.save_state()

    # ---------- websocket ----------
    def _configure_socket_handlers(self) -> None:
        self.kws.on_connect = self.on_connect
        self.kws.on_ticks = self.on_ticks
        self.kws.on_close = self.on_close
        self.kws.on_error = self.on_error

    def on_connect(self, ws, response) -> None:
        logger.info("WebSocket connected")
        self.resolve_spot_token()
        ws.subscribe([265])
        ws.set_mode(ws.MODE_LTP, [265])
        if self.state.active_trade:
            self.subscribe_token(self.state.active_trade.token)

    def subscribe_token(self, token: int) -> None:
        try:
            self.kws.subscribe([token])
            self.kws.set_mode(self.kws.MODE_LTP, [token])
        except Exception as exc:
            logger.warning("Token subscribe failed for %s: %s", token, exc)

    def on_ticks(self, ws, ticks: List[dict]) -> None:
        for t in ticks:
            token = t.get("instrument_token")
            if token == 265:
                self.spot_ltp = float(t["last_price"])
            if self.state.active_trade and token == self.state.active_trade.token:
                self.option_ltp = float(t["last_price"])

    def on_close(self, ws, code, reason) -> None:
        logger.warning("WebSocket closed: %s | %s", code, reason)

    def on_error(self, ws, code, reason) -> None:
        logger.error("WebSocket error: %s | %s", code, reason)

    def start_socket_loop(self) -> None:
        def _runner() -> None:
            while self.running:
                try:
                    self.kws.connect(threaded=True)
                    while self.running:
                        time.sleep(1)
                except Exception as exc:
                    logger.warning("Socket reconnect loop: %s", exc)
                    time.sleep(5)

        threading.Thread(target=_runner, daemon=True).start()

    def close_socket(self) -> None:
        try:
            self.kws.close()
        except Exception:
            pass

    # ---------- reporting ----------
    def status_text(self) -> str:
        s = self.state
        lines = [
            "📊 BOT STATUS",
            f"Date: {s.trade_date}",
            f"CPR: {s.cpr_type} ({s.cpr_width:.4f}%)",
            f"Signal: {s.auto_signal}",
            f"Mode: {s.day_mode}",
            f"Allowed: {s.allowed_side}",
            f"Trades: {s.trades_taken}",
        ]
        if s.active_trade:
            lines.extend(
                [
                    f"Active: {s.active_trade.symbol}",
                    f"Entry: {s.active_trade.entry_price:.2f}",
                    f"SL: {s.active_trade.sl_price:.2f}",
                    f"Target: {s.active_trade.target_price:.2f}",
                ]
            )
        else:
            lines.append("Active: None")
        return "\n".join(lines)

    def pnl_text(self) -> str:
        trade = self.state.active_trade
        if not trade:
            return "No active trade"
        if self.option_ltp is None:
            return f"Active: {trade.symbol}\nWaiting live option tick..."
        pnl = (self.option_ltp - trade.entry_price) * trade.qty
        return (
            f"💰 LIVE P&L\n"
            f"Symbol: {trade.symbol}\n"
            f"Entry: {trade.entry_price:.2f}\n"
            f"Current: {self.option_ltp:.2f}\n"
            f"P&L: {pnl:.2f}"
        )

    # ---------- orchestrator ----------
    def process_market_logic(self) -> None:
        now = self.now_time()

        if now >= FORCE_EXIT_TIME and self.state.active_trade:
            self.exit_trade("FORCE_EXIT")

        if now > MARKET_CLOSE:
            self.state.day_closed = True
            self.running = False
            self.save_state()
            return

        if MARKET_OPEN <= now <= MARKET_CLOSE and not self.state.candle_ready and now >= CANDLE_READY_AFTER:
            self.fetch_930_candle()

        if not self.state.candle_ready or self.state.day_closed:
            return

        # Manage active trade
        trade = self.state.active_trade
        if trade and self.option_ltp is not None:
            if self.option_ltp <= trade.sl_price:
                self.exit_trade("SL")
                return
            if self.option_ltp >= trade.target_price:
                self.exit_trade("TARGET")
                return

        # New entry
        if self.state.active_trade:
            return

        side = self.breakout_side()
        if not side:
            return

        if not self.can_take_trade(side):
            return

        opt = self.find_atm_option(side)
        if not opt:
            logger.info("No liquid ATM found for %s", side)
            return

        symbol, token = opt
        self.place_buy(symbol, token, side)

    def run(self) -> None:
        profile = self.kite.profile()
        logger.info("Logged in as %s", profile.get("user_name"))

        self.resolve_spot_token()
        self.notifier.start_polling()
        self.start_socket_loop()

        self.notifier.send(
            f"🚀 SENSEX CPR bot started\nDate: {self.today()}\nTime: {self.ist_now().strftime('%H:%M:%S')} IST"
        )

        while self.running:
            try:
                self.process_market_logic()
            except Exception as exc:
                logger.exception("Main loop error: %s", exc)
            time.sleep(POLL_SLEEP)

        self.close_socket()
        logger.info("Bot stopped cleanly")


if __name__ == "__main__":
    bot = SensexCPRBot()
    bot.run()
