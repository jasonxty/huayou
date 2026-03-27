"""Fetch fundamental data for 603799 from AKShare."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import akshare as ak
import pandas as pd

import config

logger = logging.getLogger(__name__)


@dataclass
class FundamentalSnapshot:
    """All the fundamental data needed for one analysis run."""
    ticker: str
    name: str
    industry: str
    total_shares: float
    market_cap: float  # 亿
    price: float

    # Income (亿)
    revenue_latest: float  # 最近一期营收
    revenue_prev_year: float  # 去年同期营收
    net_profit_latest: float  # 最近一期归母净利
    net_profit_prev_year: float  # 去年同期归母净利
    report_period: str  # 报告期名称

    # Margins
    gross_margin: float  # 毛利率 %
    net_margin: float  # 净利率 %
    roe: float  # ROE %

    # Balance sheet
    debt_ratio: float  # 资产负债率 %
    current_ratio: float  # 流动比率

    # Valuation
    pe_ttm: float
    pb: float

    # Historical annual data for trend analysis
    annual_data: list[dict]

    def revenue_yoy(self) -> float:
        if self.revenue_prev_year > 0:
            return (self.revenue_latest / self.revenue_prev_year - 1) * 100
        return 0.0

    def profit_yoy(self) -> float:
        if self.net_profit_prev_year > 0:
            return (self.net_profit_latest / self.net_profit_prev_year - 1) * 100
        return 0.0


def fetch_fundamentals(ticker: str = config.TICKER,
                       current_price: float | None = None) -> FundamentalSnapshot:
    """Fetch all fundamental data for 603799. Returns a FundamentalSnapshot."""

    logger.info("Fetching fundamentals for %s...", ticker)

    info = ak.stock_individual_info_em(symbol=ticker)
    info_dict = dict(zip(info["item"], info["value"]))
    total_shares = float(info_dict.get("总股本", 0))
    industry = str(info_dict.get("行业", "能源金属"))
    price = current_price or float(info_dict.get("最新", 0))
    market_cap = price * total_shares / 1e8

    logger.info("Fetching financial summary...")
    fin = ak.stock_financial_abstract_ths(symbol=ticker, indicator="按年度")
    fin["报告期"] = fin["报告期"].astype(str)
    recent_annual = fin[fin["报告期"] >= "2019"].sort_values("报告期", ascending=False)

    annual_data = []
    for _, row in recent_annual.iterrows():
        annual_data.append({
            "year": row["报告期"],
            "revenue": row["营业总收入"],
            "net_profit": row["净利润"],
            "gross_margin": row["销售毛利率"],
            "net_margin": row["销售净利率"],
            "roe": str(row.get("净资产收益率", "")),
            "debt_ratio": row["资产负债率"],
        })

    latest_annual = recent_annual.iloc[0] if len(recent_annual) > 0 else {}

    def parse_pct(val) -> float:
        s = str(val).replace("%", "").strip()
        try:
            return float(s)
        except (ValueError, TypeError):
            return 0.0

    def parse_amount_yi(val) -> float:
        """Parse values like '41.55亿' or '6923.75万' to 亿."""
        s = str(val).strip()
        if "亿" in s:
            return float(s.replace("亿", ""))
        elif "万" in s:
            return float(s.replace("万", "")) / 10000
        try:
            return float(s)
        except (ValueError, TypeError):
            return 0.0

    gross_margin = parse_pct(latest_annual.get("销售毛利率", 0))
    net_margin = parse_pct(latest_annual.get("销售净利率", 0))
    roe = parse_pct(latest_annual.get("净资产收益率", 0))
    debt_ratio = parse_pct(latest_annual.get("资产负债率", 0))

    logger.info("Fetching income statements...")
    profit_df = ak.stock_profit_sheet_by_report_em(symbol=f"SH{ticker}")
    profit_df = profit_df.sort_values("REPORT_DATE_NAME", ascending=False)

    latest_report = profit_df.iloc[0] if len(profit_df) > 0 else {}
    report_period = str(latest_report.get("REPORT_DATE_NAME", ""))

    revenue_latest = float(latest_report.get("TOTAL_OPERATE_INCOME", 0)) / 1e8
    net_profit_latest = float(latest_report.get("PARENT_NETPROFIT", 0)) / 1e8

    period_suffix = ""
    if "三季报" in report_period:
        period_suffix = "三季报"
    elif "中报" in report_period:
        period_suffix = "中报"
    elif "一季报" in report_period:
        period_suffix = "一季报"
    elif "年报" in report_period:
        period_suffix = "年报"

    prev_year_match = profit_df[
        profit_df["REPORT_DATE_NAME"].str.contains(period_suffix, na=False)
    ]
    if len(prev_year_match) > 1:
        prev_report = prev_year_match.iloc[1]
        revenue_prev = float(prev_report.get("TOTAL_OPERATE_INCOME", 0)) / 1e8
        profit_prev = float(prev_report.get("PARENT_NETPROFIT", 0)) / 1e8
    else:
        revenue_prev = revenue_latest
        profit_prev = net_profit_latest

    revenue_annual = parse_amount_yi(latest_annual.get("营业总收入", 0))
    profit_annual = parse_amount_yi(latest_annual.get("净利润", 0))
    eps_annual = profit_annual * 1e8 / total_shares if total_shares > 0 else 0
    pe_ttm = price / eps_annual if eps_annual > 0 else 0

    nav_per_share = float(str(latest_annual.get("每股净资产", 0) or 0) or 0)
    pb = price / nav_per_share if nav_per_share > 0 else 0

    current_ratio = float(str(latest_annual.get("流动比率", 0) or 0) or 0)

    return FundamentalSnapshot(
        ticker=ticker,
        name=config.TICKER_NAME,
        industry=industry,
        total_shares=total_shares,
        market_cap=round(market_cap, 0),
        price=price,
        revenue_latest=round(revenue_latest, 1),
        revenue_prev_year=round(revenue_prev, 1),
        net_profit_latest=round(net_profit_latest, 1),
        net_profit_prev_year=round(profit_prev, 1),
        report_period=report_period,
        gross_margin=gross_margin,
        net_margin=net_margin,
        roe=roe,
        debt_ratio=debt_ratio,
        current_ratio=current_ratio,
        pe_ttm=round(pe_ttm, 1),
        pb=round(pb, 1),
        annual_data=annual_data,
    )
