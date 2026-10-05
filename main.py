import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import tkinter as tk
from tkinter import messagebox
import threading
import time
import datetime
import logging
from winotify import Notification, audio

# إعداد السجلات الشاملة والموثوقة
logging.basicConfig(filename='master_trading_system.log', level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# ==========================================
# ⚙️ إعدادات التحكم واختيار استراتيجية التشغيل
# ==========================================
# اختر النمط الذي تريده للعمل:
# 1. "GRID_MARTINGALE" -> التداول المتكرر والمضاعفات (جريد) لحسابات السنت (صفقة فورية، مضاعفة كل 10 سنت، وهدف 50 سنت ربح).
# 2. "AI_SMC"           -> المساعد الذكي، تحليل المؤسسات (SMC)، صفقتان يومياً بأوقات الذروة، وتقارير أداء يومية.
STRATEGY_MODE = "GRID_MARTINGALE"  

IS_CENT_ACCOUNT = True     # True لحسابات السنت | False لحسابات الدولار العادية
BASE_SYMBOL = "XAUUSD"
MAGIC_NUMBER = 777888
RISK_PERCENTAGE = 1.0  
TIMEFRAME = mt5.TIMEFRAME_M15
AUTOMATIC_EXECUTION = False  # لنمط AI_SMC: True تنفيذ تلقائي كامل | False مراجعة تقرير المساعد أولاً

# إعدادات استراتيجية الجريد والمضاعفات (للنمط الأول)
INITIAL_LOT = 0.01               
LOT_MULTIPLIER = 1.5             
GRID_STEP_POINTS = 100           # مسافة انعكاس السعر (10 سنت = 100 نقطة في الذهب)
TARGET_NET_PROFIT_CENTS = 50     # هدف الربح الإجمالي بالسنت للإغلاق

# إعدادات المساعد الذكي (للنمط الثاني)
TARGET_DAILY_TRADES = 2
daily_trade_date = None
trades_executed_today = 0

ACTIVE_SYMBOL = BASE_SYMBOL

def get_correct_symbol(base):
    """اكتشاف اسم الرمز أوتوماتيكياً (مثل XAUUSDc أو XAUUSD.cent)"""
    if mt5.symbol_info(base):
        return base
    cent_suffixes = ["c", ".c", "cent", ".cent", "_c"]
    for suffix in cent_suffixes:
        if mt5.symbol_info(base + suffix):
            return base + suffix
    return base

def initialize_mt5():
    global ACTIVE_SYMBOL
    if not mt5.initialize():
        logging.error(f"فشل الاتصال بـ MT5: {mt5.last_error()}")
        return False
    ACTIVE_SYMBOL = get_correct_symbol(BASE_SYMBOL)
    if not mt5.symbol_select(ACTIVE_SYMBOL, True):
        logging.error(f"فشل اختيار الرمز {ACTIVE_SYMBOL}")
        return False
    logging.info(f"تم الاتصال بنجاح. الرمز: {ACTIVE_SYMBOL} | النمط المفعل: {STRATEGY_MODE}")
    return True

# ==========================================
# 📈 النمط الأول: استراتيجية الجريد والمضاعفات الذكية
# ==========================================
def run_grid_martingale_engine():
    positions = mt5.positions_get(symbol=ACTIVE_SYMBOL, magic=MAGIC_NUMBER)
    tick = mt5.symbol_info_tick(ACTIVE_SYMBOL)
    if not tick: return

    point = mt5.symbol_info(ACTIVE_SYMBOL).point

    # 1. فتح الصفقة الأولية تلقائياً عند التشغيل إذا لم تكن هناك صفقات مفتوحة
    if not positions:
        price = tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": ACTIVE_SYMBOL,
            "volume": INITIAL_LOT,
            "type": mt5.ORDER_TYPE_BUY,  # افتراضياً تبدأ بصفقة شراء للجريد
            "price": price,
            "deviation": 20,
            "magic": MAGIC_NUMBER,
            "comment": "Grid Cent Initial",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }
        res = mt5.order_send(request)
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            logging.info(f"🚀 [Grid] تم فتح الصفقة الأولية بحجم {INITIAL_LOT} على حساب السنت.")
        return

    # 2. حساب صافي الربح الكلي وإغلاق كافة الصفقات فور وصول هدف الـ 50 سنت
    total_net_profit = sum([pos.profit + pos.swap + pos.commission for pos in positions])
    if total_net_profit >= TARGET_NET_PROFIT_CENTS:
        logging.info(f"🎯 [Grid] تم الوصول لهدف الربح الكلي ({total_net_profit} سنت). جاري إغلاق كافة صفقات الجريد...")
        for pos in positions:
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            close_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
            mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL, "symbol": ACTIVE_SYMBOL, "volume": pos.volume,
                "type": close_type, "position": pos.ticket, "price": close_price,
                "deviation": 20, "magic": MAGIC_NUMBER, "comment": "Grid Target Reached",
                "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_FOK
            })
        
        try:
            toast = Notification(app_id="Grid Martingale Bot",
                                 title="🎉 تم تحقيق هدف الجريد بنجاح!",
                                 msg=f"صافي الربح: {total_net_profit} سنت. تم تأمين المكسب وبدء دورة جديدة.",
                                 duration="long")
            toast.set_audio(audio.Default, loop=False)
            toast.show()
        except:
            pass
        return

    # 3. فتح صفقة مضاعفة جديدة كلما عكس السوق مسافة GRID_STEP_POINTS
    last_pos = max(positions, key=lambda x: x.time)
    last_price = last_pos.price
    last_lot = last_pos.volume
    current_bid = tick.bid
    current_ask = tick.ask

    if last_pos.type == mt5.ORDER_TYPE_BUY:
        if last_price - current_bid >= GRID_STEP_POINTS * point:
            new_lot = round(last_lot * LOT_MULTIPLIER, 2)
            symbol_info = mt5.symbol_info(ACTIVE_SYMBOL)
            new_lot = min(new_lot, symbol_info.volume_max)
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL, "symbol": ACTIVE_SYMBOL, "volume": new_lot,
                "type": mt5.ORDER_TYPE_BUY, "price": current_ask, "deviation": 20,
                "magic": MAGIC_NUMBER, "comment": f"Grid Martingale L:{new_lot}",
                "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_FOK,
            }
            res = mt5.order_send(request)
            if res.retcode == mt5.TRADE_RETCODE_DONE:
                logging.info(f"📉 [Grid] عكس السوق المسافة المطلوبة. فتح صفقة مضاعفة بحجم: {new_lot}")
                time.sleep(10)

    elif last_pos.type == mt5.ORDER_TYPE_SELL:
        if current_ask - last_price >= GRID_STEP_POINTS * point:
            new_lot = round(last_lot * LOT_MULTIPLIER, 2)
            symbol_info = mt5.symbol_info(ACTIVE_SYMBOL)
            new_lot = min(new_lot, symbol_info.volume_max)
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL, "symbol": ACTIVE_SYMBOL, "volume": new_lot,
                "type": mt5.ORDER_TYPE_SELL, "price": current_bid, "deviation": 20,
                "magic": MAGIC_NUMBER, "comment": f"Grid Martingale L:{new_lot}",
                "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_FOK,
            }
            res = mt5.order_send(request)
            if res.retcode == mt5.TRADE_RETCODE_DONE:
                logging.info(f"📈 [Grid] عكس السوق للأسفل مسافة المطلوبة. فتح صفقة مضاعفة بحجم: {new_lot}")
                time.sleep(10)

# ==========================================
# 🤖 النمط الثاني: استراتيجية المساعد الذكي و الـ SMC
# ==========================================
def is_peak_trading_session():
    tick = mt5.symbol_info_tick(ACTIVE_SYMBOL)
    if not tick: return False
    server_time = datetime.datetime.fromtimestamp(tick.time)
    if server_time.weekday() >= 5: return False
    return 9 <= server_time.hour <= 21

def generate_and_save_daily_performance_report():
    today = datetime.datetime.now().date()
    from_date = datetime.datetime(today.year, today.month, today.day, 0, 0, 0)
    to_date = datetime.datetime.now()
    
    deals = mt5.history_deals_get(from_date, to_date)
    if not deals: return
        
    bot_deals = [d for d in deals if d.magic == MAGIC_NUMBER and d.entry == mt5.DEAL_ENTRY_OUT]
    if not bot_deals: return
        
    raw_profit = sum([d.profit + d.commission + d.swap for d in bot_deals])
    total_profit = raw_profit / 100.0 if IS_CENT_ACCOUNT else raw_profit
    
    total_trades = len(bot_deals)
    winning_trades = len([d for d in bot_deals if (d.profit + d.commission + d.swap) > 0])
    losing_trades = total_trades - winning_trades
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    
    ai_evaluation = "أداء استثنائي وعالي الانضباط." if total_profit > 0 else "توجيه مراجعة: الالتزام بوقف الخسارة بانتظام."

    report_text = (
        f"========================================\n"
        f"📅 تقرير الأداء اليومي المساعد الذكي - {today}\n"
        f"========================================\n"
        f"🔹 إجمالي الصفقات: {total_trades} | الرابحة: {winning_trades} | الخاسرة: {losing_trades}\n"
        f"🔹 نسبة النجاح (Win Rate): {win_rate:.1f}%\n"
        f"🔹 صافي الربح الإجمالي: {total_profit:.2f} USD\n"
        f"🤖 تقييم المساعد الذكي: {ai_evaluation}\n"
        f"========================================\n\n"
    )
    
    with open(f"daily_report_{today}.txt", "w", encoding="utf-8") as f:
        f.write(report_text)

def scan_ai_market_opportunity(symbol):
    active_positions = mt5.positions_get(symbol=symbol, magic=MAGIC_NUMBER)
    if active_positions and len(active_positions) > 0: return None, None, 0, 0, 0

    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 30)
    if rates is None or len(rates) < 30: return None, None, 0, 0, 0
    
    df = pd.DataFrame(rates)
    point = mt5.symbol_info(symbol).point
    current_close = df['close'].iloc[-1]
    recent_high = df['high'].iloc[-15:-2].max()
    recent_low = df['low'].iloc[-15:-2].min()
    tick = mt5.symbol_info_tick(symbol)
    if not tick: return None, None, 0, 0, 0

    if df['low'].iloc[-1] < recent_low and current_close > recent_low:
        entry = tick.ask
        sl = recent_low - (250 * point)
        tp = entry + (abs(entry - sl) * 2.5)
        return "BUY", "Institutional Liquidity Sweep & SMC", entry, sl, tp
    elif df['high'].iloc[-1] > recent_high and current_close < recent_high:
        entry = tick.bid
        sl = recent_high + (250 * point)
        tp = entry - (abs(sl - entry) * 2.5)
        return "SELL", "Institutional Liquidity Sweep & SMC", entry, sl, tp

    return None, None, 0, 0, 0

def calculate_dynamic_lot(entry_price, sl_price):
    account_info = mt5.account_info()
    if not account_info: return 0.01
    equity = account_info.equity
    risk_amount = equity * (RISK_PERCENTAGE / 100.0)
    symbol_info = mt5.symbol_info(ACTIVE_SYMBOL)
    if not symbol_info: return 0.01
    pip_diff = abs(entry_price - sl_price)
    if pip_diff == 0: return 0.01
    tick_value = symbol_info.trade_tick_value
    tick_size = symbol_info.trade_tick_size
    lot = risk_amount / (pip_diff / tick_size * tick_value) if tick_value > 0 else 0.01
    return max(symbol_info.volume_min, min(round(lot, 2), symbol_info.volume_max))

def execute_ai_trade(action_type, entry, sl, tp, rationale):
    global trades_executed_today, daily_trade_date
    lot_size = calculate_dynamic_lot(entry, sl)
    tick = mt5.symbol_info_tick(ACTIVE_SYMBOL)
    price = tick.ask if action_type == "BUY" else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": ACTIVE_SYMBOL, "volume": lot_size,
        "type": mt5.ORDER_TYPE_BUY if action_type == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": price, "sl": sl, "tp": tp, "deviation": 20, "magic": MAGIC_NUMBER,
        "comment": "AI SMC Elite", "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_FOK,
    }
    res = mt5.order_send(request)
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        today = datetime.datetime.now().date()
        if daily_trade_date != today:
            daily_trade_date = today
            trades_executed_today = 0
        trades_executed_today += 1
        logging.info(f"[AI_SMC] تم تنفيذ الصفقة بنجاح. التقرير:\n{rationale}")
        return True
    return False

def run_ai_smc_engine():
    global trades_executed_today, daily_trade_date
    today = datetime.datetime.now().date()
    if daily_trade_date != today:
        daily_trade_date = today
        trades_executed_today = 0
        
    current_hour = datetime.datetime.now().hour
    if current_hour >= 22:
        generate_and_save_daily_performance_report()
        
    if trades_executed_today < TARGET_DAILY_TRADES:
        if is_peak_trading_session():
            signal, model, entry, sl, tp = scan_ai_market_opportunity(ACTIVE_SYMBOL)
            if signal and model:
                rationale = f"🧠 [المساعد الذكي]\nالسبب الفني: {model}\nالدخول: {entry} | الوقف: {sl} | الهدف: {tp}"
                if AUTOMATIC_EXECUTION:
                    execute_ai_trade(signal, entry, sl, tp, rationale)
                else:
                    def show_win():
                        root = tk.Tk()
                        root.attributes('-topmost', True)
                        root.title("المساعد الذكي - تقرير الصفقة")
                        root.geometry("480x300")
                        root.configure(bg="#1a1a1a")
                        root.eval('tk::PlaceWindow . center')
                        tk.Label(root, text=f"🤖 فرصة ({signal})", fg="#00ffcc", bg="#1a1a1a", font=("Arial", 12, "bold")).pack(pady=10)
                        txt = tk.Text(root, height=8, width=50, bg="#2b2b2b", fg="#ffffff")
                        txt.insert(tk.END, rationale)
                        txt.config(state=tk.DISABLED)
                        txt.pack(pady=5)
                        tk.Button(root, text="✅ تنفيذ الصفقة", bg="#28a745", fg="white", command=lambda: [execute_ai_trade(signal, entry, sl, tp, rationale), root.destroy()]).pack(pady=10)
                        root.mainloop()
                    threading.Thread(target=show_win).start()
                time.sleep(1200)

# ==========================================
# 🤖 الحلقة التشغيلية الموحدة (Main Loop)
# ==========================================
def main_bot_loop():
    print(f"🚀 البوت يعمل الآن بكفاءة مطلقة! النمط المفعل: [{STRATEGY_MODE}]")
    
    while True:
        if STRATEGY_MODE == "GRID_MARTINGALE":
            run_grid_martingale_engine()
            time.sleep(5)  # مراقبة مستمرة وسريعة للجريد وحساب السنت
            
        elif STRATEGY_MODE == "AI_SMC":
            run_ai_smc_engine()
            time.sleep(15) # مسح ذكي دوري للفرص المؤسسية

if __name__ == "__main__":
    if initialize_mt5():
        threading.Thread(target=main_bot_loop, daemon=True).start()
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            mt5.shutdown()
