"""
================================================================================
바이낸스 자동매매 봇 v4.0.0 (Spot + USDS-M Futures 숏)
================================================================================
- 기존 Spot 매매 (MA + 스토캐스틱 롱 전략) 유지
- USDS-M Futures 숏 포지션 매매 추가
- 숏 전략: 가격 < MA AND 스토캐스틱 K < D → 숏 진입
- Futures BNB 자동 충전 (수수료 할인용)
- Spot/Futures 별도 지갑 관리
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
FUTURES_STOCH_CACHE_FILE = os.path.join(os.path.expanduser('~'), 'binance_futures_stoch_cache.json')

# ============================================================
# 거래 설정
# ============================================================

# Spot 설정
FEE_RATE = 0.00075  # 0.075% (BNB 할인)
MIN_ORDER_USDT = 11  # 최소 주문 금액

# Spot BNB 자동 충전 설정
BNB_MIN_BALANCE = 15  # BNB 최소 보유량 (USDT 기준)
BNB_RECHARGE_AMOUNT = 30  # 충전 시 매수할 금액 (USDT)

# Futures 설정
FUTURES_FEE_RATE = 0.0006  # 0.06% (BNB 할인 적용 시, 기본 0.04% maker / 0.04% taker)
FUTURES_MIN_ORDER_USDT = 6  # Futures 최소 주문 금액 (보통 5 USDT)

# Futures BNB 자동 충전 설정 (Futures 지갑에서 BNB 부족 시)
FUTURES_BNB_MIN_BALANCE = 10  # Futures 지갑 BNB 최소 보유량 (USDT 기준)
FUTURES_BNB_RECHARGE_AMOUNT = 20  # 충전 시 매수할 금액 (USDT)

# ============================================================
# 종료 알림 관련 전역 변수
# ============================================================

BOT_START_TIME = None
SHUTDOWN_SENT = False

# ============================================================
# Spot 투자 적합 코인 135개 (기존 유지)
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
# Spot MA 기간 (기존 유지)
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
# Spot 스토캐스틱 파라미터 (기존 유지)
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
# USDS-M Futures 숏 포지션 설정 (백테스트 결과 2026-02-02)
# ============================================================
# 총 318개 코인 분석, 234개 적합 코인 선정
# 제외 기준: CAGR <= 0%, MDD < -85%, Sharpe < 0.7,
#           과적합 의심 (거래횟수 <= 2 & 기간 < 500일),
#           비정상적 CAGR (> 10000% & 거래횟수 < 10)
# ============================================================

SHORT_TRADING_CONFIGS = [
    {'symbol': 'AEROUSDT', 'ma_period': 66, 'stoch_k_period': 150, 'stoch_k_smooth': 65, 'stoch_d_period': 30, 'leverage': 5},
    {'symbol': 'COOKIEUSDT', 'ma_period': 150, 'stoch_k_period': 113, 'stoch_k_smooth': 58, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'MEUSDT', 'ma_period': 178, 'stoch_k_period': 128, 'stoch_k_smooth': 26, 'stoch_d_period': 37, 'leverage': 5},
    {'symbol': 'VANRYUSDT', 'ma_period': 99, 'stoch_k_period': 148, 'stoch_k_smooth': 59, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'ZKUSDT', 'ma_period': 86, 'stoch_k_period': 77, 'stoch_k_smooth': 78, 'stoch_d_period': 40, 'leverage': 5},
    {'symbol': 'SYNUSDT', 'ma_period': 129, 'stoch_k_period': 103, 'stoch_k_smooth': 36, 'stoch_d_period': 24, 'leverage': 5},
    {'symbol': '1000CATUSDT', 'ma_period': 178, 'stoch_k_period': 46, 'stoch_k_smooth': 7, 'stoch_d_period': 39, 'leverage': 5},
    {'symbol': 'PENGUUSDT', 'ma_period': 102, 'stoch_k_period': 117, 'stoch_k_smooth': 73, 'stoch_d_period': 27, 'leverage': 5},
    {'symbol': 'SAGAUSDT', 'ma_period': 250, 'stoch_k_period': 99, 'stoch_k_smooth': 79, 'stoch_d_period': 38, 'leverage': 5},
    {'symbol': 'MOVEUSDT', 'ma_period': 145, 'stoch_k_period': 50, 'stoch_k_smooth': 64, 'stoch_d_period': 27, 'leverage': 5},
    {'symbol': 'FIDAUSDT', 'ma_period': 141, 'stoch_k_period': 54, 'stoch_k_smooth': 32, 'stoch_d_period': 41, 'leverage': 5},
    {'symbol': 'NOTUSDT', 'ma_period': 40, 'stoch_k_period': 145, 'stoch_k_smooth': 65, 'stoch_d_period': 38, 'leverage': 5},
    {'symbol': 'SPXUSDT', 'ma_period': 62, 'stoch_k_period': 146, 'stoch_k_smooth': 64, 'stoch_d_period': 36, 'leverage': 4},
    {'symbol': '1MBABYDOGEUSDT', 'ma_period': 39, 'stoch_k_period': 85, 'stoch_k_smooth': 10, 'stoch_d_period': 7, 'leverage': 5},
    {'symbol': 'CGPTUSDT', 'ma_period': 69, 'stoch_k_period': 79, 'stoch_k_smooth': 62, 'stoch_d_period': 49, 'leverage': 4},
    {'symbol': 'BRETTUSDT', 'ma_period': 267, 'stoch_k_period': 84, 'stoch_k_smooth': 26, 'stoch_d_period': 43, 'leverage': 5},
    {'symbol': 'ACTUSDT', 'ma_period': 118, 'stoch_k_period': 89, 'stoch_k_smooth': 74, 'stoch_d_period': 31, 'leverage': 5},
    {'symbol': 'MEWUSDT', 'ma_period': 106, 'stoch_k_period': 130, 'stoch_k_smooth': 47, 'stoch_d_period': 27, 'leverage': 5},
    {'symbol': 'BOMEUSDT', 'ma_period': 182, 'stoch_k_period': 150, 'stoch_k_smooth': 79, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'KAIAUSDT', 'ma_period': 49, 'stoch_k_period': 149, 'stoch_k_smooth': 61, 'stoch_d_period': 29, 'leverage': 5},
    {'symbol': 'RPLUSDT', 'ma_period': 115, 'stoch_k_period': 124, 'stoch_k_smooth': 15, 'stoch_d_period': 35, 'leverage': 5},
    {'symbol': 'TURBOUSDT', 'ma_period': 71, 'stoch_k_period': 91, 'stoch_k_smooth': 79, 'stoch_d_period': 35, 'leverage': 5},
    {'symbol': 'KOMAUSDT', 'ma_period': 229, 'stoch_k_period': 150, 'stoch_k_smooth': 76, 'stoch_d_period': 40, 'leverage': 5},
    {'symbol': 'LUMIAUSDT', 'ma_period': 293, 'stoch_k_period': 139, 'stoch_k_smooth': 49, 'stoch_d_period': 41, 'leverage': 5},
    {'symbol': 'PIXELUSDT', 'ma_period': 109, 'stoch_k_period': 87, 'stoch_k_smooth': 70, 'stoch_d_period': 38, 'leverage': 5},
    {'symbol': 'PORTALUSDT', 'ma_period': 152, 'stoch_k_period': 139, 'stoch_k_smooth': 78, 'stoch_d_period': 30, 'leverage': 5},
    {'symbol': 'GUSDT', 'ma_period': 131, 'stoch_k_period': 112, 'stoch_k_smooth': 51, 'stoch_d_period': 7, 'leverage': 5},
    {'symbol': 'POLUSDT', 'ma_period': 233, 'stoch_k_period': 24, 'stoch_k_smooth': 16, 'stoch_d_period': 41, 'leverage': 5},
    {'symbol': 'RENDERUSDT', 'ma_period': 110, 'stoch_k_period': 70, 'stoch_k_smooth': 29, 'stoch_d_period': 15, 'leverage': 5},
    {'symbol': 'XAIUSDT', 'ma_period': 116, 'stoch_k_period': 69, 'stoch_k_smooth': 54, 'stoch_d_period': 36, 'leverage': 5},
    {'symbol': 'ENAUSDT', 'ma_period': 204, 'stoch_k_period': 150, 'stoch_k_smooth': 63, 'stoch_d_period': 33, 'leverage': 5},
    {'symbol': 'DEGOUSDT', 'ma_period': 247, 'stoch_k_period': 147, 'stoch_k_smooth': 16, 'stoch_d_period': 6, 'leverage': 5},
    {'symbol': 'NFPUSDT', 'ma_period': 129, 'stoch_k_period': 118, 'stoch_k_smooth': 53, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'AKTUSDT', 'ma_period': 324, 'stoch_k_period': 98, 'stoch_k_smooth': 13, 'stoch_d_period': 13, 'leverage': 5},
    {'symbol': 'COWUSDT', 'ma_period': 265, 'stoch_k_period': 94, 'stoch_k_smooth': 29, 'stoch_d_period': 9, 'leverage': 5},
    {'symbol': 'SCRUSDT', 'ma_period': 135, 'stoch_k_period': 129, 'stoch_k_smooth': 72, 'stoch_d_period': 37, 'leverage': 5},
    {'symbol': 'WIFUSDT', 'ma_period': 175, 'stoch_k_period': 106, 'stoch_k_smooth': 63, 'stoch_d_period': 19, 'leverage': 5},
    {'symbol': 'FLUXUSDT', 'ma_period': 73, 'stoch_k_period': 77, 'stoch_k_smooth': 60, 'stoch_d_period': 38, 'leverage': 5},
    {'symbol': 'WUSDT', 'ma_period': 88, 'stoch_k_period': 70, 'stoch_k_smooth': 78, 'stoch_d_period': 42, 'leverage': 5},
    {'symbol': 'IOUSDT', 'ma_period': 124, 'stoch_k_period': 133, 'stoch_k_smooth': 57, 'stoch_d_period': 42, 'leverage': 5},
    {'symbol': '1000WHYUSDT', 'ma_period': 114, 'stoch_k_period': 104, 'stoch_k_smooth': 51, 'stoch_d_period': 26, 'leverage': 4},
    {'symbol': 'AEVOUSDT', 'ma_period': 165, 'stoch_k_period': 84, 'stoch_k_smooth': 78, 'stoch_d_period': 14, 'leverage': 5},
    {'symbol': 'RONINUSDT', 'ma_period': 67, 'stoch_k_period': 138, 'stoch_k_smooth': 59, 'stoch_d_period': 39, 'leverage': 5},
    {'symbol': 'ZETAUSDT', 'ma_period': 171, 'stoch_k_period': 41, 'stoch_k_smooth': 68, 'stoch_d_period': 24, 'leverage': 5},
    {'symbol': 'ORCAUSDT', 'ma_period': 133, 'stoch_k_period': 70, 'stoch_k_smooth': 33, 'stoch_d_period': 43, 'leverage': 5},
    {'symbol': 'MBOXUSDT', 'ma_period': 61, 'stoch_k_period': 97, 'stoch_k_smooth': 68, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'BLURUSDT', 'ma_period': 117, 'stoch_k_period': 107, 'stoch_k_smooth': 51, 'stoch_d_period': 27, 'leverage': 5},
    {'symbol': 'ALTUSDT', 'ma_period': 148, 'stoch_k_period': 124, 'stoch_k_smooth': 70, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'TNSRUSDT', 'ma_period': 269, 'stoch_k_period': 75, 'stoch_k_smooth': 79, 'stoch_d_period': 44, 'leverage': 5},
    {'symbol': 'MOODENGUSDT', 'ma_period': 288, 'stoch_k_period': 20, 'stoch_k_smooth': 18, 'stoch_d_period': 7, 'leverage': 3},
    {'symbol': 'NTRNUSDT', 'ma_period': 235, 'stoch_k_period': 148, 'stoch_k_smooth': 60, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'BBUSDT', 'ma_period': 225, 'stoch_k_period': 137, 'stoch_k_smooth': 71, 'stoch_d_period': 32, 'leverage': 5},
    {'symbol': 'DYMUSDT', 'ma_period': 312, 'stoch_k_period': 143, 'stoch_k_smooth': 72, 'stoch_d_period': 43, 'leverage': 5},
    {'symbol': 'SYSUSDT', 'ma_period': 206, 'stoch_k_period': 48, 'stoch_k_smooth': 45, 'stoch_d_period': 11, 'leverage': 4},
    {'symbol': 'BANANAUSDT', 'ma_period': 279, 'stoch_k_period': 130, 'stoch_k_smooth': 66, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'CHILLGUYUSDT', 'ma_period': 347, 'stoch_k_period': 126, 'stoch_k_smooth': 77, 'stoch_d_period': 38, 'leverage': 5},
    {'symbol': 'PYTHUSDT', 'ma_period': 176, 'stoch_k_period': 63, 'stoch_k_smooth': 76, 'stoch_d_period': 19, 'leverage': 5},
    {'symbol': 'CHESSUSDT', 'ma_period': 114, 'stoch_k_period': 85, 'stoch_k_smooth': 76, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'METISUSDT', 'ma_period': 250, 'stoch_k_period': 145, 'stoch_k_smooth': 78, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'DFUSDT', 'ma_period': 289, 'stoch_k_period': 36, 'stoch_k_smooth': 10, 'stoch_d_period': 44, 'leverage': 5},
    {'symbol': 'GHSTUSDT', 'ma_period': 300, 'stoch_k_period': 55, 'stoch_k_smooth': 39, 'stoch_d_period': 23, 'leverage': 5},
    {'symbol': 'AIUSDT', 'ma_period': 101, 'stoch_k_period': 20, 'stoch_k_smooth': 79, 'stoch_d_period': 11, 'leverage': 5},
    {'symbol': 'BICOUSDT', 'ma_period': 285, 'stoch_k_period': 120, 'stoch_k_smooth': 71, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'STRKUSDT', 'ma_period': 167, 'stoch_k_period': 117, 'stoch_k_smooth': 56, 'stoch_d_period': 45, 'leverage': 5},
    {'symbol': 'MANTAUSDT', 'ma_period': 173, 'stoch_k_period': 34, 'stoch_k_smooth': 38, 'stoch_d_period': 7, 'leverage': 5},
    {'symbol': 'ARBUSDT', 'ma_period': 208, 'stoch_k_period': 145, 'stoch_k_smooth': 80, 'stoch_d_period': 21, 'leverage': 5},
    {'symbol': 'GRASSUSDT', 'ma_period': 268, 'stoch_k_period': 111, 'stoch_k_smooth': 48, 'stoch_d_period': 23, 'leverage': 5},
    {'symbol': 'ILVUSDT', 'ma_period': 182, 'stoch_k_period': 107, 'stoch_k_smooth': 80, 'stoch_d_period': 29, 'leverage': 5},
    {'symbol': 'SAFEUSDT', 'ma_period': 317, 'stoch_k_period': 22, 'stoch_k_smooth': 64, 'stoch_d_period': 41, 'leverage': 4},
    {'symbol': 'SCRTUSDT', 'ma_period': 105, 'stoch_k_period': 145, 'stoch_k_smooth': 52, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'SEIUSDT', 'ma_period': 192, 'stoch_k_period': 121, 'stoch_k_smooth': 65, 'stoch_d_period': 39, 'leverage': 5},
    {'symbol': 'WLDUSDT', 'ma_period': 126, 'stoch_k_period': 146, 'stoch_k_smooth': 73, 'stoch_d_period': 18, 'leverage': 4},
    {'symbol': 'POPCATUSDT', 'ma_period': 227, 'stoch_k_period': 92, 'stoch_k_smooth': 44, 'stoch_d_period': 3, 'leverage': 2},
    {'symbol': 'DODOXUSDT', 'ma_period': 232, 'stoch_k_period': 144, 'stoch_k_smooth': 29, 'stoch_d_period': 23, 'leverage': 5},
    {'symbol': 'LSKUSDT', 'ma_period': 82, 'stoch_k_period': 115, 'stoch_k_smooth': 64, 'stoch_d_period': 44, 'leverage': 5},
    {'symbol': 'POLYXUSDT', 'ma_period': 239, 'stoch_k_period': 124, 'stoch_k_smooth': 65, 'stoch_d_period': 33, 'leverage': 5},
    {'symbol': 'RAREUSDT', 'ma_period': 188, 'stoch_k_period': 39, 'stoch_k_smooth': 75, 'stoch_d_period': 35, 'leverage': 5},
    {'symbol': 'WAXPUSDT', 'ma_period': 154, 'stoch_k_period': 128, 'stoch_k_smooth': 72, 'stoch_d_period': 39, 'leverage': 5},
    {'symbol': 'BEAMXUSDT', 'ma_period': 126, 'stoch_k_period': 90, 'stoch_k_smooth': 78, 'stoch_d_period': 33, 'leverage': 4},
    {'symbol': 'ETHWUSDT', 'ma_period': 290, 'stoch_k_period': 133, 'stoch_k_smooth': 74, 'stoch_d_period': 40, 'leverage': 5},
    {'symbol': 'AVAUSDT', 'ma_period': 99, 'stoch_k_period': 148, 'stoch_k_smooth': 79, 'stoch_d_period': 38, 'leverage': 4},
    {'symbol': 'DOGSUSDT', 'ma_period': 39, 'stoch_k_period': 22, 'stoch_k_smooth': 45, 'stoch_d_period': 34, 'leverage': 4},
    {'symbol': 'IDUSDT', 'ma_period': 46, 'stoch_k_period': 144, 'stoch_k_smooth': 77, 'stoch_d_period': 28, 'leverage': 5},
    {'symbol': 'TONUSDT', 'ma_period': 115, 'stoch_k_period': 144, 'stoch_k_smooth': 77, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'HFTUSDT', 'ma_period': 331, 'stoch_k_period': 149, 'stoch_k_smooth': 79, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'MOVRUSDT', 'ma_period': 218, 'stoch_k_period': 149, 'stoch_k_smooth': 55, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': '1000CHEEMSUSDT', 'ma_period': 327, 'stoch_k_period': 148, 'stoch_k_smooth': 52, 'stoch_d_period': 36, 'leverage': 5},
    {'symbol': 'STEEMUSDT', 'ma_period': 49, 'stoch_k_period': 147, 'stoch_k_smooth': 53, 'stoch_d_period': 18, 'leverage': 5},
    {'symbol': 'NEIROUSDT', 'ma_period': 324, 'stoch_k_period': 105, 'stoch_k_smooth': 74, 'stoch_d_period': 50, 'leverage': 4},
    {'symbol': 'ARKMUSDT', 'ma_period': 159, 'stoch_k_period': 89, 'stoch_k_smooth': 30, 'stoch_d_period': 28, 'leverage': 5},
    {'symbol': 'ETHFIUSDT', 'ma_period': 229, 'stoch_k_period': 142, 'stoch_k_smooth': 61, 'stoch_d_period': 11, 'leverage': 5},
    {'symbol': 'MOCAUSDT', 'ma_period': 124, 'stoch_k_period': 150, 'stoch_k_smooth': 75, 'stoch_d_period': 46, 'leverage': 4},
    {'symbol': 'CATIUSDT', 'ma_period': 320, 'stoch_k_period': 26, 'stoch_k_smooth': 7, 'stoch_d_period': 35, 'leverage': 5},
    {'symbol': 'ORDIUSDT', 'ma_period': 172, 'stoch_k_period': 84, 'stoch_k_smooth': 78, 'stoch_d_period': 41, 'leverage': 5},
    {'symbol': 'CAKEUSDT', 'ma_period': 39, 'stoch_k_period': 144, 'stoch_k_smooth': 78, 'stoch_d_period': 24, 'leverage': 4},
    {'symbol': 'CYBERUSDT', 'ma_period': 350, 'stoch_k_period': 116, 'stoch_k_smooth': 77, 'stoch_d_period': 37, 'leverage': 5},
    {'symbol': 'OXTUSDT', 'ma_period': 284, 'stoch_k_period': 150, 'stoch_k_smooth': 59, 'stoch_d_period': 42, 'leverage': 5},
    {'symbol': 'SUIUSDT', 'ma_period': 108, 'stoch_k_period': 149, 'stoch_k_smooth': 77, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'RIFUSDT', 'ma_period': 258, 'stoch_k_period': 77, 'stoch_k_smooth': 59, 'stoch_d_period': 34, 'leverage': 5},
    {'symbol': 'POWRUSDT', 'ma_period': 183, 'stoch_k_period': 146, 'stoch_k_smooth': 67, 'stoch_d_period': 29, 'leverage': 5},
    {'symbol': 'MAGICUSDT', 'ma_period': 277, 'stoch_k_period': 121, 'stoch_k_smooth': 36, 'stoch_d_period': 44, 'leverage': 5},
    {'symbol': 'ZEREBROUSDT', 'ma_period': 277, 'stoch_k_period': 58, 'stoch_k_smooth': 48, 'stoch_d_period': 16, 'leverage': 4},
    {'symbol': 'FIOUSDT', 'ma_period': 265, 'stoch_k_period': 75, 'stoch_k_smooth': 14, 'stoch_d_period': 19, 'leverage': 5},
    {'symbol': 'SUNUSDT', 'ma_period': 32, 'stoch_k_period': 150, 'stoch_k_smooth': 54, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'SANTOSUSDT', 'ma_period': 181, 'stoch_k_period': 136, 'stoch_k_smooth': 39, 'stoch_d_period': 7, 'leverage': 5},
    {'symbol': 'ONDOUSDT', 'ma_period': 319, 'stoch_k_period': 101, 'stoch_k_smooth': 80, 'stoch_d_period': 12, 'leverage': 5},
    {'symbol': 'ACEUSDT', 'ma_period': 346, 'stoch_k_period': 138, 'stoch_k_smooth': 62, 'stoch_d_period': 27, 'leverage': 4},
    {'symbol': 'BIGTIMEUSDT', 'ma_period': 201, 'stoch_k_period': 67, 'stoch_k_smooth': 66, 'stoch_d_period': 8, 'leverage': 4},
    {'symbol': 'RDNTUSDT', 'ma_period': 94, 'stoch_k_period': 59, 'stoch_k_smooth': 15, 'stoch_d_period': 6, 'leverage': 4},
    {'symbol': 'AUCTIONUSDT', 'ma_period': 197, 'stoch_k_period': 142, 'stoch_k_smooth': 31, 'stoch_d_period': 10, 'leverage': 5},
    {'symbol': 'BNTUSDT', 'ma_period': 290, 'stoch_k_period': 112, 'stoch_k_smooth': 64, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'FLOWUSDT', 'ma_period': 218, 'stoch_k_period': 59, 'stoch_k_smooth': 29, 'stoch_d_period': 19, 'leverage': 4},
    {'symbol': '1000BONKUSDT', 'ma_period': 328, 'stoch_k_period': 119, 'stoch_k_smooth': 68, 'stoch_d_period': 50, 'leverage': 4},
    {'symbol': 'TIAUSDT', 'ma_period': 127, 'stoch_k_period': 92, 'stoch_k_smooth': 40, 'stoch_d_period': 4, 'leverage': 4},
    {'symbol': 'MANAUSDT', 'ma_period': 161, 'stoch_k_period': 150, 'stoch_k_smooth': 78, 'stoch_d_period': 32, 'leverage': 5},
    {'symbol': 'APEUSDT', 'ma_period': 136, 'stoch_k_period': 132, 'stoch_k_smooth': 71, 'stoch_d_period': 29, 'leverage': 5},
    {'symbol': '1000FLOKIUSDT', 'ma_period': 106, 'stoch_k_period': 103, 'stoch_k_smooth': 74, 'stoch_d_period': 50, 'leverage': 3},
    {'symbol': 'AGLDUSDT', 'ma_period': 335, 'stoch_k_period': 135, 'stoch_k_smooth': 80, 'stoch_d_period': 39, 'leverage': 5},
    {'symbol': 'AXLUSDT', 'ma_period': 36, 'stoch_k_period': 149, 'stoch_k_smooth': 40, 'stoch_d_period': 33, 'leverage': 5},
    {'symbol': 'REZUSDT', 'ma_period': 243, 'stoch_k_period': 28, 'stoch_k_smooth': 18, 'stoch_d_period': 13, 'leverage': 3},
    {'symbol': 'STXUSDT', 'ma_period': 93, 'stoch_k_period': 136, 'stoch_k_smooth': 75, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'GMTUSDT', 'ma_period': 107, 'stoch_k_period': 133, 'stoch_k_smooth': 68, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'USTCUSDT', 'ma_period': 53, 'stoch_k_period': 98, 'stoch_k_smooth': 79, 'stoch_d_period': 16, 'leverage': 5},
    {'symbol': 'ALCHUSDT', 'ma_period': 297, 'stoch_k_period': 69, 'stoch_k_smooth': 34, 'stoch_d_period': 12, 'leverage': 5},
    {'symbol': 'KASUSDT', 'ma_period': 152, 'stoch_k_period': 139, 'stoch_k_smooth': 61, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'ONEUSDT', 'ma_period': 123, 'stoch_k_period': 78, 'stoch_k_smooth': 50, 'stoch_d_period': 44, 'leverage': 4},
    {'symbol': 'JUPUSDT', 'ma_period': 39, 'stoch_k_period': 146, 'stoch_k_smooth': 59, 'stoch_d_period': 28, 'leverage': 4},
    {'symbol': 'HIGHUSDT', 'ma_period': 317, 'stoch_k_period': 146, 'stoch_k_smooth': 61, 'stoch_d_period': 45, 'leverage': 5},
    {'symbol': 'BSVUSDT', 'ma_period': 38, 'stoch_k_period': 121, 'stoch_k_smooth': 38, 'stoch_d_period': 18, 'leverage': 4},
    {'symbol': 'SPELLUSDT', 'ma_period': 227, 'stoch_k_period': 97, 'stoch_k_smooth': 58, 'stoch_d_period': 43, 'leverage': 5},
    {'symbol': 'ASTRUSDT', 'ma_period': 87, 'stoch_k_period': 58, 'stoch_k_smooth': 71, 'stoch_d_period': 30, 'leverage': 5},
    {'symbol': 'LUNA2USDT', 'ma_period': 254, 'stoch_k_period': 137, 'stoch_k_smooth': 9, 'stoch_d_period': 25, 'leverage': 3},
    {'symbol': 'JTOUSDT', 'ma_period': 122, 'stoch_k_period': 116, 'stoch_k_smooth': 78, 'stoch_d_period': 37, 'leverage': 3},
    {'symbol': '1000PEPEUSDT', 'ma_period': 50, 'stoch_k_period': 63, 'stoch_k_smooth': 78, 'stoch_d_period': 31, 'leverage': 5},
    {'symbol': 'OGNUSDT', 'ma_period': 120, 'stoch_k_period': 120, 'stoch_k_smooth': 73, 'stoch_d_period': 45, 'leverage': 4},
    {'symbol': 'ZROUSDT', 'ma_period': 122, 'stoch_k_period': 82, 'stoch_k_smooth': 50, 'stoch_d_period': 31, 'leverage': 5},
    {'symbol': 'IMXUSDT', 'ma_period': 105, 'stoch_k_period': 118, 'stoch_k_smooth': 46, 'stoch_d_period': 42, 'leverage': 5},
    {'symbol': 'HBARUSDT', 'ma_period': 129, 'stoch_k_period': 111, 'stoch_k_smooth': 76, 'stoch_d_period': 40, 'leverage': 5},
    {'symbol': 'MAVUSDT', 'ma_period': 31, 'stoch_k_period': 150, 'stoch_k_smooth': 67, 'stoch_d_period': 16, 'leverage': 4},
    {'symbol': 'OPUSDT', 'ma_period': 343, 'stoch_k_period': 144, 'stoch_k_smooth': 75, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'SANDUSDT', 'ma_period': 124, 'stoch_k_period': 142, 'stoch_k_smooth': 72, 'stoch_d_period': 44, 'leverage': 5},
    {'symbol': 'MINAUSDT', 'ma_period': 311, 'stoch_k_period': 44, 'stoch_k_smooth': 40, 'stoch_d_period': 23, 'leverage': 5},
    {'symbol': 'PENDLEUSDT', 'ma_period': 129, 'stoch_k_period': 91, 'stoch_k_smooth': 52, 'stoch_d_period': 44, 'leverage': 5},
    {'symbol': 'HOOKUSDT', 'ma_period': 323, 'stoch_k_period': 137, 'stoch_k_smooth': 71, 'stoch_d_period': 43, 'leverage': 4},
    {'symbol': 'JOEUSDT', 'ma_period': 207, 'stoch_k_period': 125, 'stoch_k_smooth': 79, 'stoch_d_period': 47, 'leverage': 4},
    {'symbol': 'MORPHOUSDT', 'ma_period': 130, 'stoch_k_period': 150, 'stoch_k_smooth': 78, 'stoch_d_period': 40, 'leverage': 5},
    {'symbol': 'PHBUSDT', 'ma_period': 135, 'stoch_k_period': 144, 'stoch_k_smooth': 68, 'stoch_d_period': 38, 'leverage': 4},
    {'symbol': '1000SATSUSDT', 'ma_period': 350, 'stoch_k_period': 97, 'stoch_k_smooth': 34, 'stoch_d_period': 7, 'leverage': 3},
    {'symbol': 'IOTXUSDT', 'ma_period': 203, 'stoch_k_period': 149, 'stoch_k_smooth': 70, 'stoch_d_period': 26, 'leverage': 5},
    {'symbol': 'FETUSDT', 'ma_period': 143, 'stoch_k_period': 134, 'stoch_k_smooth': 34, 'stoch_d_period': 38, 'leverage': 5},
    {'symbol': 'STGUSDT', 'ma_period': 83, 'stoch_k_period': 150, 'stoch_k_smooth': 42, 'stoch_d_period': 34, 'leverage': 5},
    {'symbol': 'WOOUSDT', 'ma_period': 198, 'stoch_k_period': 99, 'stoch_k_smooth': 69, 'stoch_d_period': 37, 'leverage': 4},
    {'symbol': 'EDUUSDT', 'ma_period': 40, 'stoch_k_period': 108, 'stoch_k_smooth': 65, 'stoch_d_period': 20, 'leverage': 3},
    {'symbol': 'CKBUSDT', 'ma_period': 217, 'stoch_k_period': 112, 'stoch_k_smooth': 79, 'stoch_d_period': 18, 'leverage': 4},
    {'symbol': 'ICXUSDT', 'ma_period': 123, 'stoch_k_period': 138, 'stoch_k_smooth': 57, 'stoch_d_period': 47, 'leverage': 4},
    {'symbol': 'IOSTUSDT', 'ma_period': 245, 'stoch_k_period': 82, 'stoch_k_smooth': 80, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'AVAXUSDT', 'ma_period': 93, 'stoch_k_period': 87, 'stoch_k_smooth': 18, 'stoch_d_period': 24, 'leverage': 3},
    {'symbol': '1000LUNCUSDT', 'ma_period': 83, 'stoch_k_period': 130, 'stoch_k_smooth': 38, 'stoch_d_period': 11, 'leverage': 3},
    {'symbol': 'API3USDT', 'ma_period': 230, 'stoch_k_period': 142, 'stoch_k_smooth': 49, 'stoch_d_period': 46, 'leverage': 4},
    {'symbol': 'TUSDT', 'ma_period': 152, 'stoch_k_period': 137, 'stoch_k_smooth': 80, 'stoch_d_period': 38, 'leverage': 5},
    {'symbol': 'ONGUSDT', 'ma_period': 190, 'stoch_k_period': 146, 'stoch_k_smooth': 80, 'stoch_d_period': 50, 'leverage': 4},
    {'symbol': 'C98USDT', 'ma_period': 333, 'stoch_k_period': 133, 'stoch_k_smooth': 80, 'stoch_d_period': 16, 'leverage': 3},
    {'symbol': 'SUSHIUSDT', 'ma_period': 94, 'stoch_k_period': 107, 'stoch_k_smooth': 66, 'stoch_d_period': 27, 'leverage': 3},
    {'symbol': 'GLMUSDT', 'ma_period': 85, 'stoch_k_period': 69, 'stoch_k_smooth': 32, 'stoch_d_period': 13, 'leverage': 3},
    {'symbol': 'RVNUSDT', 'ma_period': 113, 'stoch_k_period': 121, 'stoch_k_smooth': 70, 'stoch_d_period': 34, 'leverage': 5},
    {'symbol': 'ROSEUSDT', 'ma_period': 149, 'stoch_k_period': 143, 'stoch_k_smooth': 69, 'stoch_d_period': 17, 'leverage': 4},
    {'symbol': 'TRUUSDT', 'ma_period': 271, 'stoch_k_period': 126, 'stoch_k_smooth': 61, 'stoch_d_period': 14, 'leverage': 4},
    {'symbol': 'CTSIUSDT', 'ma_period': 214, 'stoch_k_period': 137, 'stoch_k_smooth': 67, 'stoch_d_period': 36, 'leverage': 5},
    {'symbol': 'CFXUSDT', 'ma_period': 161, 'stoch_k_period': 86, 'stoch_k_smooth': 65, 'stoch_d_period': 27, 'leverage': 2},
    {'symbol': 'HOTUSDT', 'ma_period': 178, 'stoch_k_period': 118, 'stoch_k_smooth': 51, 'stoch_d_period': 48, 'leverage': 4},
    {'symbol': 'ENJUSDT', 'ma_period': 140, 'stoch_k_period': 150, 'stoch_k_smooth': 8, 'stoch_d_period': 40, 'leverage': 3},
    {'symbol': 'ANKRUSDT', 'ma_period': 283, 'stoch_k_period': 119, 'stoch_k_smooth': 62, 'stoch_d_period': 40, 'leverage': 5},
    {'symbol': 'GTCUSDT', 'ma_period': 196, 'stoch_k_period': 133, 'stoch_k_smooth': 28, 'stoch_d_period': 49, 'leverage': 3},
    {'symbol': 'ATAUSDT', 'ma_period': 62, 'stoch_k_period': 147, 'stoch_k_smooth': 43, 'stoch_d_period': 50, 'leverage': 4},
    {'symbol': 'SKLUSDT', 'ma_period': 120, 'stoch_k_period': 108, 'stoch_k_smooth': 80, 'stoch_d_period': 49, 'leverage': 3},
    {'symbol': 'XVGUSDT', 'ma_period': 70, 'stoch_k_period': 135, 'stoch_k_smooth': 32, 'stoch_d_period': 32, 'leverage': 3},
    {'symbol': '1000XECUSDT', 'ma_period': 206, 'stoch_k_period': 93, 'stoch_k_smooth': 23, 'stoch_d_period': 32, 'leverage': 3},
    {'symbol': '1000SHIBUSDT', 'ma_period': 118, 'stoch_k_period': 74, 'stoch_k_smooth': 40, 'stoch_d_period': 26, 'leverage': 3},
    {'symbol': 'TWTUSDT', 'ma_period': 32, 'stoch_k_period': 123, 'stoch_k_smooth': 73, 'stoch_d_period': 14, 'leverage': 4},
    {'symbol': '1INCHUSDT', 'ma_period': 123, 'stoch_k_period': 138, 'stoch_k_smooth': 39, 'stoch_d_period': 47, 'leverage': 3},
    {'symbol': 'XVSUSDT', 'ma_period': 65, 'stoch_k_period': 147, 'stoch_k_smooth': 54, 'stoch_d_period': 22, 'leverage': 3},
    {'symbol': 'CELRUSDT', 'ma_period': 268, 'stoch_k_period': 150, 'stoch_k_smooth': 32, 'stoch_d_period': 48, 'leverage': 3},
    {'symbol': 'VETUSDT', 'ma_period': 217, 'stoch_k_period': 112, 'stoch_k_smooth': 73, 'stoch_d_period': 34, 'leverage': 3},
    {'symbol': 'SNXUSDT', 'ma_period': 38, 'stoch_k_period': 128, 'stoch_k_smooth': 55, 'stoch_d_period': 20, 'leverage': 3},
    {'symbol': 'GMXUSDT', 'ma_period': 88, 'stoch_k_period': 99, 'stoch_k_smooth': 79, 'stoch_d_period': 39, 'leverage': 4},
    {'symbol': 'COMPUSDT', 'ma_period': 314, 'stoch_k_period': 131, 'stoch_k_smooth': 33, 'stoch_d_period': 11, 'leverage': 3},
    {'symbol': 'NMRUSDT', 'ma_period': 340, 'stoch_k_period': 141, 'stoch_k_smooth': 60, 'stoch_d_period': 45, 'leverage': 5},
    {'symbol': 'SUPERUSDT', 'ma_period': 77, 'stoch_k_period': 109, 'stoch_k_smooth': 42, 'stoch_d_period': 44, 'leverage': 1},
    {'symbol': 'COTIUSDT', 'ma_period': 231, 'stoch_k_period': 122, 'stoch_k_smooth': 36, 'stoch_d_period': 24, 'leverage': 3},
    {'symbol': 'ONTUSDT', 'ma_period': 113, 'stoch_k_period': 150, 'stoch_k_smooth': 60, 'stoch_d_period': 41, 'leverage': 4},
    {'symbol': 'GASUSDT', 'ma_period': 49, 'stoch_k_period': 136, 'stoch_k_smooth': 62, 'stoch_d_period': 5, 'leverage': 2},
    {'symbol': 'NEARUSDT', 'ma_period': 164, 'stoch_k_period': 118, 'stoch_k_smooth': 70, 'stoch_d_period': 26, 'leverage': 2},
    {'symbol': 'QNTUSDT', 'ma_period': 274, 'stoch_k_period': 145, 'stoch_k_smooth': 58, 'stoch_d_period': 31, 'leverage': 5},
    {'symbol': 'YFIUSDT', 'ma_period': 246, 'stoch_k_period': 148, 'stoch_k_smooth': 73, 'stoch_d_period': 44, 'leverage': 3},
    {'symbol': 'ENSUSDT', 'ma_period': 224, 'stoch_k_period': 130, 'stoch_k_smooth': 56, 'stoch_d_period': 23, 'leverage': 3},
    {'symbol': 'QTUMUSDT', 'ma_period': 233, 'stoch_k_period': 108, 'stoch_k_smooth': 50, 'stoch_d_period': 49, 'leverage': 3},
    {'symbol': 'ETCUSDT', 'ma_period': 339, 'stoch_k_period': 118, 'stoch_k_smooth': 56, 'stoch_d_period': 27, 'leverage': 4},
    {'symbol': 'TRBUSDT', 'ma_period': 167, 'stoch_k_period': 102, 'stoch_k_smooth': 53, 'stoch_d_period': 12, 'leverage': 3},
    {'symbol': 'ALGOUSDT', 'ma_period': 350, 'stoch_k_period': 148, 'stoch_k_smooth': 7, 'stoch_d_period': 31, 'leverage': 3},
    {'symbol': 'LQTYUSDT', 'ma_period': 177, 'stoch_k_period': 143, 'stoch_k_smooth': 57, 'stoch_d_period': 18, 'leverage': 3},
    {'symbol': 'ZILUSDT', 'ma_period': 286, 'stoch_k_period': 71, 'stoch_k_smooth': 5, 'stoch_d_period': 31, 'leverage': 2},
    {'symbol': 'ZRXUSDT', 'ma_period': 67, 'stoch_k_period': 110, 'stoch_k_smooth': 75, 'stoch_d_period': 44, 'leverage': 3},
    {'symbol': 'DOGEUSDT', 'ma_period': 250, 'stoch_k_period': 136, 'stoch_k_smooth': 20, 'stoch_d_period': 26, 'leverage': 3},
    {'symbol': 'GRTUSDT', 'ma_period': 94, 'stoch_k_period': 94, 'stoch_k_smooth': 34, 'stoch_d_period': 7, 'leverage': 2},
    {'symbol': 'ARKUSDT', 'ma_period': 341, 'stoch_k_period': 133, 'stoch_k_smooth': 65, 'stoch_d_period': 42, 'leverage': 2},
    {'symbol': 'IOTAUSDT', 'ma_period': 151, 'stoch_k_period': 128, 'stoch_k_smooth': 59, 'stoch_d_period': 47, 'leverage': 2},
    {'symbol': 'APTUSDT', 'ma_period': 42, 'stoch_k_period': 147, 'stoch_k_smooth': 66, 'stoch_d_period': 19, 'leverage': 2},
    {'symbol': 'THETAUSDT', 'ma_period': 96, 'stoch_k_period': 90, 'stoch_k_smooth': 68, 'stoch_d_period': 27, 'leverage': 2},
    {'symbol': 'KNCUSDT', 'ma_period': 72, 'stoch_k_period': 136, 'stoch_k_smooth': 28, 'stoch_d_period': 13, 'leverage': 2},
    {'symbol': 'KSMUSDT', 'ma_period': 156, 'stoch_k_period': 144, 'stoch_k_smooth': 58, 'stoch_d_period': 39, 'leverage': 2},
    {'symbol': 'DUSKUSDT', 'ma_period': 108, 'stoch_k_period': 109, 'stoch_k_smooth': 64, 'stoch_d_period': 39, 'leverage': 2},
    {'symbol': 'LRCUSDT', 'ma_period': 98, 'stoch_k_period': 101, 'stoch_k_smooth': 64, 'stoch_d_period': 44, 'leverage': 2},
    {'symbol': 'ATOMUSDT', 'ma_period': 47, 'stoch_k_period': 112, 'stoch_k_smooth': 66, 'stoch_d_period': 10, 'leverage': 2},
    {'symbol': 'ICPUSDT', 'ma_period': 42, 'stoch_k_period': 147, 'stoch_k_smooth': 72, 'stoch_d_period': 4, 'leverage': 2},
    {'symbol': 'XLMUSDT', 'ma_period': 325, 'stoch_k_period': 85, 'stoch_k_smooth': 75, 'stoch_d_period': 47, 'leverage': 4},
    {'symbol': 'NEOUSDT', 'ma_period': 349, 'stoch_k_period': 92, 'stoch_k_smooth': 52, 'stoch_d_period': 29, 'leverage': 2},
    {'symbol': 'BELUSDT', 'ma_period': 228, 'stoch_k_period': 146, 'stoch_k_smooth': 10, 'stoch_d_period': 13, 'leverage': 1},
    {'symbol': 'ARUSDT', 'ma_period': 144, 'stoch_k_period': 34, 'stoch_k_smooth': 24, 'stoch_d_period': 16, 'leverage': 1},
    {'symbol': 'AAVEUSDT', 'ma_period': 165, 'stoch_k_period': 106, 'stoch_k_smooth': 71, 'stoch_d_period': 40, 'leverage': 2},
    {'symbol': 'ALICEUSDT', 'ma_period': 94, 'stoch_k_period': 57, 'stoch_k_smooth': 7, 'stoch_d_period': 16, 'leverage': 1},
    {'symbol': 'BANUSDT', 'ma_period': 51, 'stoch_k_period': 108, 'stoch_k_smooth': 25, 'stoch_d_period': 8, 'leverage': 1},
    {'symbol': 'TAOUSDT', 'ma_period': 88, 'stoch_k_period': 122, 'stoch_k_smooth': 64, 'stoch_d_period': 12, 'leverage': 1},
    {'symbol': 'DENTUSDT', 'ma_period': 126, 'stoch_k_period': 142, 'stoch_k_smooth': 75, 'stoch_d_period': 24, 'leverage': 1},
    {'symbol': 'ZENUSDT', 'ma_period': 297, 'stoch_k_period': 117, 'stoch_k_smooth': 15, 'stoch_d_period': 12, 'leverage': 1},
    {'symbol': 'CHZUSDT', 'ma_period': 327, 'stoch_k_period': 122, 'stoch_k_smooth': 74, 'stoch_d_period': 39, 'leverage': 2},
    {'symbol': 'CHRUSDT', 'ma_period': 100, 'stoch_k_period': 149, 'stoch_k_smooth': 59, 'stoch_d_period': 40, 'leverage': 1},
    {'symbol': 'SSVUSDT', 'ma_period': 203, 'stoch_k_period': 139, 'stoch_k_smooth': 56, 'stoch_d_period': 38, 'leverage': 1},
    {'symbol': 'JASMYUSDT', 'ma_period': 40, 'stoch_k_period': 111, 'stoch_k_smooth': 14, 'stoch_d_period': 28, 'leverage': 1},
    {'symbol': '1000RATSUSDT', 'ma_period': 108, 'stoch_k_period': 124, 'stoch_k_smooth': 48, 'stoch_d_period': 49, 'leverage': 1},
    {'symbol': 'UMAUSDT', 'ma_period': 44, 'stoch_k_period': 148, 'stoch_k_smooth': 65, 'stoch_d_period': 26, 'leverage': 1},
    {'symbol': 'MASKUSDT', 'ma_period': 126, 'stoch_k_period': 141, 'stoch_k_smooth': 69, 'stoch_d_period': 13, 'leverage': 1},
    {'symbol': 'ACHUSDT', 'ma_period': 65, 'stoch_k_period': 99, 'stoch_k_smooth': 32, 'stoch_d_period': 42, 'leverage': 1},
    {'symbol': 'INJUSDT', 'ma_period': 41, 'stoch_k_period': 141, 'stoch_k_smooth': 48, 'stoch_d_period': 14, 'leverage': 1},
    {'symbol': 'STORJUSDT', 'ma_period': 114, 'stoch_k_period': 68, 'stoch_k_smooth': 34, 'stoch_d_period': 29, 'leverage': 1},
]

# Futures 매매 제외 코인 (84개 - 백테스트 기준 부적합)
# 제외 사유: CAGR <= 0%, MDD < -85%, Sharpe < 0.7, 과적합 의심
FUTURES_EXCLUDED_COINS = [
    '1000000MOGUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'ACXUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'ADAUSDT',  # Sharpe < 0.7
    'AIXBTUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'ANIMEUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'ARCUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'ARPAUSDT',  # CAGR <= 0%
    'AVAAIUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'AXSUSDT',  # MDD < -85%
    'BANDUSDT',  # Sharpe < 0.7
    'BATUSDT',  # Sharpe < 0.7
    'BCHUSDT',  # CAGR <= 0%
    'BIOUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'BNBUSDT',  # CAGR <= 0%
    'BTCDOMUSDT',  # Sharpe < 0.7
    'BTCUSDT',  # Sharpe < 0.7
    'CELOUSDT',  # MDD < -85%
    'CETUSUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'COSUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'CRVUSDT',  # Sharpe < 0.7
    'DASHUSDT',  # Sharpe < 0.7
    'DEGENUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'DEXEUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'DIAUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'DOTUSDT',  # MDD < -85%
    'DRIFTUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'DUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'DYDXUSDT',  # MDD < -85%
    'EGLDUSDT',  # MDD < -85%
    'EIGENUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'ETHUSDT',  # Sharpe < 0.7
    'FARTCOINUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'FILUSDT',  # MDD < -85%
    'GALAUSDT',  # MDD < -85%
    'GOATUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'GRIFFAINUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'HIPPOUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'HIVEUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'HMSTRUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'KAVAUSDT',  # Sharpe < 0.7
    'KMNOUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'LDOUSDT',  # MDD < -85%
    'LINKUSDT',  # Sharpe < 0.7
    'LISTAUSDT',  # MDD < -85%
    'LPTUSDT',  # MDD < -85%
    'LTCUSDT',  # Sharpe < 0.7
    'MELANIAUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'MEMEUSDT',  # MDD < -85%
    'MTLUSDT',  # Sharpe < 0.7
    'NKNUSDT',  # Sharpe < 0.7
    'OMUSDT',  # MDD < -85%
    'PEOPLEUSDT',  # CAGR <= 0%
    'PHAUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'PIPPINUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'PNUTUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'PROMUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'RAYSOLUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'RLCUSDT',  # Sharpe < 0.7
    'RSRUSDT',  # MDD < -85%
    'RUNEUSDT',  # MDD < -85%
    'SFPUSDT',  # CAGR <= 0%
    'SOLUSDT',  # Sharpe < 0.7
    'SOLVUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'SONICUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'SUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'SWARMSUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'THEUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'TLMUSDT',  # Sharpe < 0.7
    'TRUMPUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'TRXUSDT',  # CAGR <= 0%
    'UNIUSDT',  # Sharpe < 0.7
    'USDCUSDT',  # CAGR <= 0%
    'USUALUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'VANAUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'VELODROMEUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'VINEUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'VIRTUALUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'VTHOUSDT',  # Trades <= 2 with Days < 500 (과적합)
    'VVVUSDT',  # CAGR > 10000% with Trades < 10 (과적합)
    'XMRUSDT',  # CAGR <= 0%
    'XRPUSDT',  # Sharpe < 0.7
    'XTZUSDT',  # MDD < -85%
    'YGGUSDT',  # MDD < -85%
    'ZECUSDT',  # Sharpe < 0.7
]

# ============================================================
# 전역 변수
# ============================================================

stoch_cache = {}
stoch_cache_date = None
futures_stoch_cache = {}
futures_stoch_cache_date = None
spot_exchange = None
futures_exchange = None

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

def init_spot_exchange():
    global spot_exchange
    try:
        spot_exchange = ccxt.binance({
            'apiKey': BINANCE_API_KEY,
            'secret': BINANCE_SECRET_KEY,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        spot_exchange.load_markets()
        logging.info("✅ 바이낸스 Spot 거래소 연결 성공")
        return True
    except Exception as e:
        logging.error(f"❌ 바이낸스 Spot 거래소 연결 실패: {e}")
        return False


def init_futures_exchange():
    global futures_exchange
    try:
        futures_exchange = ccxt.binance({
            'apiKey': BINANCE_API_KEY,
            'secret': BINANCE_SECRET_KEY,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
                'adjustForTimeDifference': True
            }
        })
        futures_exchange.load_markets()

        # 마진 타입 설정 (CROSSED)
        logging.info("✅ 바이낸스 USDS-M Futures 거래소 연결 성공")
        return True
    except Exception as e:
        logging.error(f"❌ 바이낸스 Futures 거래소 연결 실패: {e}")
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


def send_trade_summary(spot_buy_list, spot_sell_list, excluded_sell_list,
                       futures_open_list, futures_close_list,
                       spot_total, spot_usdt, futures_total, futures_usdt,
                       bnb_info, errors):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    msg = f"📊 <b>바이낸스 거래 종합 리포트</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"

    # Spot 자산
    msg += f"<b>📈 Spot</b>\n"
    msg += f"💰 총 자산: <b>${spot_total:,.2f}</b>\n"
    msg += f"💵 USDT: ${spot_usdt:,.2f}\n"
    if bnb_info:
        msg += f"🔶 BNB: {bnb_info['balance']:.4f} (${bnb_info['value']:.2f})\n"

    # Futures 자산
    msg += f"\n<b>📉 USDS-M Futures</b>\n"
    msg += f"💰 총 자산: <b>${futures_total:,.2f}</b>\n"
    msg += f"💵 USDT: ${futures_usdt:,.2f}\n"

    msg += f"━━━━━━━━━━━━━━━\n"

    # Spot 매수
    if spot_buy_list:
        msg += f"🟢 <b>Spot 매수 {len(spot_buy_list)}건</b>\n"
        for item in spot_buy_list[:10]:
            msg += f"  • {item['symbol']}: ${item['amount']:.2f}\n"
        if len(spot_buy_list) > 10:
            msg += f"  ... 외 {len(spot_buy_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"

    # Spot 매도
    if spot_sell_list:
        msg += f"🔴 <b>Spot 매도 {len(spot_sell_list)}건</b>\n"
        for item in spot_sell_list[:10]:
            msg += f"  • {item['symbol']}: {item['reason']}\n"
        if len(spot_sell_list) > 10:
            msg += f"  ... 외 {len(spot_sell_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"

    # 부적합 코인 매도
    if excluded_sell_list:
        msg += f"🚫 <b>부적합 코인 매도 {len(excluded_sell_list)}건</b>\n"
        for item in excluded_sell_list[:10]:
            msg += f"  • {item['symbol']}: {item['reason']}\n"
        if len(excluded_sell_list) > 10:
            msg += f"  ... 외 {len(excluded_sell_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"

    # Futures 숏 진입
    if futures_open_list:
        msg += f"🔻 <b>Futures 숏 진입 {len(futures_open_list)}건</b>\n"
        for item in futures_open_list[:10]:
            msg += f"  • {item['symbol']}: ${item['notional']:.2f} ({item['leverage']}x)\n"
        if len(futures_open_list) > 10:
            msg += f"  ... 외 {len(futures_open_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"

    # Futures 숏 청산
    if futures_close_list:
        msg += f"🔺 <b>Futures 숏 청산 {len(futures_close_list)}건</b>\n"
        for item in futures_close_list[:10]:
            pnl_sign = "+" if item['pnl'] >= 0 else ""
            emoji = "💚" if item['pnl'] >= 0 else "❤️"
            msg += f"  • {item['symbol']}: {pnl_sign}{item['pnl']:.2f} {emoji}\n"
        if len(futures_close_list) > 10:
            msg += f"  ... 외 {len(futures_close_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"

    # 거래 없음
    if not spot_buy_list and not spot_sell_list and not excluded_sell_list and not futures_open_list and not futures_close_list:
        msg += f"ℹ️ 거래 없음\n━━━━━━━━━━━━━━━\n"

    # 에러
    if errors:
        msg += f"⚠️ <b>오류 {len(errors)}건</b>\n"
        for err in errors[:5]:
            msg += f"  • {err}\n"
        msg += f"━━━━━━━━━━━━━━━\n"

    msg += f"🕐 {now}"
    send_telegram(msg)


def send_start_alert():
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg = f"🚀 <b>바이낸스 자동매매 봇 시작 (v4.0.0)</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"📈 Spot: MA + 스토캐스틱 롱\n"
    msg += f"📉 Futures: MA + 스토캐스틱 숏\n"
    msg += f"💰 수수료: 0.075% (Spot), 0.06% (Futures)\n"
    msg += f"🔶 BNB 자동충전 (Spot/Futures)\n"
    msg += f"🪙 Spot 코인: {len(COINS)}개\n"
    msg += f"🔻 Futures 숏: {len(SHORT_TRADING_CONFIGS)}개\n"
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
# Spot 관련 함수들 (기존 유지)
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
        logging.info(f"Spot 스토캐스틱 캐시 로드 완료: 날짜={stoch_cache_date}, {len(stoch_cache)}개 코인")
        return True
    except Exception as e:
        logging.error(f"Spot 스토캐스틱 캐시 로드 중 오류: {e}")
        return False


def get_bnb_balance():
    """Spot 지갑 BNB 잔고 조회"""
    try:
        balance = spot_exchange.fetch_balance()
        bnb_amount = float(balance.get('BNB', {}).get('free', 0))
        if bnb_amount > 0:
            ticker = spot_exchange.fetch_ticker('BNB/USDT')
            bnb_price, bnb_value = ticker['last'], bnb_amount * ticker['last']
        else:
            bnb_value, bnb_price = 0, 0
        return {'balance': bnb_amount, 'price': bnb_price, 'value': bnb_value}
    except Exception as e:
        logging.error(f"Spot BNB 잔고 조회 중 오류: {e}")
        return {'balance': 0, 'price': 0, 'value': 0}


def check_and_recharge_bnb():
    """Spot 지갑 BNB 자동 충전"""
    try:
        bnb_info = get_bnb_balance()
        logging.info(f"🔶 Spot BNB 잔고: {bnb_info['balance']:.4f} BNB (${bnb_info['value']:.2f})")

        if bnb_info['value'] < BNB_MIN_BALANCE:
            logging.info(f"🔶 Spot BNB 잔고 부족, 충전 시작...")
            usdt_balance = get_usdt_balance()
            if usdt_balance < BNB_RECHARGE_AMOUNT:
                logging.warning(f"⚠️ USDT 잔고 부족으로 BNB 충전 불가")
                return None
            try:
                spot_exchange.create_market_buy_order('BNB/USDT', None, {'quoteOrderQty': BNB_RECHARGE_AMOUNT})
                time.sleep(1)
                new_bnb_info = get_bnb_balance()
                logging.info(f"✅ Spot BNB 충전 완료: {new_bnb_info['balance']:.4f} BNB")
                return {'action': 'recharged', 'new_balance': new_bnb_info['balance']}
            except Exception as e:
                logging.error(f"❌ Spot BNB 충전 실패: {e}")
                return None
        return {'action': 'sufficient', 'balance': bnb_info['balance']}
    except Exception as e:
        logging.error(f"Spot BNB 충전 확인 중 오류: {e}")
        return None


def get_usdt_balance():
    """Spot USDT 잔고 조회"""
    try:
        balance = spot_exchange.fetch_balance()
        return float(balance['USDT']['free'])
    except Exception as e:
        logging.error(f"Spot USDT 잔고 조회 중 오류: {e}")
        return 0


def get_total_asset():
    """Spot 총 자산 조회"""
    try:
        balance = spot_exchange.fetch_balance()
        total = 0.0
        for currency, amounts in balance['total'].items():
            if amounts > 0:
                if currency == 'USDT':
                    total += amounts
                else:
                    symbol = f"{currency}/USDT"
                    if symbol in spot_exchange.markets:
                        try:
                            ticker = spot_exchange.fetch_ticker(symbol)
                            total += amounts * ticker['last']
                            time.sleep(0.05)
                        except:
                            pass
        return total
    except Exception as e:
        logging.error(f"Spot 총 자산 계산 중 오류: {e}")
        return 0


def get_coin_balance(symbol):
    try:
        base = symbol.split('/')[0]
        balance = spot_exchange.fetch_balance()
        return float(balance.get(base, {}).get('free', 0))
    except Exception as e:
        logging.error(f"{symbol} 잔고 조회 중 오류: {e}")
        return 0


def get_all_holdings():
    """보유 중인 모든 Spot 코인 조회"""
    try:
        balance = spot_exchange.fetch_balance()
        holdings = {}
        for currency, amounts in balance['total'].items():
            if amounts > 0 and currency not in ['USDT', 'USD']:
                symbol = f"{currency}/USDT"
                if symbol in spot_exchange.markets:
                    try:
                        ticker = spot_exchange.fetch_ticker(symbol)
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
        balance = spot_exchange.fetch_balance()
        empty_count = 0
        for symbol in COINS:
            if symbol == 'BNB/USDT':
                continue
            base = symbol.split('/')[0]
            coin_balance = float(balance.get(base, {}).get('free', 0))
            if symbol in spot_exchange.markets:
                try:
                    ticker = spot_exchange.fetch_ticker(symbol)
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
        return len(COINS) - 1


def calculate_invest_amount():
    usdt_balance = get_usdt_balance()
    empty_slots = count_empty_slots()
    if empty_slots == 0:
        return 0

    available_usdt = usdt_balance * 0.995
    amount_by_available = available_usdt / empty_slots
    total_asset = get_total_asset()
    max_by_equity = total_asset / (len(COINS) - 1)
    invest_amount = min(amount_by_available, max_by_equity)

    if invest_amount < MIN_ORDER_USDT:
        return 0
    return invest_amount


def fetch_ohlcv_batch(symbol, timeframe, limit):
    for retry in range(3):
        try:
            ohlcv = spot_exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
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
        ticker = spot_exchange.fetch_ticker(symbol)
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
    logging.info("📊 Spot 스토캐스틱 데이터 전체 갱신 시작...")

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
    logging.info(f"📊 Spot 스토캐스틱 데이터 갱신 완료: {len(stoch_cache)}개 코인")


def get_stochastic_signal(symbol):
    if should_refresh_stoch_cache():
        refresh_all_stochastic()
    return stoch_cache.get(symbol)


def sell_excluded_coins():
    """투자 적합 코인 목록에 없는 보유 코인 매도 (BNB 제외)"""
    excluded_sell_list = []
    try:
        holdings = get_all_holdings()
        for symbol, info in holdings.items():
            if symbol == 'BNB/USDT':
                logging.info(f"🔶 {symbol} - BNB는 수수료용으로 매도 제외")
                continue

            if symbol not in COINS:
                try:
                    if info['value'] >= MIN_ORDER_USDT:
                        spot_exchange.create_market_sell_order(symbol, info['balance'])
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
# Futures 관련 함수들 (새로 추가)
# ============================================================

def save_futures_stoch_cache():
    """Futures 스토캐스틱 캐시 저장"""
    global futures_stoch_cache, futures_stoch_cache_date
    try:
        save_data = {
            'cache_date': futures_stoch_cache_date.isoformat() if futures_stoch_cache_date else None,
            'data': futures_stoch_cache
        }
        with open(FUTURES_STOCH_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"Futures 스토캐스틱 캐시 저장 중 오류: {e}")
        return False


def load_futures_stoch_cache():
    """Futures 스토캐스틱 캐시 로드"""
    global futures_stoch_cache, futures_stoch_cache_date
    try:
        if not os.path.exists(FUTURES_STOCH_CACHE_FILE):
            return False
        with open(FUTURES_STOCH_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cache_date_str = data.get('cache_date')
        if cache_date_str:
            futures_stoch_cache_date = datetime.fromisoformat(cache_date_str).date()
        futures_stoch_cache = data.get('data', {})
        logging.info(f"Futures 스토캐스틱 캐시 로드 완료: 날짜={futures_stoch_cache_date}, {len(futures_stoch_cache)}개 코인")
        return True
    except Exception as e:
        logging.error(f"Futures 스토캐스틱 캐시 로드 중 오류: {e}")
        return False


def get_futures_balance():
    """Futures 지갑 잔고 조회"""
    try:
        balance = futures_exchange.fetch_balance()
        usdt_total = float(balance.get('USDT', {}).get('total', 0))
        usdt_free = float(balance.get('USDT', {}).get('free', 0))
        return {'total': usdt_total, 'free': usdt_free}
    except Exception as e:
        logging.error(f"Futures 잔고 조회 중 오류: {e}")
        return {'total': 0, 'free': 0}


def get_futures_bnb_balance():
    """Futures 지갑 BNB 잔고 조회"""
    try:
        balance = futures_exchange.fetch_balance()
        bnb_total = float(balance.get('BNB', {}).get('total', 0))
        bnb_free = float(balance.get('BNB', {}).get('free', 0))

        if bnb_total > 0:
            # Futures에서 BNB 가격 조회
            ticker = futures_exchange.fetch_ticker('BNB/USDT')
            bnb_price = ticker['last']
            bnb_value = bnb_total * bnb_price
        else:
            bnb_price, bnb_value = 0, 0

        return {'balance': bnb_total, 'free': bnb_free, 'price': bnb_price, 'value': bnb_value}
    except Exception as e:
        logging.error(f"Futures BNB 잔고 조회 중 오류: {e}")
        return {'balance': 0, 'free': 0, 'price': 0, 'value': 0}


def check_and_recharge_futures_bnb():
    """Futures 지갑 BNB 자동 충전

    Futures에서 BNB를 직접 매수할 수 있는지 확인하고,
    가능하면 USDT로 BNB를 매수합니다.

    참고: Binance Futures에서 BNB를 직접 매수하려면
    BNB/USDT 선물 포지션을 열고 실물 정산을 받아야 하는데,
    이는 복잡하므로 Spot에서 매수 후 Transfer하는 방식을 권장합니다.

    여기서는 Spot 지갑에서 BNB를 매수한 후 Futures로 전송하는 방식을 사용합니다.
    """
    try:
        bnb_info = get_futures_bnb_balance()
        logging.info(f"🔶 Futures BNB 잔고: {bnb_info['balance']:.4f} BNB (${bnb_info['value']:.2f})")

        if bnb_info['value'] < FUTURES_BNB_MIN_BALANCE:
            logging.info(f"🔶 Futures BNB 잔고 부족, Spot에서 매수 후 전송 시작...")

            # 1. Spot에서 BNB 매수
            spot_usdt = get_usdt_balance()
            if spot_usdt < FUTURES_BNB_RECHARGE_AMOUNT:
                logging.warning(f"⚠️ Spot USDT 잔고 부족으로 Futures BNB 충전 불가")
                return None

            try:
                # Spot에서 BNB 매수
                spot_exchange.create_market_buy_order('BNB/USDT', None, {'quoteOrderQty': FUTURES_BNB_RECHARGE_AMOUNT})
                time.sleep(1)

                # 2. 매수한 BNB를 Futures로 전송
                spot_bnb_info = get_bnb_balance()
                transfer_amount = spot_bnb_info['balance'] * 0.99  # 약간의 여유

                if transfer_amount > 0.001:  # 최소 전송량
                    # Spot → USDS-M Futures 전송
                    # ccxt에서는 transfer 함수 사용
                    futures_exchange.transfer('BNB', transfer_amount, 'spot', 'future')
                    time.sleep(1)

                    new_futures_bnb = get_futures_bnb_balance()
                    logging.info(f"✅ Futures BNB 충전 완료: {new_futures_bnb['balance']:.4f} BNB")
                    return {'action': 'recharged', 'new_balance': new_futures_bnb['balance']}
                else:
                    logging.warning(f"⚠️ 전송할 BNB 수량이 너무 적음")
                    return None

            except Exception as e:
                logging.error(f"❌ Futures BNB 충전 실패: {e}")
                return None

        return {'action': 'sufficient', 'balance': bnb_info['balance']}
    except Exception as e:
        logging.error(f"Futures BNB 충전 확인 중 오류: {e}")
        return None


def fetch_futures_ohlcv(symbol, timeframe, limit):
    """Futures OHLCV 데이터 조회"""
    for retry in range(3):
        try:
            ohlcv = futures_exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            if retry < 2:
                time.sleep(1)
            else:
                logging.error(f"Futures {symbol} OHLCV 조회 실패: {e}")
                return None
    return None


def get_futures_ma_price(symbol, period):
    """Futures MA 가격 계산"""
    try:
        df = fetch_futures_ohlcv(symbol, '4h', period + 10)
        if df is None or len(df) < period:
            return None
        return float(df['close'].tail(period).mean())
    except Exception as e:
        logging.error(f"Futures {symbol} MA 계산 중 오류: {e}")
        return None


def get_futures_current_price(symbol):
    """Futures 현재가 조회"""
    try:
        ticker = futures_exchange.fetch_ticker(symbol)
        return float(ticker['last'])
    except Exception as e:
        logging.error(f"Futures {symbol} 현재가 조회 중 오류: {e}")
        return None


def should_refresh_futures_stoch_cache():
    """Futures 스토캐스틱 캐시 갱신 필요 여부"""
    global futures_stoch_cache_date
    now_utc = datetime.now(timezone.utc)
    if futures_stoch_cache_date is None or futures_stoch_cache_date < now_utc.date():
        return True
    return False


def refresh_futures_stochastic():
    """Futures 스토캐스틱 데이터 갱신"""
    global futures_stoch_cache, futures_stoch_cache_date
    logging.info("📊 Futures 스토캐스틱 데이터 전체 갱신 시작...")

    for config in SHORT_TRADING_CONFIGS:
        symbol = config['symbol']
        try:
            k_period = config['stoch_k_period']
            k_smooth = config['stoch_k_smooth']
            d_period = config['stoch_d_period']
            required_count = k_period + k_smooth + d_period + 20

            df = fetch_futures_ohlcv(symbol, '1d', required_count)
            if df is None:
                continue

            slow_k, slow_d = calculate_stochastic(df, k_period, k_smooth, d_period)
            if slow_k is not None and slow_d is not None:
                # 숏 전략: K < D 일 때 숏 진입
                futures_stoch_cache[symbol] = {
                    'short_signal': bool(slow_k < slow_d),  # K < D = 하락 추세 = 숏 진입
                    'slow_k': slow_k,
                    'slow_d': slow_d
                }
            time.sleep(0.1)
        except Exception as e:
            logging.error(f"Futures {symbol} 스토캐스틱 계산 중 오류: {e}")

    futures_stoch_cache_date = datetime.now(timezone.utc).date()
    save_futures_stoch_cache()
    logging.info(f"📊 Futures 스토캐스틱 데이터 갱신 완료: {len(futures_stoch_cache)}개 코인")


def get_futures_stochastic_signal(symbol):
    """Futures 스토캐스틱 신호 조회"""
    if should_refresh_futures_stoch_cache():
        refresh_futures_stochastic()
    return futures_stoch_cache.get(symbol)


def get_futures_position(symbol):
    """Futures 포지션 조회"""
    try:
        positions = futures_exchange.fetch_positions([symbol])
        for pos in positions:
            if pos['symbol'] == symbol and abs(float(pos.get('contracts', 0))) > 0:
                return {
                    'symbol': symbol,
                    'side': pos['side'],  # 'short' or 'long'
                    'contracts': abs(float(pos['contracts'])),
                    'notional': abs(float(pos.get('notional', 0))),
                    'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                    'entry_price': float(pos.get('entryPrice', 0)),
                    'leverage': int(pos.get('leverage', 1)),
                    'liquidation_price': float(pos.get('liquidationPrice', 0))
                }
        return None
    except Exception as e:
        logging.error(f"Futures {symbol} 포지션 조회 중 오류: {e}")
        return None


def get_all_futures_positions():
    """모든 Futures 포지션 조회"""
    try:
        positions = futures_exchange.fetch_positions()
        active_positions = []
        for pos in positions:
            if abs(float(pos.get('contracts', 0))) > 0:
                active_positions.append({
                    'symbol': pos['symbol'],
                    'side': pos['side'],
                    'contracts': abs(float(pos['contracts'])),
                    'notional': abs(float(pos.get('notional', 0))),
                    'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                    'entry_price': float(pos.get('entryPrice', 0)),
                    'leverage': int(pos.get('leverage', 1))
                })
        return active_positions
    except Exception as e:
        logging.error(f"Futures 전체 포지션 조회 중 오류: {e}")
        return []


def set_futures_leverage(symbol, leverage):
    """Futures 레버리지 설정"""
    try:
        futures_exchange.set_leverage(leverage, symbol)
        logging.info(f"✅ {symbol} 레버리지 설정: {leverage}x")
        return True
    except Exception as e:
        logging.error(f"❌ {symbol} 레버리지 설정 실패: {e}")
        return False


def set_futures_margin_type(symbol, margin_type='CROSSED'):
    """Futures 마진 타입 설정 (CROSSED or ISOLATED)"""
    try:
        futures_exchange.set_margin_mode(margin_type.lower(), symbol)
        logging.info(f"✅ {symbol} 마진 타입 설정: {margin_type}")
        return True
    except Exception as e:
        # 이미 설정된 경우 에러 무시
        if 'No need to change margin type' in str(e):
            return True
        logging.error(f"❌ {symbol} 마진 타입 설정 실패: {e}")
        return False


def calculate_futures_position_size(config, usdt_amount, current_price):
    """Futures 포지션 사이즈 계산"""
    leverage = config['leverage']

    # 레버리지 적용한 명목 가치
    notional_value = usdt_amount * leverage

    # 수량 계산 (소수점 처리)
    quantity = notional_value / current_price

    # 심볼별 최소 수량 및 소수점 처리
    symbol = config['symbol']

    # 바이낸스 Futures 최소 수량
    min_quantities = {
        'BTCUSDT': 0.001,
        'ETHUSDT': 0.001,
        'BNBUSDT': 0.01,
        'SOLUSDT': 0.1,
        'XRPUSDT': 1,
        'DOGEUSDT': 1,
        'ADAUSDT': 1,
    }

    min_qty = min_quantities.get(symbol, 0.001)

    # 소수점 자릿수
    decimals = {
        'BTCUSDT': 3,
        'ETHUSDT': 3,
        'BNBUSDT': 2,
        'SOLUSDT': 1,
        'XRPUSDT': 0,
        'DOGEUSDT': 0,
        'ADAUSDT': 0,
    }

    decimal_places = decimals.get(symbol, 3)
    quantity = round(quantity, decimal_places)

    if quantity < min_qty:
        return 0

    return quantity


def count_futures_empty_slots():
    """Futures 포지션이 없는 슬롯 수 계산"""
    active_symbols = set()
    positions = get_all_futures_positions()
    for pos in positions:
        active_symbols.add(pos['symbol'])

    empty_count = 0
    for config in SHORT_TRADING_CONFIGS:
        if config['symbol'] not in active_symbols:
            empty_count += 1

    return empty_count


def get_futures_position_status():
    """
    각 심볼별 포지션 보유 여부 확인
    Returns: {'BTCUSDT': True, 'ETHUSDT': False, ...}
    """
    status = {}
    positions = get_all_futures_positions()
    active_symbols = set(pos['symbol'] for pos in positions)

    for config in SHORT_TRADING_CONFIGS:
        status[config['symbol']] = config['symbol'] in active_symbols

    return status


def calculate_futures_invest_amount_for_symbol(symbol):
    """
    특정 심볼에 대한 Futures 투자 금액 계산
    - 해당 심볼에 이미 포지션이 있으면 0 반환 (중복 진입 방지)
    - bitget_bot-8.py의 calculate_invest_amount_for_symbol 로직 참고
    """
    balance = get_futures_balance()
    usdt_free = balance['free']

    # 포지션 상태 확인
    position_status = get_futures_position_status()

    # 해당 심볼에 이미 포지션이 있으면 0 반환
    if position_status.get(symbol, False):
        logging.info(f"[{symbol}] 이미 포지션 보유 중 - 추가 진입 불가")
        return 0

    # 빈 슬롯(포지션 없는 코인) 수 계산
    empty_count = sum(1 for has_pos in position_status.values() if not has_pos)

    if empty_count == 0:
        logging.info(f"[{symbol}] 모든 슬롯에 포지션 보유 중")
        return 0

    # 가용 잔고의 99.5% 사용 (수수료 여유)
    available = usdt_free * 0.995

    # 빈 슬롯에 균등 배분
    invest_per_slot = available / empty_count

    # 최대 투자금 제한: 총 자산 / 전체 코인 수
    total_equity = balance['total']
    max_per_slot = total_equity / len(SHORT_TRADING_CONFIGS) if SHORT_TRADING_CONFIGS else 0

    invest_amount = min(invest_per_slot, max_per_slot)

    if invest_amount < FUTURES_MIN_ORDER_USDT:
        return 0

    logging.info(f"[{symbol}] 투자금 계산: 가용잔고 ${usdt_free:.2f}, 빈슬롯 {empty_count}개, 배분금액 ${invest_amount:.2f}")

    return invest_amount


def calculate_futures_invest_amount():
    """Futures 개별 포지션 투자 금액 계산 (레거시 - 하위 호환용)"""
    balance = get_futures_balance()
    usdt_free = balance['free']

    empty_slots = count_futures_empty_slots()
    if empty_slots == 0:
        return 0

    # 가용 잔고의 99.5% 사용 (수수료 여유)
    available = usdt_free * 0.995

    # 빈 슬롯에 균등 배분
    invest_per_slot = available / empty_slots

    # 최대 투자금 제한: 총 자산 / 전체 코인 수
    total_equity = balance['total']
    max_per_slot = total_equity / len(SHORT_TRADING_CONFIGS) if SHORT_TRADING_CONFIGS else 0

    invest_amount = min(invest_per_slot, max_per_slot)

    if invest_amount < FUTURES_MIN_ORDER_USDT:
        return 0

    return invest_amount


def open_short_position(config):
    """숏 포지션 진입"""
    symbol = config['symbol']
    leverage = config['leverage']

    try:
        # 투자 금액 계산 (포지션 중복 체크 포함)
        # calculate_futures_invest_amount_for_symbol은 해당 심볼에 이미 포지션이 있으면 0을 반환
        invest_amount = calculate_futures_invest_amount_for_symbol(symbol)
        if invest_amount <= 0:
            # 이미 포지션이 있거나 투자 금액이 없는 경우
            return None

        if invest_amount < FUTURES_MIN_ORDER_USDT:
            logging.warning(f"[{symbol}] 투자 금액 부족: ${invest_amount:.2f}")
            return None

        # 현재가 조회
        current_price = get_futures_current_price(symbol)
        if not current_price:
            logging.error(f"[{symbol}] 현재가 조회 실패")
            return None

        # 마진 타입 설정 (CROSSED)
        set_futures_margin_type(symbol, 'CROSSED')

        # 레버리지 설정
        set_futures_leverage(symbol, leverage)

        # 수량 계산
        quantity = calculate_futures_position_size(config, invest_amount, current_price)
        if quantity <= 0:
            logging.warning(f"[{symbol}] 수량 계산 실패")
            return None

        # 시장가 숏 주문
        order = futures_exchange.create_market_sell_order(symbol, quantity)

        logging.info(f"🔻 [{symbol}] 숏 진입 완료: {quantity} @ ~${current_price:.2f} ({leverage}x)")

        return {
            'symbol': symbol,
            'side': 'short',
            'quantity': quantity,
            'price': current_price,
            'notional': quantity * current_price,
            'leverage': leverage
        }

    except Exception as e:
        logging.error(f"❌ [{symbol}] 숏 진입 실패: {e}")
        return None


def close_short_position(symbol, reason=""):
    """숏 포지션 청산"""
    try:
        pos = get_futures_position(symbol)
        if not pos or pos['side'] != 'short':
            logging.info(f"[{symbol}] 청산할 숏 포지션 없음")
            return None

        quantity = pos['contracts']
        entry_price = pos['entry_price']
        unrealized_pnl = pos['unrealized_pnl']

        # 시장가 매수로 숏 청산
        order = futures_exchange.create_market_buy_order(symbol, quantity)

        current_price = get_futures_current_price(symbol)

        reason_str = f" ({reason})" if reason else ""
        pnl_str = f"+{unrealized_pnl:.2f}" if unrealized_pnl >= 0 else f"{unrealized_pnl:.2f}"

        logging.info(f"🔺 [{symbol}] 숏 청산 완료{reason_str}: {quantity} @ ~${current_price:.2f} (PnL: {pnl_str})")

        return {
            'symbol': symbol,
            'quantity': quantity,
            'entry_price': entry_price,
            'exit_price': current_price,
            'pnl': unrealized_pnl,
            'reason': reason
        }

    except Exception as e:
        logging.error(f"❌ [{symbol}] 숏 청산 실패: {e}")
        return None


# ============================================================
# 메인 거래 전략
# ============================================================

def spot_trade_strategy():
    """Spot 거래 전략 (기존과 동일)"""
    global spot_exchange
    buy_list, sell_list, excluded_sell_list, errors = [], [], [], []

    try:
        if spot_exchange is None:
            logging.info("📡 Spot 거래소 재초기화 시도...")
            if not init_spot_exchange():
                logging.warning("⚠️ Spot 거래소 초기화 실패")
                return buy_list, sell_list, excluded_sell_list, errors

        logging.info("✅ Spot API 연결 정상")

        # BNB 자동 충전
        check_and_recharge_bnb()

        # 부적합 코인 먼저 매도
        logging.info("🚫 Spot 부적합 코인 매도 확인 중...")
        excluded_sell_list = sell_excluded_coins()

        usdt_balance = get_usdt_balance()
        total_asset = get_total_asset()

        logging.info("=" * 80)
        logging.info(f"📊 Spot 거래 - 총자산: ${total_asset:,.2f}, USDT: ${usdt_balance:,.2f}")
        logging.info("=" * 80)

        for symbol in COINS:
            if symbol == 'BNB/USDT':
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
                                spot_exchange.create_market_buy_order(symbol, None, {'quoteOrderQty': invest_amount})
                                buy_list.append({'symbol': symbol, 'amount': invest_amount})
                                logging.info(f"🟢 Spot {symbol} 매수 완료: ${invest_amount:.2f}")
                                time.sleep(0.2)
                            except Exception as e:
                                errors.append(f"Spot {symbol} 매수 실패: {e}")
                else:
                    if coin_value >= MIN_ORDER_USDT:
                        try:
                            spot_exchange.create_market_sell_order(symbol, coin_balance)
                            sell_reason = "MA 조건 미충족" if not ma_condition else "스토캐스틱 조건 미충족"
                            sell_list.append({'symbol': symbol, 'reason': sell_reason})
                            logging.info(f"🔴 Spot {symbol} 매도 완료 ({sell_reason})")
                            time.sleep(0.2)
                        except Exception as e:
                            errors.append(f"Spot {symbol} 매도 실패: {e}")

            except Exception as e:
                errors.append(f"Spot {symbol} 처리 중 오류: {e}")

    except Exception as e:
        logging.error(f"Spot 전략 실행 중 오류: {e}")
        errors.append(f"Spot 전략 오류: {e}")

    return buy_list, sell_list, excluded_sell_list, errors


def futures_trade_strategy():
    """Futures 숏 거래 전략"""
    global futures_exchange
    open_list, close_list, errors = [], [], []

    if not SHORT_TRADING_CONFIGS:
        logging.info("📉 Futures 숏 설정이 없습니다. 백테스트 결과를 추가하세요.")
        return open_list, close_list, errors

    try:
        if futures_exchange is None:
            logging.info("📡 Futures 거래소 재초기화 시도...")
            if not init_futures_exchange():
                logging.warning("⚠️ Futures 거래소 초기화 실패")
                return open_list, close_list, errors

        logging.info("✅ Futures API 연결 정상")

        # Futures BNB 자동 충전
        check_and_recharge_futures_bnb()

        balance = get_futures_balance()
        logging.info("=" * 80)
        logging.info(f"📉 Futures 숏 거래 - 총자산: ${balance['total']:,.2f}, 가용: ${balance['free']:,.2f}")
        logging.info("=" * 80)

        for config in SHORT_TRADING_CONFIGS:
            symbol = config['symbol']

            # 제외 코인 체크
            if symbol in FUTURES_EXCLUDED_COINS:
                continue

            try:
                time.sleep(0.15)

                ma_period = config['ma_period']
                ma_price = get_futures_ma_price(symbol, ma_period)
                current_price = get_futures_current_price(symbol)
                stoch_data = get_futures_stochastic_signal(symbol)

                if ma_price is None or current_price is None:
                    continue

                # 현재 포지션 확인
                pos = get_futures_position(symbol)
                has_short = pos and pos['side'] == 'short'

                # 숏 진입 조건: 가격 < MA AND K < D (하락 추세)
                ma_condition = current_price < ma_price
                stoch_condition = stoch_data['short_signal'] if stoch_data and stoch_data.get('short_signal') is not None else False

                final_short_condition = ma_condition and stoch_condition

                logging.info(f"[{symbol}] 현재가: ${current_price:.4f}, MA{ma_period}: ${ma_price:.4f}")
                if stoch_data:
                    logging.info(f"[{symbol}] Stoch K: {stoch_data['slow_k']:.2f}, D: {stoch_data['slow_d']:.2f}")
                logging.info(f"[{symbol}] 조건: MA({ma_condition}) AND Stoch({stoch_condition}) = {final_short_condition}")

                if final_short_condition:
                    # 숏 진입 또는 유지
                    if not has_short:
                        result = open_short_position(config)
                        if result:
                            open_list.append(result)
                    else:
                        logging.info(f"[{symbol}] ➡️ 숏 포지션 유지")
                else:
                    # 조건 미충족 시 청산
                    if has_short:
                        reason = "MA 조건 미충족" if not ma_condition else "스토캐스틱 조건 미충족"
                        result = close_short_position(symbol, reason)
                        if result:
                            close_list.append(result)
                    else:
                        logging.info(f"[{symbol}] ➡️ 현금 유지 (숏 조건 미충족)")

            except Exception as e:
                errors.append(f"Futures {symbol} 처리 중 오류: {e}")
                logging.error(f"Futures {symbol} 처리 중 오류: {e}")

    except Exception as e:
        logging.error(f"Futures 전략 실행 중 오류: {e}")
        errors.append(f"Futures 전략 오류: {e}")

    return open_list, close_list, errors


def trade_strategy():
    """통합 거래 전략"""
    logging.info("\n" + "=" * 80)
    logging.info("📊 거래 전략 실행 시작")
    logging.info("=" * 80)

    # Spot 전략 실행
    spot_buy_list, spot_sell_list, excluded_sell_list, spot_errors = spot_trade_strategy()

    # Futures 전략 실행
    futures_open_list, futures_close_list, futures_errors = futures_trade_strategy()

    # 결과 수집
    all_errors = spot_errors + futures_errors

    # 최종 자산 조회
    spot_total = get_total_asset()
    spot_usdt = get_usdt_balance()
    futures_balance = get_futures_balance()
    futures_total = futures_balance['total']
    futures_usdt = futures_balance['free']
    bnb_info = get_bnb_balance()

    # 텔레그램 알림
    send_trade_summary(
        spot_buy_list, spot_sell_list, excluded_sell_list,
        futures_open_list, futures_close_list,
        spot_total, spot_usdt, futures_total, futures_usdt,
        bnb_info, all_errors
    )

    logging.info("=" * 80)
    logging.info(f"📊 완료 - Spot 매수: {len(spot_buy_list)}건 / 매도: {len(spot_sell_list)}건")
    logging.info(f"📊 완료 - Futures 숏 진입: {len(futures_open_list)}건 / 청산: {len(futures_close_list)}건")
    logging.info("=" * 80)


def log_strategy_info():
    logging.info("=" * 80)
    logging.info("🤖 바이낸스 자동매매 봇 v4.0.0 (Spot + USDS-M Futures)")
    logging.info("=" * 80)
    logging.info("📈 Spot 매수: 현재가 > MA(4H) AND Slow %K > Slow %D (1D)")
    logging.info("📈 Spot 매도: 매수 조건 미충족 OR 부적합 코인 (BNB 제외)")
    logging.info("📉 Futures 숏: 현재가 < MA(4H) AND Slow %K < Slow %D (1D)")
    logging.info("📉 Futures 청산: 숏 조건 미충족")
    logging.info(f"🪙 Spot 거래 대상: {len(COINS)}개 코인")
    logging.info(f"🔻 Futures 숏 대상: {len(SHORT_TRADING_CONFIGS)}개 코인")
    logging.info(f"🔶 Spot BNB 자동충전: ${BNB_MIN_BALANCE} 이하시 ${BNB_RECHARGE_AMOUNT} 매수")
    logging.info(f"🔶 Futures BNB 자동충전: ${FUTURES_BNB_MIN_BALANCE} 이하시 ${FUTURES_BNB_RECHARGE_AMOUNT} 매수")
    logging.info("=" * 80)


def main():
    global BOT_START_TIME
    BOT_START_TIME = datetime.now()

    setup_shutdown_handlers()

    # 거래소 초기화
    if not init_spot_exchange():
        logging.warning("⚠️ Spot 거래소 초기화 실패")

    if not init_futures_exchange():
        logging.warning("⚠️ Futures 거래소 초기화 실패")

    # 캐시 로드
    load_stoch_cache()
    load_futures_stoch_cache()

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

    # 시작 시 즉시 실행
    if spot_exchange is not None or futures_exchange is not None:
        logging.info("🚀 시작 시 전략 즉시 실행...")
        trade_strategy()

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
