import os
import time
from datetime import datetime
from dateutil.relativedelta import relativedelta

# 规避系统代理可能引起的外部接口请求超时
for env_var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ.pop(env_var, None)

import matplotlib
matplotlib.use('Agg')  # 强制静默画图，避免多线程环境崩溃
import matplotlib.pyplot as plt
from pylab import mpl

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import akshare as ak
import pandas as pd
import pandas_ta as ta

# 设置中文字体，防止图表渲染乱码
mpl.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial']
mpl.rcParams['axes.unicode_minus'] = False

# ================= FastAPI 实例初始化 =================
app = FastAPI(
    title="AkShare 资产量价因子复合服务", 
    description="为 Dify 智能体提供基础统计与技术因子计算接口。"
)

# 缓存与静态资源目录初始化
CACHE_DIR = "data_cache"
IMAGE_DIR = "static/images"
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(IMAGE_DIR, exist_ok=True)

# 挂载静态目录，提供图表 URL 访问
app.mount("/static", StaticFiles(directory="static"), name="static")

# ================= 请求体数据模型 =================
class AssetRequest(BaseModel):
    symbol: str  
    asset_type: str = "stock"  # 支持: stock, etf, board
    period_months: int = 3     # 默认拉取3个月数据用于指标平滑计算

# ================= 核心业务逻辑 =================
def resolve_stock_code(symbol: str) -> str:
    """解析中文股票名称为对应的六位数字代码"""
    if symbol.isdigit() and len(symbol) == 6:
        return symbol
    try:
        stock_dict = ak.stock_info_a_code_name()
        match = stock_dict[stock_dict['name'] == symbol]
        if not match.empty:
            return match.iloc[0]['code']
    except Exception:
        pass
    return symbol

def get_cached_or_fetch_data(symbol: str, asset_type: str, period_months: int) -> pd.DataFrame:
    """带有防御性缓存的数据获取引擎"""
    stock_code = symbol if asset_type == "board" else resolve_stock_code(symbol)
    cache_file = os.path.join(CACHE_DIR, f"{stock_code}_{asset_type}_raw.csv")

    # 1. 缓存拦截机制 (设定过期时间为 1 小时)
    if os.path.exists(cache_file):
        if time.time() - os.path.getmtime(cache_file) < 3600:
            return pd.read_csv(cache_file, index_col=0, parse_dates=True), stock_code

    # 2. 缓存失效，重新拉取数据
    end_date_obj = datetime.now()
    start_date_obj = end_date_obj - relativedelta(months=period_months)
    start_date = start_date_obj.strftime("%Y%m%d")
    end_date = end_date_obj.strftime("%Y%m%d")

    df = pd.DataFrame()
    if asset_type == "stock":
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
    elif asset_type == "etf":
        df = ak.fund_etf_hist_em(symbol=stock_code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
    elif asset_type == "board":
        try:
            df = ak.stock_board_industry_hist_em(symbol=stock_code, start_date=start_date, end_date=end_date, adjust="qfq")
        except Exception:
            # 容错降级：尝试作为概念板块拉取
            df = ak.stock_board_concept_hist_em(symbol=stock_code, start_date=start_date, end_date=end_date, adjust="qfq")
    else:
        raise ValueError("不支持的资产类型，仅支持 stock / etf / board")

    if df is None or df.empty:
        raise ValueError(f"未获取到 {symbol} 的有效数据，请检查标的名称或网络状态。")

    # 3. 统一字段与时间索引
    rename_map = {'日期': 'Date', '开盘': 'Open', '最高': 'High', '最低': 'Low', '收盘': 'Close', '成交量': 'Volume', '换手率': 'Turnover'}
    df.rename(columns=rename_map, inplace=True)
    df['Date'] = pd.to_datetime(df['Date'])
    df.set_index('Date', inplace=True)

    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # 4. 更新本地缓存
    df.to_csv(cache_file)
    return df, stock_code

def compute_factors_and_plot(df: pd.DataFrame, symbol: str, stock_code: str):
    """计算核心因子，生成可视化图表，并结构化输出分析文本"""
    df = df.copy()

    # Pandas-TA 技术因子计算
    df.ta.macd(append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.mfi(length=14, append=True)
    df.ta.bbands(length=20, append=True)

    # 提取动态生成的列名
    macd_cols = [c for c in df.columns if c.startswith('MACD_')]
    macdh_cols = [c for c in df.columns if c.startswith('MACDh_')]
    rsi_col = next((c for c in df.columns if c.startswith('RSI_')), None)
    mfi_col = next((c for c in df.columns if c.startswith('MFI_')), None)
    bb_lower = next((c for c in df.columns if c.startswith('BBL_')), None)

    latest_data = df.iloc[-1]
    
    current_close = float(latest_data['Close'])
    highest_price = float(df['High'].max())
    lowest_price = float(df['Low'].max())
    period_return = round((current_close - float(df.iloc[0]['Close'])) / float(df.iloc[0]['Close']) * 100, 2)

    trend_desc = "偏强" if current_close > df['Close'].mean() else "偏弱" if current_close < df['Close'].mean() else "震荡"

    macd_val = float(latest_data[macd_cols[0]]) if macd_cols else 0.0
    macdh_val = float(latest_data[macdh_cols[0]]) if macdh_cols else 0.0
    rsi_val = float(latest_data[rsi_col]) if rsi_col else 50.0
    mfi_val = float(latest_data[mfi_col]) if mfi_col else 50.0

    analysis_text = (
        f"【标的诊断】{symbol} ({stock_code})\n"
        f"1. 基础数据：最新收盘价 {current_close:.2f}，区间最高 {highest_price:.2f}，区间最低 {lowest_price:.2f}，区间涨跌幅 {period_return}%。\n"
        f"2. 趋势判定：当前处于【{trend_desc}】状态。\n"
        f"3. 动能分析：MACD 值为 {macd_val:.3f}，柱状图(能量) {macdh_val:.3f}。\n"
        f"4. 情绪超买超卖：RSI(14) 为 {rsi_val:.2f}，MFI(资金流量) 为 {mfi_val:.2f}。"
    )

    if rsi_val > 70 or mfi_val > 80:
        analysis_text += "\n⚠️ 警示：技术指标处于超买区间，警惕回撤风险。"
    elif rsi_val < 30 or mfi_val < 20:
        analysis_text += "\n💡 提示：技术指标处于超卖区间，可能存在反弹修复预期。"

    # 生成走势可视化图表
    img_filename = f"{stock_code}_{datetime.now().strftime('%Y%m%d%H%M')}.png"
    img_path = os.path.join(IMAGE_DIR, img_filename)

    plt.figure(figsize=(10, 5))
    plt.plot(df.index, df['Close'], label='Close Price', color='black', linewidth=1.5)
    if bb_lower:
        bbu_col = next(c for c in df.columns if c.startswith('BBU_'))
        plt.plot(df.index, df[bbu_col], label='Bollinger Upper', linestyle='--', color='red', alpha=0.5)
        plt.plot(df.index, df[bb_lower], label='Bollinger Lower', linestyle='--', color='green', alpha=0.5)
    
    plt.title(f"{symbol} ({stock_code}) - Price & Bollinger Bands")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.savefig(img_path, bbox_inches='tight')
    plt.close()

    return analysis_text, f"/static/images/{img_filename}", {
        "current_price": current_close,
        "period_return_pct": period_return,
        "macd": round(macd_val, 4),
        "rsi_14": round(rsi_val, 2),
        "mfi_14": round(mfi_val, 2)
    }

# ================= 核心接口端点 =================
@app.post("/api/finance/comprehensive_factors")
async def get_comprehensive_factors(req: AssetRequest):
    """获取标的综合技术因子与可视化图表"""
    try:
        df, stock_code = get_cached_or_fetch_data(req.symbol, req.asset_type, req.period_months)

        if len(df) < 20:
            return {"status": "error", "message": "获取的数据点过少，无法计算有效技术因子。"}

        analysis_text, img_url, raw_factors = compute_factors_and_plot(df, req.symbol, stock_code)

        # 降采样收盘价轨迹，减少传输体积
        sampled_df = df.iloc[::3].copy()
        trajectory = [{"date": date.strftime("%m-%d"), "price": float(row['Close'])} for date, row in sampled_df.iterrows()]

        return {
            "status": "success",
            "data": {
                "symbol": req.symbol,
                "asset_type": req.asset_type,
                "stock_code": stock_code,
                "analysis_summary": analysis_text,
                "raw_factors": raw_factors,
                "trajectory": trajectory,
                "chart_url": img_url
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # 默认绑定到 8000 端口，符合通用部署规范
    uvicorn.run(app, host="0.0.0.0", port=8000)
