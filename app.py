# app.py
# 雲端版：你的 ETF + 買房頭期款手機儀表板（for Render）
# 功能：
# - 固定持股 + 配息資料
# - 新增交易 / 配息複投（寫入 SQLite，永久記錄）
# - 後台頁面：查看 / 篩選 / 編輯 / 刪除交易
# - 投資組合總覽（含息）
# - 配息年度對比
# - 填息比對（最近一次配息）
# - 買房頭期款進度

import yfinance as yf
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for
import sqlite3
from pathlib import Path

# ======== 全域設定：買房目標與定投金額 =========
HOUSE_TARGET_LOW = 6_500_000   # 下限：650 萬
HOUSE_TARGET_HIGH = 7_500_000  # 上限：750 萬
ANNUAL_RETURN = 0.06           # 假設年化報酬率 6%
MONTHLY_DCA = 5_000            # 每月定投總額（0050:2000 + 其他各1000）

# 抓不到股價時的預設值（可以依你習慣隨時調整）
DEFAULT_PRICES = {
    "0050": 150.0,
    "0056": 35.0,
    "00878": 23.0,
    "00919": 24.0,
}

# ======== SQLite 資料庫設定 =========
DB_PATH = Path(__file__).with_name("portfolio_trades.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            shares INTEGER NOT NULL,
            amount REAL NOT NULL,
            reinvest REAL NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def get_trades_summary():
    """
    回傳：
      trades_summary: {symbol: {add_shares, add_amount, add_reinvest}}
      total_amount: 所有交易總金額
      total_reinvest: 所有交易中配息複投總額
      total_new_cash: 自掏腰包金額
    """
    conn = get_db()
    cur = conn.execute(
        """
        SELECT
          symbol,
          COALESCE(SUM(shares),0)  AS add_shares,
          COALESCE(SUM(amount),0)  AS add_amount,
          COALESCE(SUM(reinvest),0) AS add_reinvest
        FROM trades
        GROUP BY symbol
        """
    )
    rows = cur.fetchall()
    conn.close()

    summary = {}
    total_amount = 0.0
    total_reinvest = 0.0

    for r in rows:
        sym = r["symbol"]
        add_shares = r["add_shares"]
        add_amount = r["add_amount"]
        add_reinvest = r["add_reinvest"]
        summary[sym] = {
            "add_shares": add_shares,
            "add_amount": add_amount,
            "add_reinvest": add_reinvest,
        }
        total_amount += add_amount
        total_reinvest += add_reinvest

    total_new_cash = max(0.0, total_amount - total_reinvest)

    return summary, total_amount, total_reinvest, total_new_cash


# ======== 基本資料區：持股、配息、定期定額 =========

holdings = [
    {"symbol": "0050", "name": "元大台灣50",        "shares": 3228, "cost": 57.53},
    {"symbol": "0056", "name": "高股息ETF",         "shares": 3192, "cost": 37.36},
    {"symbol": "00878", "name": "國泰永續高息",    "shares": 5176, "cost": 22.06},
    {"symbol": "00919", "name": "群益台灣精選高息", "shares": 5195, "cost": 22.86},
]

dividends = [
    {"date": "2023-08-16", "symbol": "00878", "cash": 350,  "note": "2023/08配息"},
    {"date": "2023-11-16", "symbol": "00878", "cash": 350,  "note": "2023/11配息"},
    {"date": "2023-12-18", "symbol": "00919", "cash": 358,  "note": "2023/12配息"},

    {"date": "2024-09-23", "symbol": "00919", "cash": 2160, "note": "2024/09配息"},
    {"date": "2024-10-17", "symbol": "0056",  "cash": 2140, "note": "2024/10配息"},
    {"date": "2024-11-18", "symbol": "00878", "cash": 1650, "note": "2024/11配息"},
    {"date": "2024-12-20", "symbol": "00919", "cash": 2160, "note": "2024/12配息"},

    {"date": "2025-01-17", "symbol": "0056",  "cash": 2140, "note": "2025/01配息"},
    {"date": "2025-02-20", "symbol": "00878", "cash": 1500, "note": "2025/02配息"},
    {"date": "2025-03-20", "symbol": "00919", "cash": 2160, "note": "2025/03配息"},

    {"date": "2025-04-23", "symbol": "0056",  "cash": 2140, "note": "2025/04配息"},
    {"date": "2025-05-19", "symbol": "00878", "cash": 1410, "note": "2025/05配息"},
    {"date": "2025-06-17", "symbol": "00919", "cash": 2160, "note": "2025/06配息"},

    {"date": "2025-07-21", "symbol": "0050",  "cash": 360,  "note": "2025/07配息"},
    {"date": "2025-07-21", "symbol": "0056",  "cash": 1732, "note": "2025/07配息"},
    {"date": "2025-08-18", "symbol": "00878", "cash": 1203, "note": "2025/08配息"},
    {"date": "2025-09-16", "symbol": "00919", "cash": 1629, "note": "2025/09配息"},

    {"date": "2025-10-23", "symbol": "0056",  "cash": 2653, "note": "2025/10配息"},
    {"date": "2025-11-20", "symbol": "00878", "cash": 2051, "note": "2025/11配息"},
    # {"date": "2025-12-20", "symbol": "00919", "cash": 2160, "note": "2025/12配息"},
]

dca_records = [
    {"date": "2025-10-01", "symbol": "0050", "amount": 2000},
    {"date": "2025-10-01", "symbol": "0056", "amount": 1000},
    {"date": "2025-10-01", "symbol": "00878", "amount": 1000},
    {"date": "2025-10-01", "symbol": "00919", "amount": 1000},

    {"date": "2025-11-01", "symbol": "0050", "amount": 2000},
    {"date": "2025-11-01", "symbol": "0056", "amount": 1000},
    {"date": "2025-11-01", "symbol": "00878", "amount": 1000},
    {"date": "2025-11-01", "symbol": "00919", "amount": 1000},

    {"date": "2025-12-01", "symbol": "0050", "amount": 2000},
    {"date": "2025-12-01", "symbol": "0056", "amount": 1000},
    {"date": "2025-12-01", "symbol": "00878", "amount": 1000},
    {"date": "2025-12-01", "symbol": "00919", "amount": 1000},
]

# ======== 工具：格式化 =========

def fmt_money(x: float) -> str:
    return f"{x:,.0f}"

def fmt_pct(x: float) -> str:
    return f"{x:,.2f}%"


# ======== 抓股價 =========

def fetch_price_tw(symbol: str) -> float:
    """用 yfinance 抓台股 ETF 價格，失敗就用 DEFAULT_PRICES。"""
    ticker_symbol = symbol + ".TW"
    try:
        ticker = yf.Ticker(ticker_symbol)
        data = ticker.history(period="5d")

        if not data.empty:
            close_series = data["Close"].dropna()
            if not close_series.empty:
                return float(close_series.iloc[-1])
    except Exception as e:
        print(f"[警告] 抓 {symbol} 價格失敗：{e}")

    print(f"[改用預設價格] {symbol} = {DEFAULT_PRICES.get(symbol, 0.0)}")
    return DEFAULT_PRICES.get(symbol, 0.0)


def ensure_prices():
    """確保 holdings 每檔都有 price 欄位（只抓一次）。"""
    for h in holdings:
        if "price" not in h:
            h["price"] = fetch_price_tw(h["symbol"])


def get_total_market_value() -> float:
    ensure_prices()
    return sum(h["shares"] * h["price"] for h in holdings)


# ======== 配息 & DCA 工具 =========

def get_dividends_total(symbol: str) -> float:
    return sum(d["cash"] for d in dividends if d["symbol"] == symbol)


def get_dividends_total_by_year(year: int) -> float:
    total = 0.0
    for d in dividends:
        d_year = int(d["date"][:4])
        if d_year == year:
            total += d["cash"]
    return total


def get_dca_total(year: int | None = None) -> float:
    total = 0.0
    for r in dca_records:
        if year is not None:
            r_year = int(r["date"][:4])
            if r_year != year:
                continue
        total += r["amount"]
    return total


# ======== 填息相關工具 =========

def get_last_dividend_event(symbol: str):
    """回傳某檔 ETF 最近一次配息紀錄（沒有就回 None）。"""
    events = [d for d in dividends if d["symbol"] == symbol]
    if not events:
        return None
    events_sorted = sorted(events, key=lambda d: d["date"])
    return events_sorted[-1]


def get_pre_ex_close_price(symbol: str, ex_date_str: str) -> float | None:
    """
    給 symbol（例如 '00919'）和配息日期（例如 '2025-09-16'），
    回傳「除息日前一個交易日」的收盤價。
    """
    ex_date = datetime.strptime(ex_date_str, "%Y-%m-%d").date()
    ticker = yf.Ticker(symbol + ".TW")

    # 抓 ex_date 往前 20 天，避免遇到連假沒交易
    start = ex_date - timedelta(days=20)
    end = ex_date  # history 的 end 不含當天
    data = ticker.history(start=start, end=end)

    if data.empty:
        return None

    pre_close = float(data["Close"].iloc[-1])
    return pre_close


def compute_fill_infos(etf_rows):
    """
    傳入 compute_dashboard() 產出的 etfs list，
    回傳每檔 ETF 的填息資訊（最近一次配息）。
    """
    fill_infos = []

    for e in etf_rows:
        symbol = e["symbol"]
        shares = e["shares"]
        now_price = e["price"]
        name = e["name"]

        last_ev = get_last_dividend_event(symbol)
        if not last_ev:
            continue  # 這檔暫時沒有配息紀錄

        last_date = last_ev["date"]
        cash_total = last_ev["cash"]

        if not shares or shares <= 0:
            continue

        div_per_share = cash_total / shares  # 約略每股配息

        pre_close = get_pre_ex_close_price(symbol, last_date)
        if pre_close is None:
            continue

        # 理論除息價 = 除息前價 - 每股配息
        ex_ref_price = pre_close - div_per_share
        filled_amount = now_price - ex_ref_price

        if div_per_share > 0:
            fill_ratio = filled_amount / div_per_share * 100
        else:
            fill_ratio = 0.0

        # 還差多少元才回到除息前價（若已超過，則 0）
        gap_to_fill = max(0.0, pre_close - now_price)

        fill_infos.append({
            "symbol": symbol,
            "name": name,
            "last_date": last_date,
            "pre_close": pre_close,
            "now_price": now_price,
            "div_per_share": div_per_share,
            "fill_ratio": fill_ratio,
            "gap_to_fill": gap_to_fill,
        })

    return fill_infos


# ======== 買房相關：估算達成時間 =========

def estimate_years_to_target(current_value: float,
                             monthly_invest: float,
                             annual_return: float,
                             target: float):
    r = annual_return
    yearly_invest = monthly_invest * 12
    years = 0.0
    step = 0.25  # 每 0.25 年（約 3 個月）模擬一次

    while years <= 80:
        n = years
        fv_lump = current_value * (1 + r) ** n
        fv_dca = yearly_invest * ((1 + r) ** n - 1) / r
        fv_total = fv_lump + fv_dca
        if fv_total >= target:
            return round(years, 1)
        years += step

    return None


# ======== 計算儀表板數據（會把 DB 交易加上去） =========

def compute_dashboard(trades_summary=None):
    """
    trades_summary: 由 get_trades_summary() 回傳的 dict {symbol: {...}}
    """
    if trades_summary is None:
        trades_summary = {}

    ensure_prices()

    etf_rows = []
    total_cost = 0.0
    total_mv = 0.0
    total_dividends = 0.0

    for h in holdings:
        symbol = h["symbol"]
        shares = h["shares"]
        cost = h["cost"]
        price = h["price"]
        name = h["name"]

        # 把 DB 裡的交易加上去：股數＋成本
        t = trades_summary.get(symbol)
        if t:
            add_shares = t["add_shares"]
            add_amount = t["add_amount"]
            if add_shares or add_amount:
                base_cost_total = cost * shares
                new_shares = shares + add_shares
                new_cost_total = base_cost_total + add_amount
                if new_shares > 0 and new_cost_total > 0:
                    cost = new_cost_total / new_shares
                    shares = new_shares

        cost_total = shares * cost
        mv = shares * price
        profit = mv - cost_total
        pl_pct = (profit / cost_total * 100) if cost_total else 0

        div_total = get_dividends_total(symbol)
        profit_with_div = profit + div_total
        pl_with_div_pct = (profit_with_div / cost_total * 100) if cost_total else 0

        total_cost += cost_total
        total_mv += mv
        total_dividends += div_total

        etf_rows.append({
            "symbol": symbol,
            "name": name,
            "shares": shares,
            "price": price,
            "cost_total": cost_total,
            "mv": mv,
            "profit": profit,
            "pl_pct": pl_pct,
            "div_total": div_total,
            "profit_with_div": profit_with_div,
            "pl_with_div_pct": pl_with_div_pct,
        })

    total_profit = total_mv - total_cost
    total_profit_with_div = total_profit + total_dividends
    total_pl_pct = (total_profit / total_cost * 100) if total_cost else 0
    total_pl_with_div_pct = (total_profit_with_div / total_cost * 100) if total_cost else 0

    current_year = datetime.now().year
    last_year = current_year - 1
    div_last_year = get_dividends_total_by_year(last_year)
    div_this_year = get_dividends_total_by_year(current_year)
    diff_div = div_this_year - div_last_year

    # DCA 數字先保留（目前沒顯示）
    dca_total_all = get_dca_total()
    profit_vs_dca = None
    pl_vs_dca_pct = None
    if dca_total_all > 0:
        profit_vs_dca = total_mv - dca_total_all
        pl_vs_dca_pct = profit_vs_dca / dca_total_all * 100

    current_mv = total_mv
    diff_low = max(0, HOUSE_TARGET_LOW - current_mv)
    diff_high = max(0, HOUSE_TARGET_HIGH - current_mv)
    years_low = estimate_years_to_target(current_mv, MONTHLY_DCA, ANNUAL_RETURN, HOUSE_TARGET_LOW)
    years_high = estimate_years_to_target(current_mv, MONTHLY_DCA, ANNUAL_RETURN, HOUSE_TARGET_HIGH)

    fill_infos = compute_fill_infos(etf_rows)

    return {
        "etfs": etf_rows,
        "totals": {
            "total_cost": total_cost,
            "total_mv": total_mv,
            "total_profit": total_profit,
            "total_profit_with_div": total_profit_with_div,
            "total_pl_pct": total_pl_pct,
            "total_pl_with_div_pct": total_pl_with_div_pct,
            "total_dividends": total_dividends,
        },
        "div_compare": {
            "current_year": current_year,
            "last_year": last_year,
            "div_last_year": div_last_year,
            "div_this_year": div_this_year,
            "diff_div": diff_div,
        },
        "dca_compare": {
            "dca_total_all": dca_total_all,
            "profit_vs_dca": profit_vs_dca,
            "pl_vs_dca_pct": pl_vs_dca_pct,
        },
        "house_goal": {
            "current_mv": current_mv,
            "diff_low": diff_low,
            "diff_high": diff_high,
            "years_low": years_low,
            "years_high": years_high,
        },
        "fill_infos": fill_infos,
    }


# ======== HTML 模板（首頁：儀表板） =========

TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>ETF & 買房儀表板</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f5f7;
      margin: 0;
      padding: 16px;
    }
    .container {
      max-width: 480px;
      margin: 0 auto 32px;
    }
    h1 {
      font-size: 24px;
      text-align: center;
      margin-bottom: 4px;
    }
    .subtitle {
      text-align: center;
      font-size: 13px;
      color: #666;
      margin-bottom: 8px;
    }
    .top-link {
      text-align: center;
      margin-bottom: 10px;
      font-size: 12px;
    }
    .top-link a {
      color: #3949ab;
      text-decoration: none;
    }
    .card {
      background: #fff;
      border-radius: 16px;
      padding: 16px 18px;
      margin-bottom: 14px;
      box-shadow: 0 6px 18px rgba(0,0,0,0.06);
    }
    .card h2 {
      font-size: 18px;
      margin: 0 0 8px;
    }
    .row {
      display: flex;
      justify-content: space-between;
      margin: 4px 0;
      font-size: 14px;
      align-items: center;
      gap: 8px;
    }
    .label {
      color: #555;
    }
    .value {
      font-weight: 600;
    }
    .big-number {
      font-size: 22px;
      font-weight: 700;
      margin: 8px 0;
    }
    .positive {
      color: #0a8f3c;
    }
    .negative {
      color: #d32f2f;
    }
    .neutral {
      color: #f9a825;
    }
    .chip {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      background: #eef2ff;
      color: #3949ab;
      margin-left: 4px;
    }
    .etf-list {
      margin-top: 8px;
      border-top: 1px solid #eee;
      padding-top: 6px;
      max-height: 260px;
      overflow-y: auto;
    }
    .etf-item {
      padding: 4px 0;
      border-bottom: 1px dashed #eee;
      font-size: 13px;
    }
    .etf-item:last-child {
      border-bottom: none;
    }
    .etf-header {
      display: flex;
      justify-content: space-between;
      font-weight: 600;
      margin-bottom: 2px;
    }
    .etf-sub {
      display: flex;
      justify-content: space-between;
      color: #666;
    }
    .note {
      font-size: 11px;
      color: #999;
      margin-top: 6px;
      line-height: 1.4;
    }
    input[type="number"], select {
      border-radius: 999px;
      border: 1px solid #ddd;
      padding: 4px 8px;
      font-size: 13px;
      flex: 1;
    }
    button {
      cursor: pointer;
    }
  </style>
</head>
<body>
<div class="container">
  <h1>ETF & 買房儀表板</h1>
  <div class="subtitle">最後更新：{{ now }}</div>
  <div class="top-link">
    <a href="{{ url_for('trades_page') }}">查看 / 編輯交易紀錄 ▶</a>
  </div>

  <!-- 新增交易 / 配息複投 -->
  <div class="card">
    <h2>新增交易 / 配息複投</h2>
    <form method="post">
      <div class="row">
        <span class="label">標的</span>
        <select name="symbol">
          {% for e in etfs %}
          <option value="{{ e.symbol }}">{{ e.symbol }} · {{ e.name }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="row">
        <span class="label">買進股數</span>
        <input type="number" name="shares" min="0" step="1">
      </div>
      <div class="row">
        <span class="label">本次總投入金額</span>
        <input type="number" name="amount" min="0" step="0.01">
      </div>
      <div class="row">
        <span class="label">其中配息複投</span>
        <input type="number" name="reinvest" min="0" step="0.01">
      </div>
      <div style="text-align:right;margin-top:10px;">
        <button type="submit"
                style="padding:6px 14px;border-radius:999px;border:none;background:#3949ab;color:#fff;font-size:13px;">
          儲存交易
        </button>
      </div>
    </form>
    <div class="note">
      歷史累積：投入 {{ fmt_money(trade_totals.total_amount) }} 元，<br>
      其中配息複投 {{ fmt_money(trade_totals.total_reinvest) }} 元，<br>
      自掏腰包 {{ fmt_money(trade_totals.total_new_cash) }} 元。
    </div>
  </div>

  <div class="card">
    <h2>投資組合總覽 <span class="chip">含息</span></h2>
    <div class="row">
      <span class="label">總成本（含歷史交易）</span>
      <span class="value">{{ fmt_money(totals.total_cost) }} 元</span>
    </div>
    <div class="row">
      <span class="label">總市值</span>
      <span class="value">{{ fmt_money(totals.total_mv) }} 元</span>
    </div>
    <div class="big-number {% if totals.total_profit_with_div > 0 %}positive{% elif totals.total_profit_with_div < 0 %}negative{% else %}neutral{% endif %}">
      含息報酬率：{{ fmt_pct(totals.total_pl_with_div_pct) }}
    </div>
    <div class="row">
      <span class="label">未實現損益</span>
      <span class="value {% if totals.total_profit > 0 %}positive{% elif totals.total_profit < 0 %}negative{% else %}neutral{% endif %}">
        {{ fmt_money(totals.total_profit) }} 元
      </span>
    </div>
    <div class="row">
      <span class="label">已領配息總額</span>
      <span class="value">{{ fmt_money(totals.total_dividends) }} 元</span>
    </div>

    <div class="etf-list">
      {% for e in etfs %}
      <div class="etf-item">
        <div class="etf-header">
          <span>{{ e.symbol }} · {{ e.name }}</span>
          <span class="{% if e.profit_with_div > 0 %}positive{% elif e.profit_with_div < 0 %}negative{% else %}neutral{% endif %}">
            {{ '%.1f' % e.pl_with_div_pct }}%
          </span>
        </div>
        <div class="etf-sub">
          <span>股數 {{ e.shares }}｜現價 {{ '%.2f' % e.price }}</span>
          <span>市值 {{ fmt_money(e.mv) }} 元</span>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>

  <div class="card">
    <h2>配息年度對比</h2>
    <div class="row">
      <span class="label">{{ div_compare.last_year }} 年配息總額</span>
      <span class="value">{{ fmt_money(div_compare.div_last_year) }} 元</span>
    </div>
    <div class="row">
      <span class="label">{{ div_compare.current_year }} 年配息總額</span>
      <span class="value">{{ fmt_money(div_compare.div_this_year) }} 元</span>
    </div>
    <div class="big-number {% if div_compare.diff_div > 0 %}positive{% elif div_compare.diff_div < 0 %}negative{% else %}neutral{% endif %}">
      {{ '今年比去年多' if div_compare.diff_div >= 0 else '今年比去年少' }}：{{ fmt_money(div_compare.diff_div) }} 元
    </div>
    <div class="note">
      以 dividends 清單中該年度所有配息現金加總計算。
    </div>
  </div>

  <div class="card">
    <h2>填息比對（最近一次配息）</h2>
    {% if fill_infos %}
      {% for f in fill_infos %}
      <div class="row">
        <span class="label">
          {{ f.symbol }} · {{ f.name }}<br>
          <span style="font-size:11px;color:#888;">最近配息日：{{ f.last_date }}</span>
        </span>
        <span class="value">
          <span class="{% if f.fill_ratio > 100 %}positive{% elif f.fill_ratio < 0 %}negative{% else %}neutral{% endif %}">
            {{ ('%.1f' % f.fill_ratio) }}%
          </span><br>
          <span style="font-size:11px;color:#666;">
            每股息約 {{ '%.3f' % f.div_per_share }} 元
          </span>
        </span>
      </div>
      <div class="row" style="font-size:12px;color:#666;">
        <span>除息前價：約 {{ '%.2f' % f.pre_close }} 元</span>
        <span>現價：{{ '%.2f' % f.now_price }} 元</span>
      </div>
      <div class="row" style="font-size:12px;color:#666;margin-bottom:6px;">
        <span>距除息前價還差</span>
        <span>{{ '%.2f' % f.gap_to_fill }} 元</span>
      </div>
      <hr style="border:none;border-top:1px dashed #eee;margin:4px 0;">
      {% endfor %}
      <div class="note">
        以「除息前收盤價 − 每股配息」為理論除息價，再用目前股價估算填息進度，僅供參考。
      </div>
    {% else %}
      <div>目前尚無可計算的填息資料。</div>
    {% endif %}
  </div>

  <div class="card">
    <h2>買房頭期款進度</h2>
    <div class="row">
      <span class="label">目前頭期（ETF 市值）</span>
      <span class="value">{{ fmt_money(house_goal.current_mv) }} 元</span>
    </div>
    <div class="row">
      <span class="label">距離 650 萬</span>
      <span class="value">{{ fmt_money(house_goal.diff_low) }} 元</span>
    </div>
    <div class="row">
      <span class="label">距離 750 萬</span>
      <span class="value">{{ fmt_money(house_goal.diff_high) }} 元</span>
    </div>
    <div class="big-number">
      650 萬：約 {{ house_goal.years_low if house_goal.years_low is not none else '-' }} 年後
    </div>
    <div class="big-number">
      750 萬：約 {{ house_goal.years_high if house_goal.years_high is not none else '-' }} 年後
    </div>
    <div class="note">
      假設年化報酬率 {{ (ANNUAL_RETURN*100)|round(1) }}%，每月定投 {{ MONTHLY_DCA }} 元（持續投入）。
    </div>
  </div>

</div>
</body>
</html>
"""

# ======== HTML 模板（交易管理頁 /trades 第二版：可篩選） =========

TRADES_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>交易紀錄管理</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f5f7;
      margin: 0;
      padding: 16px;
    }
    .container {
      max-width: 540px;
      margin: 0 auto 32px;
    }
    h1 {
      font-size: 22px;
      text-align: center;
      margin-bottom: 8px;
    }
    .subtitle {
      text-align: center;
      font-size: 12px;
      color: #666;
      margin-bottom: 8px;
    }
    .top-link {
      text-align: center;
      margin-bottom: 10px;
      font-size: 12px;
    }
    .top-link a {
      color: #3949ab;
      text-decoration: none;
    }
    .card {
      background: #fff;
      border-radius: 16px;
      padding: 14px 16px;
      margin-bottom: 10px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.06);
      font-size: 13px;
    }
    .row {
      display: flex;
      justify-content: space-between;
      margin: 2px 0;
      gap: 6px;
    }
    .label {
      color: #555;
    }
    .tag {
      display: inline-block;
      padding: 2px 6px;
      border-radius: 999px;
      background: #eef2ff;
      font-size: 11px;
      color: #3949ab;
      margin-left: 4px;
    }
    .btn-row {
      margin-top: 6px;
      text-align: right;
    }
    .btn {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      border: none;
      font-size: 12px;
      cursor: pointer;
      text-decoration: none;
      margin-left: 6px;
    }
    .btn-edit {
      background: #3949ab;
      color: #fff;
    }
    .btn-delete {
      background: #d32f2f;
      color: #fff;
    }
    .note {
      font-size: 11px;
      color: #777;
      margin-top: 6px;
      line-height: 1.4;
    }
    form.inline {
      display: inline;
    }
    .filter-card {
      background: #fff;
      border-radius: 16px;
      padding: 10px 12px;
      margin-bottom: 10px;
      box-shadow: 0 3px 8px rgba(0,0,0,0.04);
      font-size: 12px;
    }
    .filter-row {
      display: flex;
      gap: 8px;
      margin-bottom: 6px;
      align-items: center;
    }
    .filter-row label {
      font-size: 12px;
      color: #555;
      flex: 0 0 50px;
    }
    .filter-row select {
      flex: 1;
      border-radius: 999px;
      border: 1px solid #ddd;
      padding: 4px 8px;
      font-size: 12px;
    }
    .filter-actions {
      text-align: right;
      margin-top: 4px;
    }
    .btn-filter {
      padding: 4px 10px;
      border-radius: 999px;
      border: none;
      background: #3949ab;
      color: #fff;
      font-size: 12px;
      cursor: pointer;
      margin-left: 6px;
    }
    .btn-reset {
      padding: 4px 10px;
      border-radius: 999px;
      border: none;
      background: #e0e0e0;
      color: #333;
      font-size: 12px;
      cursor: pointer;
    }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: #f1f3ff;
      color: #3949ab;
      font-size: 11px;
      margin-right: 4px;
    }
  </style>
</head>
<body>
<div class="container">
  <h1>交易紀錄管理</h1>
  <div class="subtitle">
    目前顯示：{{ trades|length }} 筆
  </div>
  <div class="top-link">
    <a href="{{ url_for('index') }}">◀ 返回儀表板</a>
  </div>

  <!-- 篩選條件 -->
  <div class="filter-card">
    <form method="get">
      <div class="filter-row">
        <label>標的</label>
        <select name="symbol">
          <option value="">全部</option>
          {% for s in symbols %}
          <option value="{{ s }}" {% if s == selected_symbol %}selected{% endif %}>{{ s }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="filter-row">
        <label>年份</label>
        <select name="year">
          <option value="">全部</option>
          {% for y in years %}
          <option value="{{ y }}" {% if y == selected_year %}selected{% endif %}>{{ y }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="filter-actions">
        <button type="submit" class="btn-filter">套用篩選</button>
        <button type="button" class="btn-reset" onclick="window.location='{{ url_for('trades_page') }}'">
          清除
        </button>
      </div>
    </form>
    <div class="note">
      篩選中：
      {% if selected_symbol %}
        <span class="pill">標的：{{ selected_symbol }}</span>
      {% else %}
        <span class="pill">標的：全部</span>
      {% endif %}
      {% if selected_year %}
        <span class="pill">年份：{{ selected_year }}</span>
      {% else %}
        <span class="pill">年份：全部</span>
      {% endif %}
    </div>
  </div>

  <!-- 全部累積總覽 -->
  <div class="card">
    <div class="row">
      <span class="label">累積總投入（全部）</span>
      <span>{{ fmt_money(trade_totals.total_amount) }} 元</span>
    </div>
    <div class="row">
      <span class="label">其中配息複投</span>
      <span>{{ fmt_money(trade_totals.total_reinvest) }} 元</span>
    </div>
    <div class="row">
      <span class="label">自掏腰包</span>
      <span>{{ fmt_money(trade_totals.total_new_cash) }} 元</span>
    </div>
    <div class="note">
      以上為「所有年份＋所有標的」的累積數字，不受上方篩選影響。
    </div>
  </div>

  <!-- 目前篩選的小計 -->
  <div class="card">
    <div class="row">
      <span class="label">篩選後投入小計</span>
      <span>{{ fmt_money(filtered_totals.total_amount) }} 元</span>
    </div>
    <div class="row">
      <span class="label">其中配息複投</span>
      <span>{{ fmt_money(filtered_totals.total_reinvest) }} 元</span>
    </div>
    <div class="row">
      <span class="label">自掏腰包</span>
      <span>{{ fmt_money(filtered_totals.total_new_cash) }} 元</span>
    </div>
    <div class="note">
      僅計算目前篩選條件下（標的 / 年份）的交易紀錄合計。
    </div>
  </div>

  {% for t in trades %}
  <div class="card">
    <div class="row">
      <span class="label">
        #{{ t.id }}
        <span class="tag">{{ t.symbol }}</span>
      </span>
      <span>{{ t.ts }}</span>
    </div>
    <div class="row">
      <span class="label">股數</span>
      <span>{{ t.shares }}</span>
    </div>
    <div class="row">
      <span class="label">總投入金額</span>
      <span>{{ fmt_money(t.amount) }} 元</span>
    </div>
    <div class="row">
      <span class="label">其中配息複投</span>
      <span>{{ fmt_money(t.reinvest) }} 元</span>
    </div>
    <div class="btn-row">
      <a class="btn btn-edit" href="{{ url_for('edit_trade', trade_id=t.id) }}">編輯</a>
      <form class="inline"
            method="post"
            action="{{ url_for('delete_trade', trade_id=t.id) }}"
            onsubmit="return confirm('確定要刪除這筆交易嗎？\\nID: {{ t.id }}  標的: {{ t.symbol }}');">
        <button type="submit" class="btn btn-delete">刪除</button>
      </form>
    </div>
  </div>
  {% else %}
  <div class="card">
    目前在此篩選條件下，沒有任何交易紀錄。
  </div>
  {% endfor %}

</div>
</body>
</html>
"""

# ======== HTML 模板（交易編輯頁 /trades/edit/<id>） =========

EDIT_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>編輯交易 #{{ trade.id }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f5f7;
      margin: 0;
      padding: 16px;
    }
    .container {
      max-width: 480px;
      margin: 0 auto 32px;
    }
    h1 {
      font-size: 20px;
      text-align: center;
      margin-bottom: 8px;
    }
    .subtitle {
      text-align: center;
      font-size: 12px;
      color: #666;
      margin-bottom: 12px;
    }
    .card {
      background: #fff;
      border-radius: 16px;
      padding: 16px 18px;
      margin-bottom: 14px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.06);
    }
    .row {
      display: flex;
      justify-content: space-between;
      margin: 6px 0;
      gap: 8px;
      font-size: 14px;
      align-items: center;
    }
    .label {
      color: #555;
      flex: 0 0 90px;
    }
    input[type="number"], input[type="text"] {
      flex: 1;
      border-radius: 999px;
      border: 1px solid #ddd;
      padding: 6px 10px;
      font-size: 14px;
    }
    .note {
      font-size: 11px;
      color: #777;
      margin-top: 6px;
      line-height: 1.4;
    }
    .btn-row {
      margin-top: 10px;
      text-align: right;
    }
    .btn {
      display: inline-block;
      padding: 6px 14px;
      border-radius: 999px;
      border: none;
      font-size: 13px;
      cursor: pointer;
      text-decoration: none;
      margin-left: 8px;
    }
    .btn-primary {
      background: #3949ab;
      color: #fff;
    }
    .btn-secondary {
      background: #e0e0e0;
      color: #333;
    }
  </style>
</head>
<body>
<div class="container">
  <h1>編輯交易 #{{ trade.id }}</h1>
  <div class="subtitle">
    標的：{{ trade.symbol }}
  </div>

  <div class="card">
    <form method="post">
      <div class="row">
        <span class="label">日期時間</span>
        <input type="text" name="ts" value="{{ trade.ts }}">
      </div>
      <div class="row">
        <span class="label">股數</span>
        <input type="number" name="shares" min="0" step="1" value="{{ trade.shares }}">
      </div>
      <div class="row">
        <span class="label">總投入金額</span>
        <input type="number" name="amount" min="0" step="0.01" value="{{ trade.amount }}">
      </div>
      <div class="row">
        <span class="label">配息複投</span>
        <input type="number" name="reinvest" min="0" step="0.01" value="{{ trade.reinvest }}">
      </div>

      <div class="note">
        建議日期時間保持 ISO 格式：YYYY-MM-DD HH:MM:SS<br>
        例如：2025-12-09 08:30:00
      </div>

      <div class="btn-row">
        <a href="{{ url_for('trades_page') }}" class="btn btn-secondary">取消</a>
        <button type="submit" class="btn btn-primary">儲存變更</button>
      </div>
    </form>
  </div>
</div>
</body>
</html>
"""

# ======== Flask App =========

app = Flask(__name__)
init_db()


@app.route("/", methods=["GET", "POST"])
def index():
    # 處理新增交易表單
    if request.method == "POST":
        symbol = request.form.get("symbol")
        shares_raw = request.form.get("shares", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        reinvest_raw = request.form.get("reinvest", "").strip()

        try:
            shares = int(shares_raw) if shares_raw else 0
        except ValueError:
            shares = 0

        try:
            amount = float(amount_raw) if amount_raw else 0.0
        except ValueError:
            amount = 0.0

        try:
            reinvest = float(reinvest_raw) if reinvest_raw else 0.0
        except ValueError:
            reinvest = 0.0

        if symbol and shares > 0 and amount > 0:
            conn = get_db()
            conn.execute(
                "INSERT INTO trades (ts, symbol, shares, amount, reinvest) VALUES (?,?,?,?,?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), symbol, shares, amount, reinvest),
            )
            conn.commit()
            conn.close()

    trades_summary, total_amount, total_reinvest, total_new_cash = get_trades_summary()
    data = compute_dashboard(trades_summary=trades_summary)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    trade_totals = {
        "total_amount": total_amount,
        "total_reinvest": total_reinvest,
        "total_new_cash": total_new_cash,
    }

    return render_template_string(
        TEMPLATE,
        now=now,
        fmt_money=fmt_money,
        fmt_pct=fmt_pct,
        ANNUAL_RETURN=ANNUAL_RETURN,
        MONTHLY_DCA=MONTHLY_DCA,
        trade_totals=trade_totals,
        **data,
    )


@app.route("/trades")
def trades_page():
    conn = get_db()

    # 取得所有 symbol / 年份供下拉選單用
    cur = conn.execute("SELECT DISTINCT symbol FROM trades ORDER BY symbol")
    symbols_rows = cur.fetchall()
    symbols = [r["symbol"] for r in symbols_rows]

    cur = conn.execute("SELECT DISTINCT substr(ts,1,4) AS y FROM trades ORDER BY y DESC")
    years_rows = cur.fetchall()
    years = [r["y"] for r in years_rows if r["y"]]

    # 讀取篩選條件
    selected_symbol = request.args.get("symbol", "").strip()
    selected_year = request.args.get("year", "").strip()

    # 依篩選條件取出交易紀錄
    sql = "SELECT * FROM trades WHERE 1=1"
    params = []

    if selected_symbol:
        sql += " AND symbol = ?"
        params.append(selected_symbol)

    if selected_year:
        sql += " AND substr(ts,1,4) = ?"
        params.append(selected_year)

    sql += " ORDER BY ts DESC, id DESC"
    cur = conn.execute(sql, params)
    rows = cur.fetchall()

    # 全部累積
    trades_summary, total_amount, total_reinvest, total_new_cash = get_trades_summary()
    trade_totals = {
        "total_amount": total_amount,
        "total_reinvest": total_reinvest,
        "total_new_cash": total_new_cash,
    }

    # 篩選後小計
    filtered_amount = sum(r["amount"] for r in rows) if rows else 0.0
    filtered_reinvest = sum(r["reinvest"] for r in rows) if rows else 0.0
    filtered_new_cash = max(0.0, filtered_amount - filtered_reinvest)
    filtered_totals = {
        "total_amount": filtered_amount,
        "total_reinvest": filtered_reinvest,
        "total_new_cash": filtered_new_cash,
    }

    conn.close()

    return render_template_string(
        TRADES_TEMPLATE,
        trades=rows,
        trade_totals=trade_totals,
        filtered_totals=filtered_totals,
        symbols=symbols,
        years=years,
        selected_symbol=selected_symbol,
        selected_year=selected_year,
        fmt_money=fmt_money,
    )


@app.route("/trades/edit/<int:trade_id>", methods=["GET", "POST"])
def edit_trade(trade_id):
    conn = get_db()
    cur = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return "Trade not found", 404

    if request.method == "POST":
        ts = request.form.get("ts", "").strip()
        shares_raw = request.form.get("shares", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        reinvest_raw = request.form.get("reinvest", "").strip()

        try:
            shares = int(shares_raw) if shares_raw else 0
        except ValueError:
            shares = 0

        try:
            amount = float(amount_raw) if amount_raw else 0.0
        except ValueError:
            amount = 0.0

        try:
            reinvest = float(reinvest_raw) if reinvest_raw else 0.0
        except ValueError:
            reinvest = 0.0

        if not ts:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn.execute(
            "UPDATE trades SET ts = ?, shares = ?, amount = ?, reinvest = ? WHERE id = ?",
            (ts, shares, amount, reinvest, trade_id),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("trades_page"))

    conn.close()
    return render_template_string(
        EDIT_TEMPLATE,
        trade=row,
    )


@app.route("/trades/delete/<int:trade_id>", methods=["POST"])
def delete_trade(trade_id):
    conn = get_db()
    conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("trades_page"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

