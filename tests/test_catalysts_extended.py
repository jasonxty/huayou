"""Tests for extended commodity data in catalysts.py (SHFE nickel, lithium carbonate)."""

import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from data.catalysts import _fetch_domestic_futures, fetch_catalysts, CatalystSnapshot


class TestFetchDomesticFutures:
    @patch("data.catalysts.ak")
    def test_normal_data(self, mock_ak):
        mock_ak.futures_main_sina.return_value = pd.DataFrame({
            "日期": ["2026-03-26", "2026-03-27"],
            "开盘价": [135000, 136000],
            "最高价": [138000, 139000],
            "最低价": [134000, 135000],
            "收盘价": [136000.0, 137100.0],
            "成交量": [400000, 350000],
            "持仓量": [180000, 182000],
            "动态结算价": [136500, 136800],
        })
        close, change = _fetch_domestic_futures("ni0", "沪镍")
        assert close == 137100.0
        assert change == 1100.0

    @patch("data.catalysts.ak")
    def test_empty_df(self, mock_ak):
        mock_ak.futures_main_sina.return_value = pd.DataFrame()
        close, change = _fetch_domestic_futures("ni0", "沪镍")
        assert close is None
        assert change is None

    @patch("data.catalysts.ak")
    def test_api_failure(self, mock_ak):
        mock_ak.futures_main_sina.side_effect = Exception("timeout")
        close, change = _fetch_domestic_futures("ni0", "沪镍")
        assert close is None
        assert change is None


class TestFetchCatalystsExtended:
    @patch("data.catalysts._fetch_domestic_futures")
    @patch("data.catalysts._fetch_lme_nickel")
    def test_all_commodities(self, mock_lme, mock_domestic):
        mock_lme.return_value = (17200.0, 118000.0, 0.5, "2026-03-27")
        mock_domestic.side_effect = [
            (137100.0, 1100.0),  # SHFE nickel
            (168440.0, 11240.0),  # lithium carbonate
        ]
        snap = fetch_catalysts()
        assert snap.lme_nickel_usd == 17200.0
        assert snap.shfe_nickel == 137100.0
        assert snap.shfe_nickel_chg == 1100.0
        assert snap.lithium_carbonate == 168440.0
        assert snap.lithium_carbonate_chg == 11240.0
        assert len(snap.fetch_errors) == 0

    @patch("data.catalysts._fetch_domestic_futures")
    @patch("data.catalysts._fetch_lme_nickel")
    def test_partial_failures(self, mock_lme, mock_domestic):
        mock_lme.return_value = (None, None, None, "")
        mock_domestic.side_effect = [
            (137100.0, 1100.0),
            (None, None),
        ]
        snap = fetch_catalysts()
        assert snap.shfe_nickel == 137100.0
        assert snap.lithium_carbonate is None
        assert "LME镍价获取失败" in snap.fetch_errors
        assert "碳酸锂主力获取失败" in snap.fetch_errors
