# app.py
# 雲端版：你的 ETF + 買房頭期款手機儀表板（for Render）

import yfinance as yf
from datetime import datetime
from flask import Flask, render_template_string

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

# ======== 計算儀表板數據 =========

def compute_dashboard():
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
        }
    }

# ======== HTML 模板（手機優化） =========

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
      margin-bottom: 16px;
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
  </style>
</head>
<body>
<div class="container">
  <h1>ETF & 買房儀表板</h1>
  <div class="subtitle">最後更新：{{ now }}</div>

  <div class="card">
    <h2>投資組合總覽 <span class="chip">含息</span></h2>
    <div class="row">
      <span class="label">總成本</span>
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
    <h2>定期定額 vs 市值</h2>
    {% if dca_compare.dca_total_all > 0 %}
      <div class="row">
        <span class="label">DCA 總投入</span>
        <span class="value">{{ fmt_money(dca_compare.dca_total_all) }} 元</span>
      </div>
      <div class="row">
        <span class="label">目前市值</span>
        <span class="value">{{ fmt_money(totals.total_mv) }} 元</span>
      </div>
      <div class="big-number {% if dca_compare.profit_vs_dca > 0 %}positive{% elif dca_compare.profit_vs_dca < 0 %}negative{% else %}neutral{% endif %}">
        粗略報酬率：{{ fmt_pct(dca_compare.pl_vs_dca_pct) }}
      </div>
      <div class="note">
        僅以 dca_records 總投入 vs 目前整體市值做大方向參考，未區分早期持股與一次性買入。
      </div>
    {% else %}
      <div>尚無定期定額紀錄。</div>
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

# ======== Flask App =========

app = Flask(__name__)

@app.route("/")
def index():
    data = compute_dashboard()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return render_template_string(
        TEMPLATE,
        now=now,
        fmt_money=fmt_money,
        fmt_pct=fmt_pct,
        ANNUAL_RETURN=ANNUAL_RETURN,
        MONTHLY_DCA=MONTHLY_DCA,
        **data
    )

if __name__ == "__main__":
    # 本機測試用，雲端 Render 不會跑到這段
    app.run(host="0.0.0.0", port=5000, debug=True)
