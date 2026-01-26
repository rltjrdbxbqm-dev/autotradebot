"""
================================================================================
바이낸스 자동매매 봇 v2.0.0 (MA + 스토캐스틱 전략)
================================================================================
- MA + 스토캐스틱 전략 (역방향 전략 제거)
- 파인튜닝된 135개 투자 적합 코인 대상
- 부적합 코인 자동 매도 기능 (BNB 제외)
- BNB 자동 충전 기능 (수수료 할인용)
- 수수료: 0.075% (BNB 할인 적용)
- 4시간봉 기준 매매 (UTC 00:00, 04:00, 08:00, 12:00, 16:00, 20:00)
- 서버 점검 시 자동 복구: API 실패 시 다음 스케줄에 자동 재시도
================================================================================
"""

import os
import sys
import time
import signal
import atexit
import schedule
import numpy as np
import pandas as pd
import ccxt
import requests
import json
from datetime import datetime, timedelta, timezone
import logging
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 로그 파일 경로 설정
log_file_path = os.path.join(os.path.expanduser('~'), 'binance_trading_log.txt')

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(log_file_path),
        logging.StreamHandler()
    ]
)

# ============================================================
# API 설정 (환경변수에서 로드)
# ============================================================

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not all([BINANCE_API_KEY, BINANCE_SECRET_KEY]):
    logging.error("❌ .env 파일에서 바이낸스 API 키를 찾을 수 없습니다.")

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    logging.warning("⚠️ .env 파일에서 텔레그램 설정을 찾을 수 없습니다.")

# ============================================================
# 상태 저장 파일 경로
# ============================================================

STOCH_CACHE_FILE = os.path.join(os.path.expanduser('~'), 'binance_stoch_cache.json')

# ============================================================
# 거래 설정
# ============================================================

FEE_RATE = 0.00075  # 0.075% (BNB 할인)
MIN_ORDER_USDT = 11  # 최소 주문 금액 (바이낸스 최소 $10 + 여유)

# BNB 자동 충전 설정
BNB_MIN_BALANCE = 15  # BNB 최소 보유량 (USDT 기준) - 이 이하면 충전
BNB_RECHARGE_AMOUNT = 30  # 충전 시 매수할 금액 (USDT)

# ============================================================
# 종료 알림 관련 전역 변수
# ============================================================

BOT_START_TIME = None
SHUTDOWN_SENT = False

# ============================================================
# 투자 적합 코인 135개 (파인튜닝 결과, BTC CAGR 이상)
# 제외: 데이터 부족, 프로젝트 문제, 팬토큰, 래핑토큰, 거래횟수 부족
# ============================================================

COINS = [
    'FLOKI/USDT', 'PEPE/USDT', 'SOL/USDT', 'FET/USDT', 'SUI/USDT',
    'SAND/USDT', 'VANRY/USDT', 'BONK/USDT', 'AXS/USDT', 'RAY/USDT',
    'ONE/USDT', 'OM/USDT', 'WLD/USDT', 'AVAX/USDT', 'JASMY/USDT',
    'DENT/USDT', 'SUPER/USDT', 'RENDER/USDT', 'ETHFI/USDT', 'CHR/USDT',
    'ARKM/USDT', 'MANA/USDT', 'ANKR/USDT', 'CTSI/USDT', 'TFUEL/USDT',
    'DOGE/USDT', 'VTHO/USDT', 'BOME/USDT', 'ZK/USDT', 'GAS/USDT',
    'BNT/USDT', 'TAO/USDT', 'CELR/USDT', 'CHZ/USDT', 'EGLD/USDT',
    'WIF/USDT', 'PENDLE/USDT', 'ROSE/USDT', 'HBAR/USDT', 'FIL/USDT',
    'SUSHI/USDT', 'CRV/USDT', 'LINK/USDT', 'ARB/USDT', 'WIN/USDT',
    'OSMO/USDT', 'VET/USDT', 'AMP/USDT', 'CFX/USDT', 'RUNE/USDT',
    'STX/USDT', 'POLYX/USDT', 'ZRO/USDT', 'ZEC/USDT', 'SHIB/USDT',
    'DUSK/USDT', 'ZIL/USDT', 'GALA/USDT', 'ADA/USDT', 'HIGH/USDT',
    'ZEN/USDT', 'POL/USDT', 'BNB/USDT', 'HOT/USDT', 'GLM/USDT',
    'ENJ/USDT', 'COTI/USDT', 'SUN/USDT', 'CETUS/USDT', 'BEAMX/USDT',
    'PIXEL/USDT', 'THETA/USDT', 'SYN/USDT', 'KAIA/USDT', 'UNI/USDT',
    'ALGO/USDT', 'ACH/USDT', 'IOTX/USDT', 'RVN/USDT', 'CVC/USDT',
    'TWT/USDT', 'LRC/USDT', 'XRP/USDT', 'IQ/USDT', 'JOE/USDT',
    'IOST/USDT', 'DOT/USDT', 'TNSR/USDT', 'NEAR/USDT', 'CVX/USDT',
    'IMX/USDT', 'ETC/USDT', 'AI/USDT', 'XVG/USDT', 'PORTAL/USDT',
    'ETH/USDT', 'AUCTION/USDT', 'KAVA/USDT', 'JST/USDT', 'NFP/USDT',
    'ZRX/USDT', 'TRB/USDT', 'MDT/USDT', 'GNO/USDT', 'FLUX/USDT',
    'XLM/USDT', 'GRT/USDT', 'ICX/USDT', 'MBL/USDT', 'BLUR/USDT',
    'ENS/USDT', 'KSM/USDT', 'SC/USDT', 'SNX/USDT', 'ARDR/USDT',
    'ICP/USDT', 'XVS/USDT', 'HIVE/USDT', 'INJ/USDT', 'LQTY/USDT',
    'MOVR/USDT', 'DYDX/USDT', 'CAKE/USDT', 'QNT/USDT', 'XTZ/USDT',
    'OGN/USDT', 'SEI/USDT', 'STG/USDT', 'SLP/USDT', 'CKB/USDT',
    'OP/USDT', 'PHB/USDT', 'W/USDT', 'DGB/USDT', 'BTC/USDT',
]

# ============================================================
# MA 기간 (4시간봉 기준) - 파인튜닝 결과
# ============================================================

MA_PERIODS = {
    'FLOKI/USDT': 255, 'PEPE/USDT': 320, 'SOL/USDT': 245, 'FET/USDT': 230,
    'SUI/USDT': 246, 'SAND/USDT': 226, 'VANRY/USDT': 136, 'BONK/USDT': 124,
    'AXS/USDT': 249, 'RAY/USDT': 219, 'ONE/USDT': 224, 'OM/USDT': 270,
    'WLD/USDT': 280, 'AVAX/USDT': 214, 'JASMY/USDT': 97, 'DENT/USDT': 71,
    'SUPER/USDT': 191, 'RENDER/USDT': 112, 'ETHFI/USDT': 219, 'CHR/USDT': 262,
    'ARKM/USDT': 185, 'MANA/USDT': 69, 'ANKR/USDT': 227, 'CTSI/USDT': 223,
    'TFUEL/USDT': 106, 'DOGE/USDT': 241, 'VTHO/USDT': 222, 'BOME/USDT': 46,
    'ZK/USDT': 58, 'GAS/USDT': 44, 'BNT/USDT': 102, 'TAO/USDT': 119,
    'CELR/USDT': 274, 'CHZ/USDT': 119, 'EGLD/USDT': 276, 'WIF/USDT': 153,
    'PENDLE/USDT': 284, 'ROSE/USDT': 61, 'HBAR/USDT': 224, 'FIL/USDT': 52,
    'SUSHI/USDT': 235, 'CRV/USDT': 88, 'LINK/USDT': 52, 'ARB/USDT': 57,
    'WIN/USDT': 161, 'OSMO/USDT': 127, 'VET/USDT': 310, 'AMP/USDT': 258,
    'CFX/USDT': 207, 'RUNE/USDT': 147, 'STX/USDT': 306, 'POLYX/USDT': 81,
    'ZRO/USDT': 112, 'ZEC/USDT': 262, 'SHIB/USDT': 315, 'DUSK/USDT': 237,
    'ZIL/USDT': 243, 'GALA/USDT': 81, 'ADA/USDT': 293, 'HIGH/USDT': 74,
    'ZEN/USDT': 238, 'POL/USDT': 46, 'BNB/USDT': 203, 'HOT/USDT': 313,
    'GLM/USDT': 100, 'ENJ/USDT': 183, 'COTI/USDT': 97, 'SUN/USDT': 260,
    'CETUS/USDT': 217, 'BEAMX/USDT': 80, 'PIXEL/USDT': 58, 'THETA/USDT': 167,
    'SYN/USDT': 44, 'KAIA/USDT': 21, 'UNI/USDT': 252, 'ALGO/USDT': 236,
    'ACH/USDT': 213, 'IOTX/USDT': 207, 'RVN/USDT': 57, 'CVC/USDT': 91,
    'TWT/USDT': 284, 'LRC/USDT': 105, 'XRP/USDT': 179, 'IQ/USDT': 99,
    'JOE/USDT': 103, 'IOST/USDT': 58, 'DOT/USDT': 288, 'TNSR/USDT': 65,
    'NEAR/USDT': 226, 'CVX/USDT': 128, 'IMX/USDT': 276, 'ETC/USDT': 263,
    'AI/USDT': 39, 'XVG/USDT': 122, 'PORTAL/USDT': 70, 'ETH/USDT': 114,
    'AUCTION/USDT': 84, 'KAVA/USDT': 82, 'JST/USDT': 238, 'NFP/USDT': 52,
    'ZRX/USDT': 253, 'TRB/USDT': 20, 'MDT/USDT': 297, 'GNO/USDT': 134,
    'FLUX/USDT': 221, 'XLM/USDT': 91, 'GRT/USDT': 154, 'ICX/USDT': 276,
    'MBL/USDT': 79, 'BLUR/USDT': 72, 'ENS/USDT': 65, 'KSM/USDT': 160,
    'SC/USDT': 300, 'SNX/USDT': 280, 'ARDR/USDT': 306, 'ICP/USDT': 96,
    'XVS/USDT': 330, 'HIVE/USDT': 98, 'INJ/USDT': 232, 'LQTY/USDT': 202,
    'MOVR/USDT': 54, 'DYDX/USDT': 50, 'CAKE/USDT': 30, 'QNT/USDT': 168,
    'XTZ/USDT': 244, 'OGN/USDT': 307, 'SEI/USDT': 148, 'STG/USDT': 47,
    'SLP/USDT': 55, 'CKB/USDT': 110, 'OP/USDT': 280, 'PHB/USDT': 232,
    'W/USDT': 157, 'DGB/USDT': 98, 'BTC/USDT': 292,
}

# ============================================================
# 스토캐스틱 파라미터 (1일봉 기준) - 파인튜닝 결과
# ============================================================

STOCH_PARAMS = {
    'FLOKI/USDT': {'k_period': 136, 'k_smooth': 20, 'd_period': 12},
    'PEPE/USDT': {'k_period': 119, 'k_smooth': 33, 'd_period': 3},
    'SOL/USDT': {'k_period': 97, 'k_smooth': 19, 'd_period': 19},
    'FET/USDT': {'k_period': 202, 'k_smooth': 27, 'd_period': 27},
    'SUI/USDT': {'k_period': 139, 'k_smooth': 38, 'd_period': 3},
    'SAND/USDT': {'k_period': 122, 'k_smooth': 28, 'd_period': 7},
    'VANRY/USDT': {'k_period': 20, 'k_smooth': 35, 'd_period': 4},
    'BONK/USDT': {'k_period': 201, 'k_smooth': 58, 'd_period': 29},
    'AXS/USDT': {'k_period': 39, 'k_smooth': 32, 'd_period': 3},
    'RAY/USDT': {'k_period': 154, 'k_smooth': 25, 'd_period': 8},
    'ONE/USDT': {'k_period': 179, 'k_smooth': 28, 'd_period': 28},
    'OM/USDT': {'k_period': 83, 'k_smooth': 45, 'd_period': 5},
    'WLD/USDT': {'k_period': 95, 'k_smooth': 13, 'd_period': 4},
    'AVAX/USDT': {'k_period': 39, 'k_smooth': 34, 'd_period': 7},
    'JASMY/USDT': {'k_period': 51, 'k_smooth': 17, 'd_period': 9},
    'DENT/USDT': {'k_period': 190, 'k_smooth': 46, 'd_period': 11},
    'SUPER/USDT': {'k_period': 133, 'k_smooth': 34, 'd_period': 5},
    'RENDER/USDT': {'k_period': 58, 'k_smooth': 19, 'd_period': 7},
    'ETHFI/USDT': {'k_period': 171, 'k_smooth': 57, 'd_period': 23},
    'CHR/USDT': {'k_period': 197, 'k_smooth': 56, 'd_period': 14},
    'ARKM/USDT': {'k_period': 97, 'k_smooth': 12, 'd_period': 20},
    'MANA/USDT': {'k_period': 129, 'k_smooth': 42, 'd_period': 8},
    'ANKR/USDT': {'k_period': 173, 'k_smooth': 39, 'd_period': 28},
    'CTSI/USDT': {'k_period': 62, 'k_smooth': 11, 'd_period': 17},
    'TFUEL/USDT': {'k_period': 160, 'k_smooth': 30, 'd_period': 17},
    'DOGE/USDT': {'k_period': 139, 'k_smooth': 35, 'd_period': 16},
    'VTHO/USDT': {'k_period': 117, 'k_smooth': 33, 'd_period': 18},
    'BOME/USDT': {'k_period': 160, 'k_smooth': 36, 'd_period': 15},
    'ZK/USDT': {'k_period': 79, 'k_smooth': 23, 'd_period': 23},
    'GAS/USDT': {'k_period': 125, 'k_smooth': 32, 'd_period': 33},
    'BNT/USDT': {'k_period': 51, 'k_smooth': 46, 'd_period': 6},
    'TAO/USDT': {'k_period': 129, 'k_smooth': 25, 'd_period': 7},
    'CELR/USDT': {'k_period': 120, 'k_smooth': 31, 'd_period': 11},
    'CHZ/USDT': {'k_period': 127, 'k_smooth': 37, 'd_period': 31},
    'EGLD/USDT': {'k_period': 37, 'k_smooth': 29, 'd_period': 7},
    'WIF/USDT': {'k_period': 136, 'k_smooth': 29, 'd_period': 26},
    'PENDLE/USDT': {'k_period': 83, 'k_smooth': 17, 'd_period': 6},
    'ROSE/USDT': {'k_period': 20, 'k_smooth': 59, 'd_period': 21},
    'HBAR/USDT': {'k_period': 199, 'k_smooth': 36, 'd_period': 5},
    'FIL/USDT': {'k_period': 70, 'k_smooth': 26, 'd_period': 14},
    'SUSHI/USDT': {'k_period': 21, 'k_smooth': 31, 'd_period': 23},
    'CRV/USDT': {'k_period': 36, 'k_smooth': 25, 'd_period': 17},
    'LINK/USDT': {'k_period': 54, 'k_smooth': 30, 'd_period': 6},
    'ARB/USDT': {'k_period': 138, 'k_smooth': 49, 'd_period': 24},
    'WIN/USDT': {'k_period': 126, 'k_smooth': 15, 'd_period': 10},
    'OSMO/USDT': {'k_period': 195, 'k_smooth': 50, 'd_period': 28},
    'VET/USDT': {'k_period': 98, 'k_smooth': 11, 'd_period': 5},
    'AMP/USDT': {'k_period': 133, 'k_smooth': 34, 'd_period': 3},
    'CFX/USDT': {'k_period': 183, 'k_smooth': 73, 'd_period': 20},
    'RUNE/USDT': {'k_period': 94, 'k_smooth': 41, 'd_period': 5},
    'STX/USDT': {'k_period': 136, 'k_smooth': 53, 'd_period': 12},
    'POLYX/USDT': {'k_period': 146, 'k_smooth': 26, 'd_period': 15},
    'ZRO/USDT': {'k_period': 26, 'k_smooth': 34, 'd_period': 30},
    'ZEC/USDT': {'k_period': 86, 'k_smooth': 38, 'd_period': 6},
    'SHIB/USDT': {'k_period': 41, 'k_smooth': 26, 'd_period': 5},
    'DUSK/USDT': {'k_period': 150, 'k_smooth': 71, 'd_period': 24},
    'ZIL/USDT': {'k_period': 153, 'k_smooth': 35, 'd_period': 3},
    'GALA/USDT': {'k_period': 181, 'k_smooth': 38, 'd_period': 14},
    'ADA/USDT': {'k_period': 110, 'k_smooth': 34, 'd_period': 13},
    'HIGH/USDT': {'k_period': 140, 'k_smooth': 35, 'd_period': 3},
    'ZEN/USDT': {'k_period': 126, 'k_smooth': 23, 'd_period': 12},
    'POL/USDT': {'k_period': 151, 'k_smooth': 19, 'd_period': 14},
    'BNB/USDT': {'k_period': 208, 'k_smooth': 34, 'd_period': 3},
    'HOT/USDT': {'k_period': 120, 'k_smooth': 56, 'd_period': 8},
    'GLM/USDT': {'k_period': 123, 'k_smooth': 14, 'd_period': 19},
    'ENJ/USDT': {'k_period': 123, 'k_smooth': 30, 'd_period': 36},
    'COTI/USDT': {'k_period': 98, 'k_smooth': 31, 'd_period': 16},
    'SUN/USDT': {'k_period': 63, 'k_smooth': 34, 'd_period': 17},
    'CETUS/USDT': {'k_period': 20, 'k_smooth': 12, 'd_period': 15},
    'BEAMX/USDT': {'k_period': 141, 'k_smooth': 39, 'd_period': 4},
    'PIXEL/USDT': {'k_period': 132, 'k_smooth': 24, 'd_period': 16},
    'THETA/USDT': {'k_period': 147, 'k_smooth': 58, 'd_period': 8},
    'SYN/USDT': {'k_period': 200, 'k_smooth': 37, 'd_period': 8},
    'KAIA/USDT': {'k_period': 96, 'k_smooth': 41, 'd_period': 15},
    'UNI/USDT': {'k_period': 61, 'k_smooth': 36, 'd_period': 8},
    'ALGO/USDT': {'k_period': 148, 'k_smooth': 26, 'd_period': 5},
    'ACH/USDT': {'k_period': 210, 'k_smooth': 65, 'd_period': 31},
    'IOTX/USDT': {'k_period': 199, 'k_smooth': 38, 'd_period': 29},
    'RVN/USDT': {'k_period': 92, 'k_smooth': 37, 'd_period': 18},
    'CVC/USDT': {'k_period': 72, 'k_smooth': 36, 'd_period': 11},
    'TWT/USDT': {'k_period': 80, 'k_smooth': 21, 'd_period': 11},
    'LRC/USDT': {'k_period': 93, 'k_smooth': 29, 'd_period': 13},
    'XRP/USDT': {'k_period': 183, 'k_smooth': 10, 'd_period': 5},
    'IQ/USDT': {'k_period': 78, 'k_smooth': 35, 'd_period': 10},
    'JOE/USDT': {'k_period': 149, 'k_smooth': 36, 'd_period': 13},
    'IOST/USDT': {'k_period': 115, 'k_smooth': 9, 'd_period': 33},
    'DOT/USDT': {'k_period': 94, 'k_smooth': 31, 'd_period': 8},
    'TNSR/USDT': {'k_period': 103, 'k_smooth': 43, 'd_period': 35},
    'NEAR/USDT': {'k_period': 153, 'k_smooth': 16, 'd_period': 25},
    'CVX/USDT': {'k_period': 165, 'k_smooth': 25, 'd_period': 13},
    'IMX/USDT': {'k_period': 145, 'k_smooth': 46, 'd_period': 9},
    'ETC/USDT': {'k_period': 72, 'k_smooth': 33, 'd_period': 8},
    'AI/USDT': {'k_period': 84, 'k_smooth': 49, 'd_period': 30},
    'XVG/USDT': {'k_period': 128, 'k_smooth': 45, 'd_period': 19},
    'PORTAL/USDT': {'k_period': 133, 'k_smooth': 39, 'd_period': 6},
    'ETH/USDT': {'k_period': 110, 'k_smooth': 40, 'd_period': 15},
    'AUCTION/USDT': {'k_period': 61, 'k_smooth': 37, 'd_period': 39},
    'KAVA/USDT': {'k_period': 142, 'k_smooth': 46, 'd_period': 10},
    'JST/USDT': {'k_period': 75, 'k_smooth': 11, 'd_period': 27},
    'NFP/USDT': {'k_period': 146, 'k_smooth': 63, 'd_period': 6},
    'ZRX/USDT': {'k_period': 82, 'k_smooth': 25, 'd_period': 5},
    'TRB/USDT': {'k_period': 102, 'k_smooth': 52, 'd_period': 12},
    'MDT/USDT': {'k_period': 131, 'k_smooth': 21, 'd_period': 8},
    'GNO/USDT': {'k_period': 97, 'k_smooth': 45, 'd_period': 20},
    'FLUX/USDT': {'k_period': 170, 'k_smooth': 29, 'd_period': 26},
    'XLM/USDT': {'k_period': 137, 'k_smooth': 7, 'd_period': 5},
    'GRT/USDT': {'k_period': 143, 'k_smooth': 38, 'd_period': 7},
    'ICX/USDT': {'k_period': 80, 'k_smooth': 45, 'd_period': 9},
    'MBL/USDT': {'k_period': 85, 'k_smooth': 13, 'd_period': 31},
    'BLUR/USDT': {'k_period': 153, 'k_smooth': 20, 'd_period': 5},
    'ENS/USDT': {'k_period': 185, 'k_smooth': 30, 'd_period': 14},
    'KSM/USDT': {'k_period': 60, 'k_smooth': 48, 'd_period': 21},
    'SC/USDT': {'k_period': 74, 'k_smooth': 35, 'd_period': 15},
    'SNX/USDT': {'k_period': 87, 'k_smooth': 56, 'd_period': 16},
    'ARDR/USDT': {'k_period': 101, 'k_smooth': 27, 'd_period': 16},
    'ICP/USDT': {'k_period': 166, 'k_smooth': 37, 'd_period': 30},
    'XVS/USDT': {'k_period': 48, 'k_smooth': 22, 'd_period': 15},
    'HIVE/USDT': {'k_period': 162, 'k_smooth': 40, 'd_period': 14},
    'INJ/USDT': {'k_period': 220, 'k_smooth': 45, 'd_period': 16},
    'LQTY/USDT': {'k_period': 83, 'k_smooth': 14, 'd_period': 17},
    'MOVR/USDT': {'k_period': 119, 'k_smooth': 40, 'd_period': 16},
    'DYDX/USDT': {'k_period': 139, 'k_smooth': 25, 'd_period': 17},
    'CAKE/USDT': {'k_period': 173, 'k_smooth': 37, 'd_period': 28},
    'QNT/USDT': {'k_period': 215, 'k_smooth': 23, 'd_period': 22},
    'XTZ/USDT': {'k_period': 119, 'k_smooth': 29, 'd_period': 11},
    'OGN/USDT': {'k_period': 166, 'k_smooth': 28, 'd_period': 17},
    'SEI/USDT': {'k_period': 179, 'k_smooth': 42, 'd_period': 20},
    'STG/USDT': {'k_period': 68, 'k_smooth': 51, 'd_period': 29},
    'SLP/USDT': {'k_period': 150, 'k_smooth': 20, 'd_period': 17},
    'CKB/USDT': {'k_period': 140, 'k_smooth': 30, 'd_period': 15},
    'OP/USDT': {'k_period': 138, 'k_smooth': 37, 'd_period': 24},
    'PHB/USDT': {'k_period': 86, 'k_smooth': 30, 'd_period': 10},
    'W/USDT': {'k_period': 92, 'k_smooth': 32, 'd_period': 14},
    'DGB/USDT': {'k_period': 125, 'k_smooth': 28, 'd_period': 24},
    'BTC/USDT': {'k_period': 172, 'k_smooth': 31, 'd_period': 3},
}

# ============================================================
# 전역 변수
# ============================================================

stoch_cache = {}
stoch_cache_date = None
exchange = None

# ============================================================
# API 호출 Timeout Wrapper
# ============================================================

class APITimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise APITimeoutError("API 호출 타임아웃")


def call_with_timeout(func, timeout=30):
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    
    try:
        result = func()
        signal.alarm(0)
        return result
    except APITimeoutError:
        logging.warning(f"API 호출 타임아웃 ({timeout}초)")
        return None
    except Exception as e:
        signal.alarm(0)
        logging.warning(f"API 호출 중 오류: {e}")
        return None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def retry_api_call(func, max_retries=3, delay=2.0, default=None, timeout=30):
    for attempt in range(max_retries):
        result = call_with_timeout(func, timeout=timeout)
        if result is not None:
            return result
        logging.warning(f"API 호출 결과가 None입니다. 재시도 {attempt + 1}/{max_retries}")
        if attempt < max_retries - 1:
            time.sleep(delay)
    return default


# ============================================================
# 거래소 초기화
# ============================================================

def init_exchange():
    global exchange
    try:
        exchange = ccxt.binance({
            'apiKey': BINANCE_API_KEY,
            'secret': BINANCE_SECRET_KEY,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        exchange.load_markets()
        logging.info("✅ 바이낸스 거래소 연결 성공")
        return True
    except Exception as e:
        logging.error(f"❌ 바이낸스 거래소 연결 실패: {e}")
        return False


# ============================================================
# 텔레그램 알림 함수
# ============================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'HTML'}
        response = requests.post(url, data=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logging.error(f"텔레그램 전송 중 오류: {e}")
        return False


def send_trade_summary(buy_list, sell_list, excluded_sell_list, total_asset, usdt_balance, bnb_info, errors):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    msg = f"📊 <b>바이낸스 거래 종합 리포트</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"💰 총 자산: <b>${total_asset:,.2f}</b>\n"
    msg += f"💵 USDT 잔고: ${usdt_balance:,.2f}\n"
    
    if bnb_info:
        msg += f"🔶 BNB 잔고: {bnb_info['balance']:.4f} (${bnb_info['value']:.2f})\n"
    
    msg += f"━━━━━━━━━━━━━━━\n"
    
    if buy_list:
        msg += f"🟢 <b>매수 {len(buy_list)}건</b>\n"
        for item in buy_list[:10]:
            msg += f"  • {item['symbol']}: ${item['amount']:.2f}\n"
        if len(buy_list) > 10:
            msg += f"  ... 외 {len(buy_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    if sell_list:
        msg += f"🔴 <b>전략 미충족 매도 {len(sell_list)}건</b>\n"
        for item in sell_list[:10]:
            msg += f"  • {item['symbol']}: {item['reason']}\n"
        if len(sell_list) > 10:
            msg += f"  ... 외 {len(sell_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    if excluded_sell_list:
        msg += f"🚫 <b>부적합 코인 매도 {len(excluded_sell_list)}건</b>\n"
        for item in excluded_sell_list[:10]:
            msg += f"  • {item['symbol']}: {item['reason']}\n"
        if len(excluded_sell_list) > 10:
            msg += f"  ... 외 {len(excluded_sell_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    if not buy_list and not sell_list and not excluded_sell_list:
        msg += f"ℹ️ 거래 없음\n━━━━━━━━━━━━━━━\n"
    
    if errors:
        msg += f"⚠️ <b>오류 {len(errors)}건</b>\n"
        for err in errors[:5]:
            msg += f"  • {err}\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    msg += f"🕐 {now}"
    send_telegram(msg)


def send_start_alert():
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg = f"🚀 <b>바이낸스 자동매매 봇 시작 (v2.0.0)</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"📈 전략: MA + 스토캐스틱 (역방향 제거)\n"
    msg += f"💰 수수료: 0.075% (BNB 할인)\n"
    msg += f"🔶 BNB 자동충전: ${BNB_MIN_BALANCE} 이하시 ${BNB_RECHARGE_AMOUNT} 충전\n"
    msg += f"🪙 적합 코인: {len(COINS)}개 (파인튜닝)\n"
    msg += f"🚫 부적합 코인 자동 매도 활성화 (BNB 제외)\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"🕐 {now}"
    send_telegram(msg)


def send_shutdown_alert(reason="수동 종료"):
    global SHUTDOWN_SENT
    if SHUTDOWN_SENT:
        return
    SHUTDOWN_SENT = True
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    uptime_str = "알 수 없음"
    if BOT_START_TIME:
        uptime = datetime.now() - BOT_START_TIME
        days, hours = uptime.days, uptime.seconds // 3600
        minutes = (uptime.seconds % 3600) // 60
        if days > 0:
            uptime_str = f"{days}일 {hours}시간 {minutes}분"
        elif hours > 0:
            uptime_str = f"{hours}시간 {minutes}분"
        else:
            uptime_str = f"{minutes}분"
    
    msg = f"🛑 <b>바이낸스 봇 종료</b>\n━━━━━━━━━━━━━━━\n"
    msg += f"📋 종료 사유: {reason}\n⏱️ 실행 시간: {uptime_str}\n━━━━━━━━━━━━━━━\n🕐 {now}"
    send_telegram(msg)


# ============================================================
# 종료 핸들러 설정
# ============================================================

def signal_handler(signum, frame):
    signal_names = {signal.SIGINT: "SIGINT (Ctrl+C)", signal.SIGTERM: "SIGTERM (kill)"}
    signal_name = signal_names.get(signum, f"Signal {signum}")
    logging.info(f"종료 시그널 수신: {signal_name}")
    send_shutdown_alert(reason=signal_name)
    sys.exit(0)


def setup_shutdown_handlers():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, signal_handler)
    atexit.register(lambda: send_shutdown_alert(reason="프로그램 종료"))
    logging.info("종료 핸들러 설정 완료")


# ============================================================
# 스토캐스틱 캐시
# ============================================================

def save_stoch_cache():
    global stoch_cache, stoch_cache_date
    try:
        save_data = {'cache_date': stoch_cache_date.isoformat() if stoch_cache_date else None, 'data': stoch_cache}
        with open(STOCH_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"스토캐스틱 캐시 저장 중 오류: {e}")
        return False


def load_stoch_cache():
    global stoch_cache, stoch_cache_date
    try:
        if not os.path.exists(STOCH_CACHE_FILE):
            return False
        with open(STOCH_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cache_date_str = data.get('cache_date')
        if cache_date_str:
            stoch_cache_date = datetime.fromisoformat(cache_date_str).date()
        stoch_cache = data.get('data', {})
        logging.info(f"스토캐스틱 캐시 로드 완료: 날짜={stoch_cache_date}, {len(stoch_cache)}개 코인")
        return True
    except Exception as e:
        logging.error(f"스토캐스틱 캐시 로드 중 오류: {e}")
        return False


# ============================================================
# BNB 자동 충전
# ============================================================

def get_bnb_balance():
    try:
        balance = exchange.fetch_balance()
        bnb_amount = float(balance.get('BNB', {}).get('free', 0))
        if bnb_amount > 0:
            ticker = exchange.fetch_ticker('BNB/USDT')
            bnb_price, bnb_value = ticker['last'], bnb_amount * ticker['last']
        else:
            bnb_value, bnb_price = 0, 0
        return {'balance': bnb_amount, 'price': bnb_price, 'value': bnb_value}
    except Exception as e:
        logging.error(f"BNB 잔고 조회 중 오류: {e}")
        return {'balance': 0, 'price': 0, 'value': 0}


def check_and_recharge_bnb():
    try:
        bnb_info = get_bnb_balance()
        logging.info(f"🔶 BNB 잔고: {bnb_info['balance']:.4f} BNB (${bnb_info['value']:.2f})")
        
        if bnb_info['value'] < BNB_MIN_BALANCE:
            logging.info(f"🔶 BNB 잔고 부족, 충전 시작...")
            usdt_balance = get_usdt_balance()
            if usdt_balance < BNB_RECHARGE_AMOUNT:
                logging.warning(f"⚠️ USDT 잔고 부족으로 BNB 충전 불가")
                return None
            try:
                exchange.create_market_buy_order('BNB/USDT', None, {'quoteOrderQty': BNB_RECHARGE_AMOUNT})
                time.sleep(1)
                new_bnb_info = get_bnb_balance()
                logging.info(f"✅ BNB 충전 완료: {new_bnb_info['balance']:.4f} BNB")
                return {'action': 'recharged', 'new_balance': new_bnb_info['balance']}
            except Exception as e:
                logging.error(f"❌ BNB 충전 실패: {e}")
                return None
        return {'action': 'sufficient', 'balance': bnb_info['balance']}
    except Exception as e:
        logging.error(f"BNB 충전 확인 중 오류: {e}")
        return None


# ============================================================
# 잔고 조회
# ============================================================

def get_usdt_balance():
    try:
        balance = exchange.fetch_balance()
        return float(balance['USDT']['free'])
    except Exception as e:
        logging.error(f"USDT 잔고 조회 중 오류: {e}")
        return 0


def get_total_asset():
    try:
        balance = exchange.fetch_balance()
        total = 0.0
        for currency, amounts in balance['total'].items():
            if amounts > 0:
                if currency == 'USDT':
                    total += amounts
                else:
                    symbol = f"{currency}/USDT"
                    if symbol in exchange.markets:
                        try:
                            ticker = exchange.fetch_ticker(symbol)
                            total += amounts * ticker['last']
                            time.sleep(0.05)
                        except:
                            pass
        return total
    except Exception as e:
        logging.error(f"총 자산 계산 중 오류: {e}")
        return 0


def get_coin_balance(symbol):
    try:
        base = symbol.split('/')[0]
        balance = exchange.fetch_balance()
        return float(balance.get(base, {}).get('free', 0))
    except Exception as e:
        logging.error(f"{symbol} 잔고 조회 중 오류: {e}")
        return 0


def get_all_holdings():
    """보유 중인 모든 코인 조회"""
    try:
        balance = exchange.fetch_balance()
        holdings = {}
        for currency, amounts in balance['total'].items():
            if amounts > 0 and currency not in ['USDT', 'USD']:
                symbol = f"{currency}/USDT"
                if symbol in exchange.markets:
                    try:
                        ticker = exchange.fetch_ticker(symbol)
                        value = amounts * ticker['last']
                        if value >= MIN_ORDER_USDT:
                            holdings[symbol] = {'balance': amounts, 'price': ticker['last'], 'value': value}
                        time.sleep(0.05)
                    except:
                        pass
        return holdings
    except Exception as e:
        logging.error(f"보유 코인 조회 중 오류: {e}")
        return {}


def count_empty_slots():
    try:
        balance = exchange.fetch_balance()
        empty_count = 0
        for symbol in COINS:
            if symbol == 'BNB/USDT':
                continue
            base = symbol.split('/')[0]
            coin_balance = float(balance.get(base, {}).get('free', 0))
            if symbol in exchange.markets:
                try:
                    ticker = exchange.fetch_ticker(symbol)
                    if coin_balance * ticker['last'] < MIN_ORDER_USDT:
                        empty_count += 1
                    time.sleep(0.02)
                except:
                    empty_count += 1
            else:
                empty_count += 1
        return empty_count
    except Exception as e:
        logging.error(f"빈 슬롯 계산 중 오류: {e}")
        return len(COINS) - 1  # BNB 제외


def calculate_invest_amount():
    usdt_balance = get_usdt_balance()
    empty_slots = count_empty_slots()
    if empty_slots == 0:
        return 0
    
    available_usdt = usdt_balance * 0.995
    amount_by_available = available_usdt / empty_slots
    total_asset = get_total_asset()
    max_by_equity = total_asset / (len(COINS) - 1)  # BNB 제외
    invest_amount = min(amount_by_available, max_by_equity)
    
    if invest_amount < MIN_ORDER_USDT:
        return 0
    return invest_amount


# ============================================================
# 시세 조회
# ============================================================

def fetch_ohlcv_batch(symbol, timeframe, limit):
    for retry in range(3):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            if retry < 2:
                time.sleep(1)
            else:
                logging.error(f"{symbol} OHLCV 조회 실패: {e}")
                return None
    return None


def get_current_price(symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker['last'])
    except Exception as e:
        logging.error(f"{symbol} 현재가 조회 중 오류: {e}")
        return None


def get_ma_price(symbol, period):
    try:
        df = fetch_ohlcv_batch(symbol, '4h', period + 10)
        if df is None or len(df) < period:
            return None
        return float(df['close'].tail(period).mean())
    except Exception as e:
        logging.error(f"{symbol} MA 계산 중 오류: {e}")
        return None


# ============================================================
# 스토캐스틱
# ============================================================

def calculate_stochastic(df, k_period, k_smooth, d_period):
    if df is None or len(df) < k_period:
        return None, None
    low_min = df['low'].rolling(window=k_period).min()
    high_max = df['high'].rolling(window=k_period).max()
    denom = (high_max - low_min).replace(0, np.nan)
    fast_k = ((df['close'] - low_min) / denom) * 100
    slow_k = fast_k.rolling(window=k_smooth).mean()
    slow_d = slow_k.rolling(window=d_period).mean()
    if pd.isna(slow_k.iloc[-1]) or pd.isna(slow_d.iloc[-1]):
        return None, None
    return float(slow_k.iloc[-1]), float(slow_d.iloc[-1])


def should_refresh_stoch_cache():
    global stoch_cache_date
    now_utc = datetime.now(timezone.utc)
    if stoch_cache_date is None or stoch_cache_date < now_utc.date():
        return True
    return False


def refresh_all_stochastic():
    global stoch_cache, stoch_cache_date
    logging.info("📊 스토캐스틱 데이터 전체 갱신 시작...")
    
    for symbol in COINS:
        try:
            params = STOCH_PARAMS.get(symbol, {'k_period': 100, 'k_smooth': 30, 'd_period': 10})
            required_count = params['k_period'] + params['k_smooth'] + params['d_period'] + 20
            df = fetch_ohlcv_batch(symbol, '1d', required_count)
            if df is None:
                continue
            slow_k, slow_d = calculate_stochastic(df, params['k_period'], params['k_smooth'], params['d_period'])
            if slow_k is not None and slow_d is not None:
                stoch_cache[symbol] = {'signal': bool(slow_k > slow_d), 'slow_k': slow_k, 'slow_d': slow_d}
            time.sleep(0.1)
        except Exception as e:
            logging.error(f"{symbol} 스토캐스틱 계산 중 오류: {e}")
    
    stoch_cache_date = datetime.now(timezone.utc).date()
    save_stoch_cache()
    logging.info(f"📊 스토캐스틱 데이터 갱신 완료: {len(stoch_cache)}개 코인")


def get_stochastic_signal(symbol):
    if should_refresh_stoch_cache():
        refresh_all_stochastic()
    return stoch_cache.get(symbol)


# ============================================================
# 부적합 코인 매도 (BNB 제외)
# ============================================================

def sell_excluded_coins():
    """투자 적합 코인 목록에 없는 보유 코인 매도 (BNB 제외)"""
    excluded_sell_list = []
    try:
        holdings = get_all_holdings()
        for symbol, info in holdings.items():
            # BNB는 수수료용이므로 매도하지 않음
            if symbol == 'BNB/USDT':
                logging.info(f"🔶 {symbol} - BNB는 수수료용으로 매도 제외")
                continue
            
            # 적합 코인 목록에 없으면 매도
            if symbol not in COINS:
                try:
                    if info['value'] >= MIN_ORDER_USDT:
                        exchange.create_market_sell_order(symbol, info['balance'])
                        excluded_sell_list.append({'symbol': symbol, 'reason': '투자 부적합 코인', 'value': info['value']})
                        logging.info(f"🚫 {symbol} 매도 완료 (투자 부적합 코인) - ${info['value']:.2f}")
                        time.sleep(0.2)
                except Exception as e:
                    logging.error(f"{symbol} 부적합 코인 매도 실패: {e}")
        return excluded_sell_list
    except Exception as e:
        logging.error(f"부적합 코인 매도 중 오류: {e}")
        return excluded_sell_list


# ============================================================
# 메인 거래 전략
# ============================================================

def trade_strategy():
    global exchange
    buy_list, sell_list, excluded_sell_list, errors = [], [], [], []
    
    try:
        if exchange is None:
            logging.info("📡 거래소 재초기화 시도...")
            if not init_exchange():
                logging.warning("⚠️ 거래소 초기화 실패")
                send_telegram("⚠️ <b>바이낸스 거래소 초기화 실패</b>")
                return
        
        logging.info("📡 API 연결 상태 확인 중...")
        test_balance = call_with_timeout(lambda: exchange.fetch_balance(), timeout=30)
        if test_balance is None:
            logging.warning("⚠️ API 조회 실패")
            send_telegram("⚠️ <b>바이낸스 API 조회 실패</b>")
            return
        
        logging.info("✅ API 연결 정상")
        
        # BNB 자동 충전
        bnb_result = check_and_recharge_bnb()
        
        # 부적합 코인 먼저 매도 (BNB 제외)
        logging.info("🚫 부적합 코인 매도 확인 중...")
        excluded_sell_list = sell_excluded_coins()
        
        usdt_balance = get_usdt_balance()
        total_asset = get_total_asset()
        bnb_info = get_bnb_balance()
        
        if usdt_balance == 0 and total_asset == 0:
            logging.warning("⚠️ 자산 조회 실패")
            return
        
        logging.info("=" * 80)
        logging.info(f"📊 거래 전략 실행 - 총자산: ${total_asset:,.2f}, USDT: ${usdt_balance:,.2f}")
        logging.info("=" * 80)
        
        for symbol in COINS:
            if symbol == 'BNB/USDT':  # BNB는 거래 대상에서 제외
                continue
            
            try:
                time.sleep(0.15)
                ma_period = MA_PERIODS.get(symbol, 100)
                ma_price = get_ma_price(symbol, ma_period)
                current_price = get_current_price(symbol)
                stoch_data = get_stochastic_signal(symbol)
                
                if ma_price is None or current_price is None:
                    continue
                
                coin_balance = get_coin_balance(symbol)
                coin_value = coin_balance * current_price
                
                ma_condition = current_price > ma_price
                stoch_condition = stoch_data['signal'] if stoch_data and stoch_data.get('signal') is not None else True
                final_buy_condition = ma_condition and stoch_condition
                
                if final_buy_condition:
                    if coin_value < MIN_ORDER_USDT:
                        invest_amount = calculate_invest_amount()
                        if invest_amount >= MIN_ORDER_USDT:
                            try:
                                exchange.create_market_buy_order(symbol, None, {'quoteOrderQty': invest_amount})
                                buy_list.append({'symbol': symbol, 'amount': invest_amount})
                                logging.info(f"🟢 {symbol} 매수 완료: ${invest_amount:.2f}")
                                time.sleep(0.2)
                            except Exception as e:
                                errors.append(f"{symbol} 매수 실패: {e}")
                else:
                    if coin_value >= MIN_ORDER_USDT:
                        try:
                            exchange.create_market_sell_order(symbol, coin_balance)
                            sell_reason = "MA 조건 미충족" if not ma_condition else "스토캐스틱 조건 미충족"
                            sell_list.append({'symbol': symbol, 'reason': sell_reason})
                            logging.info(f"🔴 {symbol} 매도 완료 ({sell_reason})")
                            time.sleep(0.2)
                        except Exception as e:
                            errors.append(f"{symbol} 매도 실패: {e}")
            
            except Exception as e:
                errors.append(f"{symbol} 처리 중 오류: {e}")
        
        final_total = get_total_asset()
        final_usdt = get_usdt_balance()
        final_bnb = get_bnb_balance()
        send_trade_summary(buy_list, sell_list, excluded_sell_list, final_total, final_usdt, final_bnb, errors)
        
        logging.info("=" * 80)
        logging.info(f"📊 완료 - 매수: {len(buy_list)}건 / 전략매도: {len(sell_list)}건 / 부적합매도: {len(excluded_sell_list)}건")
        logging.info("=" * 80)
        
    except Exception as e:
        logging.error(f"전략 실행 중 오류: {e}")
        send_trade_summary([], [], [], 0, 0, None, [f"전략 실행 오류: {e}"])


def log_strategy_info():
    logging.info("=" * 80)
    logging.info("🤖 바이낸스 자동매매 봇 v2.0.0")
    logging.info("=" * 80)
    logging.info("📈 매수: 현재가 > MA(4H) AND Slow %K > Slow %D (1D)")
    logging.info("📉 매도: 매수 조건 미충족 OR 부적합 코인 (BNB 제외)")
    logging.info(f"🪙 거래 대상: {len(COINS)}개 코인")
    logging.info(f"🔶 BNB 자동충전: ${BNB_MIN_BALANCE} 이하시 ${BNB_RECHARGE_AMOUNT} 매수")
    logging.info("=" * 80)


def main():
    global BOT_START_TIME
    BOT_START_TIME = datetime.now()
    
    setup_shutdown_handlers()
    
    if not init_exchange():
        logging.warning("⚠️ 거래소 초기화 실패")
        send_telegram("⚠️ <b>바이낸스 거래소 초기화 실패</b>")
    
    load_stoch_cache()
    log_strategy_info()
    send_start_alert()
    
    # 스케줄 설정 (KST)
    schedule.every().day.at("01:00").do(trade_strategy)
    schedule.every().day.at("05:00").do(trade_strategy)
    schedule.every().day.at("09:00").do(trade_strategy)
    schedule.every().day.at("13:00").do(trade_strategy)
    schedule.every().day.at("17:00").do(trade_strategy)
    schedule.every().day.at("21:00").do(trade_strategy)
    
    logging.info("실행 시간 (KST): 01:00, 05:00, 09:00, 13:00, 17:00, 21:00")
    
    if exchange is not None:
        logging.info("🚀 시작 시 전략 즉시 실행...")
        trade_strategy()
    
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
