# app.py
# 雲端版：完全前端可操作的 ETF + 買房頭期款手機儀表板（for Render）
# 功能：
# - 持股 / 配息 / DCA / 交易 都用 SQLite 存，從網頁操作，不用改程式碼
# - 儀表板顯示：投資組合總覽、配息年度對比、填息比對、買房頭期款進度
# - 管理頁：/holdings /dividends /dca /trades

import yfinance as yf
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, redirect, url_for
import sqlite3
from pathlib import Path

# ======== 全域設定：買房目標與定投金額 =========
HOUSE_TARGET_LOW = 6_500_000   # 下限：650 萬
HOUSE_TARGET_HIGH = 7_500_000  # 上限：750 萬
ANNUAL_RETURN = 0.06           # 假設年化報酬率 6%
MONTHLY_DCA = 5_000            # 每月定投總額（僅作估算用）

# 抓不到股價時的預設值
DEFAULT_PRICES = {
    "0050": 150.0,
    "0056": 35.0,
    "00878": 23.0,
    "00919": 24.0,
}

# ======== SQLite 資料庫設定 =========
DB_PATH = Path(__file__).with_name("portfolio_full.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    # 交易紀錄
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
    # 持股
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            shares INTEGER NOT NULL,
            cost REAL NOT NULL
        )
        """
    )
    # 配息
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dividends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            cash REAL NOT NULL,
            note TEXT
        )
        """
    )
    # 定期定額
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dca (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            amount REAL NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


# ======== 工具：格式化 =========

def fmt_money(x):
    return f"{x:,.0f}"


def fmt_pct(x):
    return f"{x:,.2f}%"


# ======== yfinance 抓股價 =========

def fetch_price_tw(symbol):
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


# ======== DB 讀取工具 =========

def get_all_holdings():
    conn = get_db()
    cur = conn.execute("SELECT * FROM holdings ORDER BY symbol")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_dividends():
    conn = get_db()
    cur = conn.execute("SELECT * FROM dividends ORDER BY date DESC, id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_dca():
    conn = get_db()
    cur = conn.execute("SELECT * FROM dca ORDER BY date DESC, id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_trades_summary():
    """
    回傳：
      summary: {symbol: {add_shares, add_amount, add_reinvest}}
      total_amount / total_reinvest / total_new_cash
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


# ======== 配息 / DCA 計算工具 =========

def get_dividends_total(symbol):
    conn = get_db()
    cur = conn.execute(
        "SELECT COALESCE(SUM(cash),0) AS s FROM dividends WHERE symbol = ?",
        (symbol,),
    )
    row = cur.fetchone()
    conn.close()
    return row["s"] if row and row["s"] is not None else 0.0


def get_dividends_total_by_year(year):
    conn = get_db()
    cur = conn.execute(
        "SELECT COALESCE(SUM(cash),0) AS s FROM dividends WHERE substr(date,1,4) = ?",
        (str(year),),
    )
    row = cur.fetchone()
    conn.close()
    return row["s"] if row and row["s"] is not None else 0.0


def get_last_dividend_event(symbol):
    conn = get_db()
    cur = conn.execute(
        "SELECT * FROM dividends WHERE symbol = ? ORDER BY date DESC, id DESC LIMIT 1",
        (symbol,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_dca_total(year=None):
    conn = get_db()
    if year is None:
        cur = conn.execute("SELECT COALESCE(SUM(amount),0) AS s FROM dca")
        row = cur.fetchone()
    else:
        cur = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM dca WHERE substr(date,1,4) = ?",
            (str(year),),
        )
        row = cur.fetchone()
    conn.close()
    return row["s"] if row and row["s"] is not None else 0.0


# ======== 填息計算 =========

def get_pre_ex_close_price(symbol, ex_date_str):
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
            continue

        last_date = last_ev["date"]
        cash_total = last_ev["cash"]

        if not shares or shares <= 0:
            continue

        div_per_share = cash_total / shares

        pre_close = get_pre_ex_close_price(symbol, last_date)
        if pre_close is None:
            continue

        ex_ref_price = pre_close - div_per_share
        filled_amount = now_price - ex_ref_price

        if div_per_share > 0:
            fill_ratio = filled_amount / div_per_share * 100
        else:
            fill_ratio = 0.0

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


# ======== 買房目標試算 =========

def estimate_years_to_target(current_value, monthly_invest, annual_return, target):
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


# ======== 儀表板計算 =========

def compute_dashboard():
    holdings_rows = list(get_all_holdings())
    etf_rows = []

    total_cost = 0.0
    total_mv = 0.0
    total_dividends = 0.0

    price_cache = {}

    for h in holdings_rows:
        symbol = h["symbol"]
        name = h["name"]
        shares = h["shares"]
        cost = h["cost"]

        if symbol in price_cache:
            price = price_cache[symbol]
        else:
            price = fetch_price_tw(symbol)
            price_cache[symbol] = price

        cost_total = shares * cost
        mv = shares * price
        profit = mv - cost_total
        pl_pct = (profit / cost_total * 100) if cost_total else 0.0

        div_total = get_dividends_total(symbol)
        profit_with_div = profit + div_total
        pl_with_div_pct = (profit_with_div / cost_total * 100) if cost_total else 0.0

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
    total_pl_pct = (total_profit / total_cost * 100) if total_cost else 0.0
    total_pl_with_div_pct = (total_profit_with_div / total_cost * 100) if total_cost else 0.0

    current_year = datetime.now().year
    last_year = current_year - 1
    div_last_year = get_dividends_total_by_year(last_year)
    div_this_year = get_dividends_total_by_year(current_year)
    diff_div = div_this_year - div_last_year

    dca_total_all = get_dca_total()
    profit_vs_dca = None
    pl_vs_dca_pct = None
    if dca_total_all > 0 and total_mv > 0:
        profit_vs_dca = total_mv - dca_total_all
        pl_vs_dca_pct = profit_vs_dca / dca_total_all * 100.0

    current_mv = total_mv
    diff_low = max(0.0, HOUSE_TARGET_LOW - current_mv)
    diff_high = max(0.0, HOUSE_TARGET_HIGH - current_mv)
    years_low = estimate_years_to_target(current_mv, MONTHLY_DCA, ANNUAL_RETURN, HOUSE_TARGET_LOW) if current_mv > 0 else None
    years_high = estimate_years_to_target(current_mv, MONTHLY_DCA, ANNUAL_RETURN, HOUSE_TARGET_HIGH) if current_mv > 0 else None

    fill_infos = compute_fill_infos(etf_rows) if etf_rows else []

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


# ======== HTML 模板：首頁（儀表板） =========

TEMPLATE_INDEX = """
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
    .nav {
      text-align: center;
      font-size: 12px;
      margin-bottom: 10px;
      line-height: 1.6;
    }
    .nav a {
      color: #3949ab;
      text-decoration: none;
      margin: 0 4px;
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
    input[type="number"], input[type="date"], select {
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
  <div class="nav">
    <a href="{{ url_for('holdings_page') }}">持股管理</a> ·
    <a href="{{ url_for('dividends_page') }}">配息管理</a> ·
    <a href="{{ url_for('dca_page') }}">DCA 管理</a> ·
    <a href="{{ url_for('trades_page') }}">交易紀錄</a>
  </div>

  <!-- 新增交易 / 配息複投 -->
  <div class="card">
    <h2>新增交易 / 配息複投</h2>
    {% if etfs %}
    <form method="post">
      <div class="row">
        <span class="label">日期</span>
        <input type="date" name="date" value="{{ today_date }}">
      </div>
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
    {% else %}
      <div class="note">
        目前尚未設定任何持股，請先到「持股管理」頁面新增至少一檔 ETF。
      </div>
    {% endif %}
    <div class="note">
      歷史累積：投入 {{ fmt_money(trade_totals.total_amount) }} 元，<br>
      其中配息複投 {{ fmt_money(trade_totals.total_reinvest) }} 元，<br>
      自掏腰包 {{ fmt_money(trade_totals.total_new_cash) }} 元。
    </div>
  </div>

  <div class="card">
    <h2>投資組合總覽 <span class="chip">含息</span></h2>
    {% if etfs %}
      <div class="row">
        <span class="label">總成本（以持股表為準）</span>
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
    {% else %}
      <div>尚未設定持股資料。</div>
    {% endif %}
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
      以「配息管理」中填寫的各筆配息紀錄加總計算。
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
      <div>目前尚無可計算的填息資料（請先在持股與配息管理中建立資料）。</div>
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

# ======== HTML 模板：持股管理 =========

TEMPLATE_HOLDINGS = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>持股管理</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f5f7;margin:0;padding:16px;}
    .container{max-width:540px;margin:0 auto 32px;}
    h1{font-size:22px;text-align:center;margin-bottom:8px;}
    .subtitle{text-align:center;font-size:12px;color:#666;margin-bottom:10px;}
    .top-link{text-align:center;margin-bottom:10px;font-size:12px;}
    .top-link a{color:#3949ab;text-decoration:none;}
    .card{background:#fff;border-radius:16px;padding:14px 16px;margin-bottom:10px;box-shadow:0 4px 12px rgba(0,0,0,0.06);font-size:13px;}
    .row{display:flex;justify-content:space-between;margin:4px 0;gap:6px;align-items:center;}
    .label{color:#555;}
    input[type="text"],input[type="number"]{flex:1;border-radius:999px;border:1px solid #ddd;padding:4px 8px;font-size:13px;}
    .btn-row{text-align:right;margin-top:8px;}
    .btn{display:inline-block;padding:4px 10px;border-radius:999px;border:none;font-size:12px;cursor:pointer;text-decoration:none;margin-left:6px;}
    .btn-primary{background:#3949ab;color:#fff;}
    .btn-secondary{background:#e0e0e0;color:#333;}
    .btn-danger{background:#d32f2f;color:#fff;}
    .tag{display:inline-block;padding:2px 6px;border-radius:999px;background:#eef2ff;font-size:11px;color:#3949ab;margin-left:4px;}
    form.inline{display:inline;}
    .note{font-size:11px;color:#777;margin-top:6px;line-height:1.4;}
  </style>
</head>
<body>
<div class="container">
  <h1>持股管理</h1>
  <div class="subtitle">設定目前各檔 ETF 的「總股數」與「平均成本」</div>
  <div class="top-link">
    <a href="{{ url_for('index') }}">◀ 返回儀表板</a>
  </div>

  <div class="card">
    <form method="post">
      <div class="row">
        <span class="label">代號</span>
        <input type="text" name="symbol" placeholder="例如 0050" required>
      </div>
      <div class="row">
        <span class="label">名稱</span>
        <input type="text" name="name" placeholder="例如 元大台灣50" required>
      </div>
      <div class="row">
        <span class="label">總股數</span>
        <input type="number" name="shares" min="0" step="1" required>
      </div>
      <div class="row">
        <span class="label">平均成本</span>
        <input type="number" name="cost" min="0" step="0.01" required>
      </div>
      <div class="btn-row">
        <button type="submit" class="btn btn-primary">新增持股</button>
      </div>
      <div class="note">
        若同一檔 ETF 想修改，建議直接編輯既有那一筆，而不是重複新增多筆。
      </div>
    </form>
  </div>

  {% for h in holdings %}
  <div class="card">
    <div class="row">
      <span class="label">
        #{{ h.id }}
        <span class="tag">{{ h.symbol }}</span>
      </span>
      <span>{{ h.name }}</span>
    </div>
    <div class="row">
      <span class="label">總股數</span>
      <span>{{ h.shares }}</span>
    </div>
    <div class="row">
      <span class="label">平均成本</span>
      <span>{{ '%.2f' % h.cost }} 元</span>
    </div>
    <div class="btn-row">
      <a href="{{ url_for('edit_holding', holding_id=h.id) }}" class="btn btn-secondary">編輯</a>
      <form class="inline" method="post"
            action="{{ url_for('delete_holding', holding_id=h.id) }}"
            onsubmit="return confirm('確定要刪除這檔持股嗎？\\nID: {{ h.id }}  標的: {{ h.symbol }}');">
        <button type="submit" class="btn btn-danger">刪除</button>
      </form>
    </div>
  </div>
  {% else %}
  <div class="card">
    目前尚未新增任何持股。
  </div>
  {% endfor %}
</div>
</body>
</html>
"""

# ======== HTML 模板：持股編輯 =========

TEMPLATE_HOLDINGS_EDIT = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>編輯持股 #{{ h.id }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f5f7;margin:0;padding:16px;}
    .container{max-width:480px;margin:0 auto 32px;}
    h1{font-size:20px;text-align:center;margin-bottom:8px;}
    .subtitle{text-align:center;font-size:12px;color:#666;margin-bottom:12px;}
    .card{background:#fff;border-radius:16px;padding:16px 18px;margin-bottom:14px;box-shadow:0 4px 12px rgba(0,0,0,0.06);}
    .row{display:flex;justify-content:space-between;margin:6px 0;gap:8px;align-items:center;font-size:14px;}
    .label{color:#555;flex:0 0 80px;}
    input[type="text"],input[type="number"]{flex:1;border-radius:999px;border:1px solid #ddd;padding:6px 10px;font-size:14px;}
    .btn-row{text-align:right;margin-top:10px;}
    .btn{display:inline-block;padding:6px 14px;border-radius:999px;border:none;font-size:13px;cursor:pointer;text-decoration:none;margin-left:8px;}
    .btn-primary{background:#3949ab;color:#fff;}
    .btn-secondary{background:#e0e0e0;color:#333;}
  </style>
</head>
<body>
<div class="container">
  <h1>編輯持股 #{{ h.id }}</h1>
  <div class="subtitle">標的：{{ h.symbol }}</div>

  <div class="card">
    <form method="post">
      <div class="row">
        <span class="label">代號</span>
        <input type="text" name="symbol" value="{{ h.symbol }}">
      </div>
      <div class="row">
        <span class="label">名稱</span>
        <input type="text" name="name" value="{{ h.name }}">
      </div>
      <div class="row">
        <span class="label">總股數</span>
        <input type="number" name="shares" min="0" step="1" value="{{ h.shares }}">
      </div>
      <div class="row">
        <span class="label">平均成本</span>
        <input type="number" name="cost" min="0" step="0.01" value="{{ h.cost }}">
      </div>
      <div class="btn-row">
        <a href="{{ url_for('holdings_page') }}" class="btn btn-secondary">取消</a>
        <button type="submit" class="btn btn-primary">儲存變更</button>
      </div>
    </form>
  </div>
</div>
</body>
</html>
"""

# ======== HTML 模板：配息管理 =========

TEMPLATE_DIVIDENDS = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>配息管理</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f5f7;margin:0;padding:16px;}
    .container{max-width:540px;margin:0 auto 32px;}
    h1{font-size:22px;text-align:center;margin-bottom:8px;}
    .subtitle{text-align:center;font-size:12px;color:#666;margin-bottom:10px;}
    .top-link{text-align:center;margin-bottom:10px;font-size:12px;}
    .top-link a{color:#3949ab;text-decoration:none;}
    .card{background:#fff;border-radius:16px;padding:14px 16px;margin-bottom:10px;box-shadow:0 4px 12px rgba(0,0,0,0.06);font-size:13px;}
    .row{display:flex;justify-content:space-between;margin:4px 0;gap:6px;align-items:center;}
    .label{color:#555;}
    input[type="text"],input[type="number"],input[type="date"]{flex:1;border-radius:999px;border:1px solid #ddd;padding:4px 8px;font-size:13px;}
    .btn-row{text-align:right;margin-top:8px;}
    .btn{display:inline-block;padding:4px 10px;border-radius:999px;border:none;font-size:12px;cursor:pointer;text-decoration:none;margin-left:6px;}
    .btn-primary{background:#3949ab;color:#fff;}
    .btn-secondary{background:#e0e0e0;color:#333;}
    .btn-danger{background:#d32f2f;color:#fff;}
    .tag{display:inline-block;padding:2px 6px;border-radius:999px;background:#eef2ff;font-size:11px;color:#3949ab;margin-left:4px;}
    form.inline{display:inline;}
    .note{font-size:11px;color:#777;margin-top:6px;line-height:1.4;}
  </style>
</head>
<body>
<div class="container">
  <h1>配息管理</h1>
  <div class="subtitle">記錄每次配息金額，供儀表板統計 & 填息比對使用</div>
  <div class="top-link">
    <a href="{{ url_for('index') }}">◀ 返回儀表板</a>
  </div>

  <div class="card">
    <form method="post">
      <div class="row">
        <span class="label">日期</span>
        <input type="date" name="date" required>
      </div>
      <div class="row">
        <span class="label">標的</span>
        <input type="text" name="symbol" placeholder="例如 00919" required>
      </div>
      <div class="row">
        <span class="label">配息總額</span>
        <input type="number" name="cash" min="0" step="0.01" required>
      </div>
      <div class="row">
        <span class="label">備註</span>
        <input type="text" name="note" placeholder="例如 2025/09 配息">
      </div>
      <div class="btn-row">
        <button type="submit" class="btn btn-primary">新增配息紀錄</button>
      </div>
      <div class="note">
        可輸入每次「實際入帳」的配息，之後可用來看年度總額與填息速度。
      </div>
    </form>
  </div>

  {% for d in dividends %}
  <div class="card">
    <div class="row">
      <span class="label">
        #{{ d.id }}
        <span class="tag">{{ d.symbol }}</span>
      </span>
      <span>{{ d.date }}</span>
    </div>
    <div class="row">
      <span class="label">現金</span>
      <span>{{ fmt_money(d.cash) }} 元</span>
    </div>
    {% if d.note %}
    <div class="row">
      <span class="label">備註</span>
      <span>{{ d.note }}</span>
    </div>
    {% endif %}
    <div class="btn-row">
      <a href="{{ url_for('edit_dividend', div_id=d.id) }}" class="btn btn-secondary">編輯</a>
      <form class="inline" method="post"
            action="{{ url_for('delete_dividend', div_id=d.id) }}"
            onsubmit="return confirm('確定要刪除這筆配息紀錄嗎？\\nID: {{ d.id }}  標的: {{ d.symbol }}');">
        <button type="submit" class="btn btn-danger">刪除</button>
      </form>
    </div>
  </div>
  {% else %}
  <div class="card">
    目前尚未新增任何配息紀錄。
  </div>
  {% endfor %}
</div>
</body>
</html>
"""

# ======== HTML 模板：配息編輯 =========

TEMPLATE_DIVIDENDS_EDIT = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>編輯配息 #{{ d.id }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f5f7;margin:0;padding:16px;}
    .container{max-width:480px;margin:0 auto 32px;}
    h1{font-size:20px;text-align:center;margin-bottom:8px;}
    .subtitle{text-align:center;font-size:12px;color:#666;margin-bottom:12px;}
    .card{background:#fff;border-radius:16px;padding:16px 18px;margin-bottom:14px;box-shadow:0 4px 12px rgba(0,0,0,0.06);}
    .row{display:flex;justify-content:space-between;margin:6px 0;gap:8px;align-items:center;font-size:14px;}
    .label{color:#555;flex:0 0 80px;}
    input[type="text"],input[type="number"],input[type="date"]{flex:1;border-radius:999px;border:1px solid #ddd;padding:6px 10px;font-size:14px;}
    .btn-row{text-align:right;margin-top:10px;}
    .btn{display:inline-block;padding:6px 14px;border-radius:999px;border:none;font-size:13px;cursor:pointer;text-decoration:none;margin-left:8px;}
    .btn-primary{background:#3949ab;color:#fff;}
    .btn-secondary{background:#e0e0e0;color:#333;}
  </style>
</head>
<body>
<div class="container">
  <h1>編輯配息 #{{ d.id }}</h1>
  <div class="subtitle">標的：{{ d.symbol }}</div>

  <div class="card">
    <form method="post">
      <div class="row">
        <span class="label">日期</span>
        <input type="date" name="date" value="{{ d.date }}">
      </div>
      <div class="row">
        <span class="label">標的</span>
        <input type="text" name="symbol" value="{{ d.symbol }}">
      </div>
      <div class="row">
        <span class="label">現金</span>
        <input type="number" name="cash" min="0" step="0.01" value="{{ d.cash }}">
      </div>
      <div class="row">
        <span class="label">備註</span>
        <input type="text" name="note" value="{{ d.note or '' }}">
      </div>
      <div class="btn-row">
        <a href="{{ url_for('dividends_page') }}" class="btn btn-secondary">取消</a>
        <button type="submit" class="btn btn-primary">儲存變更</button>
      </div>
    </form>
  </div>
</div>
</body>
</html>
"""

# ======== HTML 模板：DCA 管理 =========

TEMPLATE_DCA = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>DCA 管理</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f5f7;margin:0;padding:16px;}
    .container{max-width:540px;margin:0 auto 32px;}
    h1{font-size:22px;text-align:center;margin-bottom:8px;}
    .subtitle{text-align:center;font-size:12px;color:#666;margin-bottom:10px;}
    .top-link{text-align:center;margin-bottom:10px;font-size:12px;}
    .top-link a{color:#3949ab;text-decoration:none;}
    .card{background:#fff;border-radius:16px;padding:14px 16px;margin-bottom:10px;box-shadow:0 4px 12px rgba(0,0,0,0.06);font-size:13px;}
    .row{display:flex;justify-content:space-between;margin:4px 0;gap:6px;align-items:center;}
    .label{color:#555;}
    input[type="text"],input[type="number"],input[type="date"]{flex:1;border-radius:999px;border:1px solid #ddd;padding:4px 8px;font-size:13px;}
    .btn-row{text-align:right;margin-top:8px;}
    .btn{display:inline-block;padding:4px 10px;border-radius:999px;border:none;font-size:12px;cursor:pointer;text-decoration:none;margin-left:6px;}
    .btn-primary{background:#3949ab;color:#fff;}
    .btn-secondary{background:#e0e0e0;color:#333;}
    .btn-danger{background:#d32f2f;color:#fff;}
    .tag{display:inline-block;padding:2px 6px;border-radius:999px;background:#eef2ff;font-size:11px;color:#3949ab;margin-left:4px;}
    form.inline{display:inline;}
    .note{font-size:11px;color:#777;margin-top:6px;line-height:1.4;}
  </style>
</head>
<body>
<div class="container">
  <h1>DCA 管理</h1>
  <div class="subtitle">記錄每月定期定額投入金額，供自己回顧</div>
  <div class="top-link">
    <a href="{{ url_for('index') }}">◀ 返回儀表板</a>
  </div>

  <div class="card">
    <form method="post">
      <div class="row">
        <span class="label">日期</span>
        <input type="date" name="date" required>
      </div>
      <div class="row">
        <span class="label">標的</span>
        <input type="text" name="symbol" placeholder="例如 0050" required>
      </div>
      <div class="row">
        <span class="label">金額</span>
        <input type="number" name="amount" min="0" step="0.01" required>
      </div>
      <div class="btn-row">
        <button type="submit" class="btn btn-primary">新增 DCA 紀錄</button>
      </div>
      <div class="note">
        這裡只是記錄用途，不會自動改變「持股管理」中的股數與成本。
      </div>
    </form>
  </div>

  {% for r in records %}
  <div class="card">
    <div class="row">
      <span class="label">
        #{{ r.id }}
        <span class="tag">{{ r.symbol }}</span>
      </span>
      <span>{{ r.date }}</span>
    </div>
    <div class="row">
      <span class="label">金額</span>
      <span>{{ fmt_money(r.amount) }} 元</span>
    </div>
    <div class="btn-row">
      <a href="{{ url_for('edit_dca', dca_id=r.id) }}" class="btn btn-secondary">編輯</a>
      <form class="inline" method="post"
            action="{{ url_for('delete_dca', dca_id=r.id) }}"
            onsubmit="return confirm('確定要刪除這筆 DCA 紀錄嗎？\\nID: {{ r.id }}  標的: {{ r.symbol }}');">
        <button type="submit" class="btn btn-danger">刪除</button>
      </form>
    </div>
  </div>
  {% else %}
  <div class="card">
    目前尚未新增任何 DCA 紀錄。
  </div>
  {% endfor %}
</div>
</body>
</html>
"""

# ======== HTML 模板：DCA 編輯 =========

TEMPLATE_DCA_EDIT = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>編輯 DCA #{{ r.id }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f5f7;margin:0;padding:16px;}
    .container{max-width:480px;margin:0 auto 32px;}
    h1{font-size:20px;text-align:center;margin-bottom:8px;}
    .subtitle{text-align:center;font-size:12px;color:#666;margin-bottom:12px;}
    .card{background:#fff;border-radius:16px;padding:16px 18px;margin-bottom:14px;box-shadow:0 4px 12px rgba(0,0,0,0.06);}
    .row{display:flex;justify-content:space-between;margin:6px 0;gap:8px;align-items:center;font-size:14px;}
    .label{color:#555;flex:0 0 80px;}
    input[type="text"],input[type="number"],input[type="date"]{flex:1;border-radius:999px;border:1px solid:#ddd;padding:6px 10px;font-size:14px;}
    .btn-row{text-align:right;margin-top:10px;}
    .btn{display:inline-block;padding:6px 14px;border-radius:999px;border:none;font-size:13px;cursor:pointer;text-decoration:none;margin-left:8px;}
    .btn-primary{background:#3949ab;color:#fff;}
    .btn-secondary{background:#e0e0e0;color:#333;}
  </style>
</head>
<body>
<div class="container">
  <h1>編輯 DCA #{{ r.id }}</h1>
  <div class="subtitle">標的：{{ r.symbol }}</div>

  <div class="card">
    <form method="post">
      <div class="row">
        <span class="label">日期</span>
        <input type="date" name="date" value="{{ r.date }}">
      </div>
      <div class="row">
        <span class="label">標的</span>
        <input type="text" name="symbol" value="{{ r.symbol }}">
      </div>
      <div class="row">
        <span class="label">金額</span>
        <input type="number" name="amount" min="0" step="0.01" value="{{ r.amount }}">
      </div>
      <div class="btn-row">
        <a href="{{ url_for('dca_page') }}" class="btn btn-secondary">取消</a>
        <button type="submit" class="btn btn-primary">儲存變更</button>
      </div>
    </form>
  </div>
</div>
</body>
</html>
"""

# ======== HTML 模板：交易管理 =========

TEMPLATE_TRADES = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>交易紀錄管理</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f5f7;margin:0;padding:16px;}
    .container{max-width:540px;margin:0 auto 32px;}
    h1{font-size:22px;text-align:center;margin-bottom:8px;}
    .subtitle{text-align:center;font-size:12px;color:#666;margin-bottom:8px;}
    .top-link{textalign:center;margin-bottom:10px;font-size:12px;}
    .top-link a{color:#3949ab;text-decoration:none;}
    .card{background:#fff;border-radius:16px;padding:14px 16px;margin-bottom:10px;box-shadow:0 4px 12px rgba(0,0,0,0.06);font-size:13px;}
    .row{display:flex;justify-content:space-between;margin:2px 0;gap:6px;align-items:center;}
    .label{color:#555;}
    .tag{display:inline-block;padding:2px 6px;border-radius:999px;background:#eef2ff;font-size:11px;color:#3949ab;margin-left:4px;}
    .btn-row{margin-top:6px;text-align:right;}
    .btn{display:inline-block;padding:4px 10px;border-radius:999px;border:none;font-size:12px;cursor:pointer;text-decoration:none;margin-left:6px;}
    .btn-edit{background:#3949ab;color:#fff;}
    .btn-delete{background:#d32f2f;color:#fff;}
    form.inline{display:inline;}
    .note{font-size:11px;color:#777;margin-top:6px;line-height:1.4;}
    .filter-card{background:#fff;border-radius:16px;padding:10px 12px;margin-bottom:10px;box-shadow:0 3px 8px rgba(0,0,0,0.04);font-size:12px;}
    .filter-row{display:flex;gap:8px;margin-bottom:6px;align-items:center;}
    .filter-row label{font-size:12px;color:#555;flex:0 0 50px;}
    .filter-row select{flex:1;border-radius:999px;border:1px solid:#ddd;padding:4px 8px;font-size:12px;}
    .filter-actions{text-align:right;margin-top:4px;}
    .btn-filter{padding:4px 10px;border-radius:999px;border:none;background:#3949ab;color:#fff;font-size:12px;cursor:pointer;margin-left:6px;}
    .btn-reset{padding:4px 10px;border-radius:999px;border:none;background:#e0e0e0;color:#333;font-size:12px;cursor:pointer;}
    .pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#f1f3ff;color:#3949ab;font-size:11px;margin-right:4px;}
  </style>
</head>
<body>
<div class="container">
  <h1>交易紀錄管理</h1>
  <div class="subtitle">目前顯示：{{ trades|length }} 筆</div>
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
        <button type="button" class="btn-reset" onclick="window.location='{{ url_for('trades_page') }}'">清除</button>
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
      <form class="inline" method="post"
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

# ======== HTML 模板：交易編輯 =========

TEMPLATE_TRADES_EDIT = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>編輯交易 #{{ trade.id }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f5f7;margin:0;padding:16px;}
    .container{max-width:480px;margin:0 auto 32px;}
    h1{font-size:20px;text-align:center;margin-bottom:8px;}
    .subtitle{text-align:center;font-size:12px;color:#666;margin-bottom:12px;}
    .card{background:#fff;border-radius:16px;padding:16px 18px;margin-bottom:14px;box-shadow:0 4px 12px rgba(0,0,0,0.06);}
    .row{display:flex;justify-content:space-between;margin:6px 0;gap:8px;align-items:center;font-size:14px;}
    .label{color:#555;flex:0 0 90px;}
    input[type="text"],input[type="number"]{flex:1;border-radius:999px;border:1px solid #ddd;padding:6px 10px;font-size:14px;}
    .note{font-size:11px;color:#777;margin-top:6px;line-height:1.4;}
    .btn-row{text-align:right;margin-top:10px;}
    .btn{display:inline-block;padding:6px 14px;border-radius:999px;border:none;font-size:13px;cursor:pointer;text-decoration:none;margin-left:8px;}
    .btn-primary{background:#3949ab;color:#fff;}
    .btn-secondary{background:#e0e0e0;color:#333;}
  </style>
</head>
<body>
<div class="container">
  <h1>編輯交易 #{{ trade.id }}</h1>
  <div class="subtitle">標的：{{ trade.symbol }}</div>

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
        建議日期時間保持格式：YYYY-MM-DD HH:MM:SS<br>
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

# ======== Flask App & Routes =========

app = Flask(__name__)
init_db()


@app.route("/", methods=["GET", "POST"])
def index():
    # 新增交易
    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        symbol = request.form.get("symbol", "").strip()
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
            if date_str:
                ts_value = f"{date_str} 00:00:00"
            else:
                ts_value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            conn = get_db()
            conn.execute(
                "INSERT INTO trades (ts, symbol, shares, amount, reinvest) VALUES (?,?,?,?,?)",
                (ts_value, symbol, shares, amount, reinvest),
            )
            conn.commit()
            conn.close()

    trades_summary, total_amount, total_reinvest, total_new_cash = get_trades_summary()
    data = compute_dashboard()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    today_date = datetime.now().strftime("%Y-%m-%d")

    trade_totals = {
        "total_amount": total_amount,
        "total_reinvest": total_reinvest,
        "total_new_cash": total_new_cash,
    }

    return render_template_string(
        TEMPLATE_INDEX,
        now=now,
        today_date=today_date,
        fmt_money=fmt_money,
        fmt_pct=fmt_pct,
        ANNUAL_RETURN=ANNUAL_RETURN,
        MONTHLY_DCA=MONTHLY_DCA,
        trade_totals=trade_totals,
        **data,   # 這裡包含 etfs / totals / div_compare / dca_compare / house_goal / fill_infos
    )


# ----- 持股管理 -----

@app.route("/holdings", methods=["GET", "POST"])
def holdings_page():
    if request.method == "POST":
        symbol = request.form.get("symbol", "").strip()
        name = request.form.get("name", "").strip()
        shares_raw = request.form.get("shares", "").strip()
        cost_raw = request.form.get("cost", "").strip()

        try:
            shares = int(shares_raw) if shares_raw else 0
        except ValueError:
            shares = 0

        try:
            cost = float(cost_raw) if cost_raw else 0.0
        except ValueError:
            cost = 0.0

        if symbol and name and shares > 0 and cost > 0:
            conn = get_db()
            conn.execute(
                "INSERT INTO holdings (symbol, name, shares, cost) VALUES (?,?,?,?)",
                (symbol, name, shares, cost),
            )
            conn.commit()
            conn.close()

    holdings = get_all_holdings()
    return render_template_string(
        TEMPLATE_HOLDINGS,
        holdings=holdings,
    )


@app.route("/holdings/edit/<int:holding_id>", methods=["GET", "POST"])
def edit_holding(holding_id):
    conn = get_db()
    cur = conn.execute("SELECT * FROM holdings WHERE id = ?", (holding_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return "Holding not found", 404

    if request.method == "POST":
        symbol = request.form.get("symbol", "").strip()
        name = request.form.get("name", "").strip()
        shares_raw = request.form.get("shares", "").strip()
        cost_raw = request.form.get("cost", "").strip()

        try:
            shares = int(shares_raw) if shares_raw else 0
        except ValueError:
            shares = 0

        try:
            cost = float(cost_raw) if cost_raw else 0.0
        except ValueError:
            cost = 0.0

        if symbol and name and shares > 0 and cost > 0:
            conn.execute(
                "UPDATE holdings SET symbol = ?, name = ?, shares = ?, cost = ? WHERE id = ?",
                (symbol, name, shares, cost, holding_id),
            )
            conn.commit()
            conn.close()
            return redirect(url_for("holdings_page"))

    conn.close()
    return render_template_string(
        TEMPLATE_HOLDINGS_EDIT,
        h=row,
    )


@app.route("/holdings/delete/<int:holding_id>", methods=["POST"])
def delete_holding(holding_id):
    conn = get_db()
    conn.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("holdings_page"))


# ----- 配息管理 -----

@app.route("/dividends", methods=["GET", "POST"])
def dividends_page():
    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        symbol = request.form.get("symbol", "").strip()
        cash_raw = request.form.get("cash", "").strip()
        note = request.form.get("note", "").strip()

        try:
            cash = float(cash_raw) if cash_raw else 0.0
        except ValueError:
            cash = 0.0

        if date_str and symbol and cash > 0:
            conn = get_db()
            conn.execute(
                "INSERT INTO dividends (date, symbol, cash, note) VALUES (?,?,?,?)",
                (date_str, symbol, cash, note if note else None),
            )
            conn.commit()
            conn.close()

    dividends = get_all_dividends()
    return render_template_string(
        TEMPLATE_DIVIDENDS,
        dividends=dividends,
        fmt_money=fmt_money,
    )


@app.route("/dividends/edit/<int:div_id>", methods=["GET", "POST"])
def edit_dividend(div_id):
    conn = get_db()
    cur = conn.execute("SELECT * FROM dividends WHERE id = ?", (div_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return "Dividend not found", 404

    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        symbol = request.form.get("symbol", "").strip()
        cash_raw = request.form.get("cash", "").strip()
        note = request.form.get("note", "").strip()

        try:
            cash = float(cash_raw) if cash_raw else 0.0
        except ValueError:
            cash = 0.0

        if date_str and symbol and cash > 0:
            conn.execute(
                "UPDATE dividends SET date = ?, symbol = ?, cash = ?, note = ? WHERE id = ?",
                (date_str, symbol, cash, note if note else None, div_id),
            )
            conn.commit()
            conn.close()
            return redirect(url_for("dividends_page"))

    conn.close()
    return render_template_string(
        TEMPLATE_DIVIDENDS_EDIT,
        d=row,
    )


@app.route("/dividends/delete/<int:div_id>", methods=["POST"])
def delete_dividend(div_id):
    conn = get_db()
    conn.execute("DELETE FROM dividends WHERE id = ?", (div_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("dividends_page"))


# ----- DCA 管理 -----

@app.route("/dca", methods=["GET", "POST"])
def dca_page():
    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        symbol = request.form.get("symbol", "").strip()
        amount_raw = request.form.get("amount", "").strip()

        try:
            amount = float(amount_raw) if amount_raw else 0.0
        except ValueError:
            amount = 0.0

        if date_str and symbol and amount > 0:
            conn = get_db()
            conn.execute(
                "INSERT INTO dca (date, symbol, amount) VALUES (?,?,?)",
                (date_str, symbol, amount),
            )
            conn.commit()
            conn.close()

    records = get_all_dca()
    return render_template_string(
        TEMPLATE_DCA,
        records=records,
        fmt_money=fmt_money,
    )


@app.route("/dca/edit/<int:dca_id>", methods=["GET", "POST"])
def edit_dca(dca_id):
    conn = get_db()
    cur = conn.execute("SELECT * FROM dca WHERE id = ?", (dca_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return "DCA record not found", 404

    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        symbol = request.form.get("symbol", "").strip()
        amount_raw = request.form.get("amount", "").strip()

        try:
            amount = float(amount_raw) if amount_raw else 0.0
        except ValueError:
            amount = 0.0

        if date_str and symbol and amount > 0:
            conn.execute(
                "UPDATE dca SET date = ?, symbol = ?, amount = ? WHERE id = ?",
                (date_str, symbol, amount, dca_id),
            )
            conn.commit()
            conn.close()
            return redirect(url_for("dca_page"))

    conn.close()
    return render_template_string(
        TEMPLATE_DCA_EDIT,
        r=row,
    )


@app.route("/dca/delete/<int:dca_id>", methods=["POST"])
def delete_dca(dca_id):
    conn = get_db()
    conn.execute("DELETE FROM dca WHERE id = ?", (dca_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("dca_page"))


# ----- 交易管理 -----

@app.route("/trades")
def trades_page():
    conn = get_db()

    cur = conn.execute("SELECT DISTINCT symbol FROM trades ORDER BY symbol")
    symbols_rows = cur.fetchall()
    symbols = [r["symbol"] for r in symbols_rows]

    cur = conn.execute("SELECT DISTINCT substr(ts,1,4) AS y FROM trades ORDER BY y DESC")
    years_rows = cur.fetchall()
    years = [r["y"] for r in years_rows if r["y"]]

    selected_symbol = request.args.get("symbol", "").strip()
    selected_year = request.args.get("year", "").strip()

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
    _, total_amount, total_reinvest, total_new_cash = get_trades_summary()
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
        TEMPLATE_TRADES,
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
        TEMPLATE_TRADES_EDIT,
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
