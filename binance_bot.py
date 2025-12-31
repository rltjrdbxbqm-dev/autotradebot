"""
================================================================================
바이낸스 자동매매 봇 v1.1.0 (파인튜닝 파라미터 적용)
================================================================================
- MA + 스토캐스틱 + 역방향 전략
- 파인튜닝된 135개 코인 대상 (BNB 제외)
- BNB 자동 충전 기능 (수수료 할인용)
- 수수료: 0.075% (BNB 할인 적용)
- 4시간봉 기준 매매 (UTC 00:00, 04:00, 08:00, 12:00, 16:00, 20:00)
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

STATUS_FILE = os.path.join(os.path.expanduser('~'), 'binance_trading_status.json')
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
# 거래 대상 코인 (파인튜닝된 135개, BNB 제외)
# ============================================================

COINS = [
    'TURBO/USDT', 'NEIRO/USDT', 'FLOKI/USDT', 'BONK/USDT', 'PNUT/USDT',
    'ENA/USDT', 'PEPE/USDT', 'SUI/USDT', 'COW/USDT', 'SAND/USDT',
    'TAO/USDT', 'BNSOL/USDT', 'ZK/USDT', 'SOL/USDT', 'FET/USDT',
    'ARKM/USDT', 'GAS/USDT', 'PENDLE/USDT', 'WIF/USDT', 'EIGEN/USDT',
    'ONE/USDT', 'CHR/USDT', 'RAY/USDT', 'MANA/USDT', 'ETHFI/USDT',
    'HBAR/USDT', 'JASMY/USDT', 'SHIB/USDT', 'BANANA/USDT', 'CHZ/USDT',
    'DENT/USDT', 'CETUS/USDT', 'CFX/USDT', 'GALA/USDT', 'HIVE/USDT',
    '1MBABYDOGE/USDT', 'NFP/USDT', 'RENDER/USDT', 'OP/USDT', 'FLUX/USDT',
    'SCR/USDT', 'GLM/USDT', 'ADA/USDT', 'AMP/USDT', 'ANKR/USDT',
    'CVX/USDT', 'PHB/USDT', 'VIC/USDT', 'CVC/USDT', 'ARB/USDT',
    'STORJ/USDT', 'ACH/USDT', 'BB/USDT', 'AI/USDT', 'BLUR/USDT',
    'BOME/USDT', 'OSMO/USDT', 'LISTA/USDT', 'CELR/USDT', 'AVAX/USDT',
    'MAGIC/USDT', 'COTI/USDT', 'IMX/USDT', 'AXS/USDT', 'USTC/USDT',
    'RUNE/USDT', 'LUNC/USDT', 'OG/USDT', 'ZEC/USDT', 'ENJ/USDT',
    'VTHO/USDT', 'WBETH/USDT', 'METIS/USDT', 'ENS/USDT', 'AR/USDT',
    'JOE/USDT', 'ID/USDT', 'LUMIA/USDT', 'POL/USDT', 'SSV/USDT',
    'RVN/USDT', '1000CAT/USDT', 'STX/USDT', 'KAIA/USDT', 'DEXE/USDT',
    'POLYX/USDT', 'IQ/USDT', 'MDT/USDT', 'SEI/USDT', 'CYBER/USDT',
    'HOT/USDT', 'WLD/USDT', 'TRU/USDT', 'SNX/USDT', 'ZIL/USDT',
    'MOVR/USDT', 'BNT/USDT', 'SUN/USDT', 'SYN/USDT', 'QKC/USDT',
    'XAI/USDT', 'MEME/USDT', 'LDO/USDT', 'IOST/USDT', 'LPT/USDT',
    'PROM/USDT', 'XTZ/USDT', 'OM/USDT', 'ASTR/USDT', 'CKB/USDT',
    'ORDI/USDT', 'LINK/USDT', 'KAVA/USDT', 'DOT/USDT', 'QNT/USDT',
    'LQTY/USDT', 'TWT/USDT', 'XVG/USDT', 'WBTC/USDT', 'XLM/USDT',
    'KSM/USDT', 'CAKE/USDT', 'EGLD/USDT', '1INCH/USDT', 'RPL/USDT',
    'SUSHI/USDT', 'VANA/USDT', 'ALGO/USDT', 'T/USDT', 'VANRY/USDT',
    'GNS/USDT', 'ETH/USDT', 'PIXEL/USDT', 'FIL/USDT', 'ETC/USDT',
]

# ============================================================
# MA 기간 (4시간봉 기준) - 파인튜닝 결과
# ============================================================

MA_PERIODS = {
    'TURBO/USDT': 238, 'NEIRO/USDT': 152, 'FLOKI/USDT': 56, 'BONK/USDT': 122,
    'PNUT/USDT': 90, 'ENA/USDT': 268, 'PEPE/USDT': 170, 'SUI/USDT': 298,
    'COW/USDT': 218, 'SAND/USDT': 218, 'TAO/USDT': 192, 'BNSOL/USDT': 124,
    'ZK/USDT': 56, 'SOL/USDT': 258, 'FET/USDT': 230, 'ARKM/USDT': 200,
    'GAS/USDT': 54, 'PENDLE/USDT': 254, 'WIF/USDT': 152, 'EIGEN/USDT': 78,
    'ONE/USDT': 108, 'CHR/USDT': 278, 'RAY/USDT': 246, 'MANA/USDT': 84,
    'ETHFI/USDT': 60, 'HBAR/USDT': 104, 'JASMY/USDT': 138, 'SHIB/USDT': 110,
    'BANANA/USDT': 50, 'CHZ/USDT': 106, 'DENT/USDT': 256, 'CETUS/USDT': 242,
    'CFX/USDT': 146, 'GALA/USDT': 212, 'HIVE/USDT': 58, '1MBABYDOGE/USDT': 138,
    'NFP/USDT': 52, 'RENDER/USDT': 256, 'OP/USDT': 52, 'FLUX/USDT': 222,
    'SCR/USDT': 104, 'GLM/USDT': 288, 'ADA/USDT': 128, 'AMP/USDT': 224,
    'ANKR/USDT': 204, 'CVX/USDT': 122, 'PHB/USDT': 226, 'VIC/USDT': 246,
    'CVC/USDT': 94, 'ARB/USDT': 104, 'STORJ/USDT': 110, 'ACH/USDT': 178,
    'BB/USDT': 62, 'AI/USDT': 50, 'BLUR/USDT': 52, 'BOME/USDT': 58,
    'OSMO/USDT': 236, 'LISTA/USDT': 192, 'CELR/USDT': 272, 'AVAX/USDT': 288,
    'MAGIC/USDT': 86, 'COTI/USDT': 94, 'IMX/USDT': 126, 'AXS/USDT': 136,
    'USTC/USDT': 50, 'RUNE/USDT': 130, 'LUNC/USDT': 70, 'OG/USDT': 90,
    'ZEC/USDT': 142, 'ENJ/USDT': 236, 'VTHO/USDT': 242, 'WBETH/USDT': 130,
    'METIS/USDT': 54, 'ENS/USDT': 100, 'AR/USDT': 156, 'JOE/USDT': 128,
    'ID/USDT': 294, 'LUMIA/USDT': 262, 'POL/USDT': 74, 'SSV/USDT': 74,
    'RVN/USDT': 58, '1000CAT/USDT': 222, 'STX/USDT': 296, 'KAIA/USDT': 284,
    'DEXE/USDT': 60, 'POLYX/USDT': 58, 'IQ/USDT': 86, 'MDT/USDT': 248,
    'SEI/USDT': 62, 'CYBER/USDT': 64, 'HOT/USDT': 138, 'WLD/USDT': 284,
    'TRU/USDT': 198, 'SNX/USDT': 108, 'ZIL/USDT': 228, 'MOVR/USDT': 150,
    'BNT/USDT': 102, 'SUN/USDT': 262, 'SYN/USDT': 64, 'QKC/USDT': 246,
    'XAI/USDT': 70, 'MEME/USDT': 60, 'LDO/USDT': 152, 'IOST/USDT': 74,
    'LPT/USDT': 162, 'PROM/USDT': 230, 'XTZ/USDT': 72, 'OM/USDT': 74,
    'ASTR/USDT': 198, 'CKB/USDT': 168, 'ORDI/USDT': 54, 'LINK/USDT': 52,
    'KAVA/USDT': 82, 'DOT/USDT': 190, 'QNT/USDT': 116, 'LQTY/USDT': 226,
    'TWT/USDT': 170, 'XVG/USDT': 60, 'WBTC/USDT': 100, 'XLM/USDT': 52,
    'KSM/USDT': 156, 'CAKE/USDT': 60, 'EGLD/USDT': 284, '1INCH/USDT': 170,
    'RPL/USDT': 54, 'SUSHI/USDT': 72, 'VANA/USDT': 130, 'ALGO/USDT': 236,
    'T/USDT': 66, 'VANRY/USDT': 146, 'GNS/USDT': 152, 'ETH/USDT': 142,
    'PIXEL/USDT': 58, 'FIL/USDT': 242, 'ETC/USDT': 50,
}

# ============================================================
# 스토캐스틱 파라미터 (1일봉 기준) - 파인튜닝 결과
# ============================================================

STOCH_PARAMS = {
    'TURBO/USDT': {'k_period': 155, 'k_smooth': 46, 'd_period': 5},
    'NEIRO/USDT': {'k_period': 160, 'k_smooth': 32, 'd_period': 15},
    'FLOKI/USDT': {'k_period': 145, 'k_smooth': 32, 'd_period': 27},
    'BONK/USDT': {'k_period': 155, 'k_smooth': 54, 'd_period': 14},
    'PNUT/USDT': {'k_period': 85, 'k_smooth': 52, 'd_period': 23},
    'ENA/USDT': {'k_period': 155, 'k_smooth': 22, 'd_period': 16},
    'PEPE/USDT': {'k_period': 150, 'k_smooth': 20, 'd_period': 5},
    'SUI/USDT': {'k_period': 160, 'k_smooth': 34, 'd_period': 7},
    'COW/USDT': {'k_period': 120, 'k_smooth': 24, 'd_period': 12},
    'SAND/USDT': {'k_period': 125, 'k_smooth': 30, 'd_period': 6},
    'TAO/USDT': {'k_period': 80, 'k_smooth': 28, 'd_period': 8},
    'BNSOL/USDT': {'k_period': 125, 'k_smooth': 22, 'd_period': 29},
    'ZK/USDT': {'k_period': 80, 'k_smooth': 36, 'd_period': 9},
    'SOL/USDT': {'k_period': 80, 'k_smooth': 26, 'd_period': 6},
    'FET/USDT': {'k_period': 185, 'k_smooth': 26, 'd_period': 28},
    'ARKM/USDT': {'k_period': 160, 'k_smooth': 32, 'd_period': 9},
    'GAS/USDT': {'k_period': 125, 'k_smooth': 44, 'd_period': 21},
    'PENDLE/USDT': {'k_period': 155, 'k_smooth': 30, 'd_period': 7},
    'WIF/USDT': {'k_period': 100, 'k_smooth': 36, 'd_period': 15},
    'EIGEN/USDT': {'k_period': 150, 'k_smooth': 52, 'd_period': 19},
    'ONE/USDT': {'k_period': 185, 'k_smooth': 44, 'd_period': 16},
    'CHR/USDT': {'k_period': 200, 'k_smooth': 56, 'd_period': 13},
    'RAY/USDT': {'k_period': 175, 'k_smooth': 24, 'd_period': 10},
    'MANA/USDT': {'k_period': 130, 'k_smooth': 38, 'd_period': 14},
    'ETHFI/USDT': {'k_period': 145, 'k_smooth': 30, 'd_period': 30},
    'HBAR/USDT': {'k_period': 50, 'k_smooth': 34, 'd_period': 19},
    'JASMY/USDT': {'k_period': 150, 'k_smooth': 26, 'd_period': 9},
    'SHIB/USDT': {'k_period': 60, 'k_smooth': 26, 'd_period': 5},
    'BANANA/USDT': {'k_period': 190, 'k_smooth': 58, 'd_period': 25},
    'CHZ/USDT': {'k_period': 195, 'k_smooth': 24, 'd_period': 24},
    'DENT/USDT': {'k_period': 195, 'k_smooth': 20, 'd_period': 6},
    'CETUS/USDT': {'k_period': 90, 'k_smooth': 58, 'd_period': 18},
    'CFX/USDT': {'k_period': 175, 'k_smooth': 22, 'd_period': 27},
    'GALA/USDT': {'k_period': 190, 'k_smooth': 34, 'd_period': 19},
    'HIVE/USDT': {'k_period': 150, 'k_smooth': 30, 'd_period': 15},
    '1MBABYDOGE/USDT': {'k_period': 145, 'k_smooth': 60, 'd_period': 25},
    'NFP/USDT': {'k_period': 60, 'k_smooth': 58, 'd_period': 23},
    'RENDER/USDT': {'k_period': 160, 'k_smooth': 54, 'd_period': 10},
    'OP/USDT': {'k_period': 120, 'k_smooth': 58, 'd_period': 23},
    'FLUX/USDT': {'k_period': 185, 'k_smooth': 30, 'd_period': 12},
    'SCR/USDT': {'k_period': 165, 'k_smooth': 20, 'd_period': 9},
    'GLM/USDT': {'k_period': 60, 'k_smooth': 22, 'd_period': 9},
    'ADA/USDT': {'k_period': 200, 'k_smooth': 34, 'd_period': 9},
    'AMP/USDT': {'k_period': 160, 'k_smooth': 22, 'd_period': 15},
    'ANKR/USDT': {'k_period': 125, 'k_smooth': 42, 'd_period': 24},
    'CVX/USDT': {'k_period': 150, 'k_smooth': 30, 'd_period': 12},
    'PHB/USDT': {'k_period': 130, 'k_smooth': 22, 'd_period': 25},
    'VIC/USDT': {'k_period': 155, 'k_smooth': 24, 'd_period': 18},
    'CVC/USDT': {'k_period': 85, 'k_smooth': 30, 'd_period': 18},
    'ARB/USDT': {'k_period': 145, 'k_smooth': 50, 'd_period': 15},
    'STORJ/USDT': {'k_period': 150, 'k_smooth': 24, 'd_period': 11},
    'ACH/USDT': {'k_period': 130, 'k_smooth': 26, 'd_period': 28},
    'BB/USDT': {'k_period': 60, 'k_smooth': 56, 'd_period': 5},
    'AI/USDT': {'k_period': 125, 'k_smooth': 40, 'd_period': 19},
    'BLUR/USDT': {'k_period': 150, 'k_smooth': 54, 'd_period': 22},
    'BOME/USDT': {'k_period': 135, 'k_smooth': 34, 'd_period': 27},
    'OSMO/USDT': {'k_period': 195, 'k_smooth': 42, 'd_period': 22},
    'LISTA/USDT': {'k_period': 75, 'k_smooth': 50, 'd_period': 30},
    'CELR/USDT': {'k_period': 115, 'k_smooth': 30, 'd_period': 14},
    'AVAX/USDT': {'k_period': 55, 'k_smooth': 34, 'd_period': 7},
    'MAGIC/USDT': {'k_period': 170, 'k_smooth': 30, 'd_period': 14},
    'COTI/USDT': {'k_period': 120, 'k_smooth': 24, 'd_period': 23},
    'IMX/USDT': {'k_period': 200, 'k_smooth': 42, 'd_period': 12},
    'AXS/USDT': {'k_period': 55, 'k_smooth': 32, 'd_period': 5},
    'USTC/USDT': {'k_period': 50, 'k_smooth': 22, 'd_period': 29},
    'RUNE/USDT': {'k_period': 95, 'k_smooth': 36, 'd_period': 5},
    'LUNC/USDT': {'k_period': 145, 'k_smooth': 36, 'd_period': 30},
    'OG/USDT': {'k_period': 135, 'k_smooth': 40, 'd_period': 12},
    'ZEC/USDT': {'k_period': 120, 'k_smooth': 38, 'd_period': 8},
    'ENJ/USDT': {'k_period': 125, 'k_smooth': 22, 'd_period': 12},
    'VTHO/USDT': {'k_period': 130, 'k_smooth': 34, 'd_period': 7},
    'WBETH/USDT': {'k_period': 150, 'k_smooth': 34, 'd_period': 25},
    'METIS/USDT': {'k_period': 100, 'k_smooth': 58, 'd_period': 24},
    'ENS/USDT': {'k_period': 60, 'k_smooth': 26, 'd_period': 28},
    'AR/USDT': {'k_period': 150, 'k_smooth': 56, 'd_period': 6},
    'JOE/USDT': {'k_period': 170, 'k_smooth': 36, 'd_period': 16},
    'ID/USDT': {'k_period': 140, 'k_smooth': 36, 'd_period': 28},
    'LUMIA/USDT': {'k_period': 145, 'k_smooth': 24, 'd_period': 15},
    'POL/USDT': {'k_period': 145, 'k_smooth': 20, 'd_period': 22},
    'SSV/USDT': {'k_period': 170, 'k_smooth': 20, 'd_period': 9},
    'RVN/USDT': {'k_period': 120, 'k_smooth': 42, 'd_period': 5},
    '1000CAT/USDT': {'k_period': 60, 'k_smooth': 36, 'd_period': 14},
    'STX/USDT': {'k_period': 105, 'k_smooth': 58, 'd_period': 29},
    'KAIA/USDT': {'k_period': 55, 'k_smooth': 60, 'd_period': 8},
    'DEXE/USDT': {'k_period': 200, 'k_smooth': 22, 'd_period': 14},
    'POLYX/USDT': {'k_period': 75, 'k_smooth': 58, 'd_period': 29},
    'IQ/USDT': {'k_period': 85, 'k_smooth': 24, 'd_period': 20},
    'MDT/USDT': {'k_period': 50, 'k_smooth': 40, 'd_period': 6},
    'SEI/USDT': {'k_period': 70, 'k_smooth': 60, 'd_period': 5},
    'CYBER/USDT': {'k_period': 120, 'k_smooth': 54, 'd_period': 8},
    'HOT/USDT': {'k_period': 185, 'k_smooth': 52, 'd_period': 6},
    'WLD/USDT': {'k_period': 125, 'k_smooth': 22, 'd_period': 8},
    'TRU/USDT': {'k_period': 145, 'k_smooth': 32, 'd_period': 12},
    'SNX/USDT': {'k_period': 80, 'k_smooth': 56, 'd_period': 22},
    'ZIL/USDT': {'k_period': 135, 'k_smooth': 36, 'd_period': 6},
    'MOVR/USDT': {'k_period': 160, 'k_smooth': 48, 'd_period': 28},
    'BNT/USDT': {'k_period': 50, 'k_smooth': 46, 'd_period': 6},
    'SUN/USDT': {'k_period': 55, 'k_smooth': 28, 'd_period': 16},
    'SYN/USDT': {'k_period': 100, 'k_smooth': 48, 'd_period': 17},
    'QKC/USDT': {'k_period': 55, 'k_smooth': 26, 'd_period': 10},
    'XAI/USDT': {'k_period': 90, 'k_smooth': 38, 'd_period': 12},
    'MEME/USDT': {'k_period': 140, 'k_smooth': 34, 'd_period': 26},
    'LDO/USDT': {'k_period': 110, 'k_smooth': 50, 'd_period': 16},
    'IOST/USDT': {'k_period': 130, 'k_smooth': 28, 'd_period': 27},
    'LPT/USDT': {'k_period': 95, 'k_smooth': 20, 'd_period': 14},
    'PROM/USDT': {'k_period': 155, 'k_smooth': 38, 'd_period': 16},
    'XTZ/USDT': {'k_period': 60, 'k_smooth': 28, 'd_period': 7},
    'OM/USDT': {'k_period': 75, 'k_smooth': 60, 'd_period': 10},
    'ASTR/USDT': {'k_period': 195, 'k_smooth': 58, 'd_period': 25},
    'CKB/USDT': {'k_period': 165, 'k_smooth': 30, 'd_period': 10},
    'ORDI/USDT': {'k_period': 130, 'k_smooth': 38, 'd_period': 20},
    'LINK/USDT': {'k_period': 55, 'k_smooth': 26, 'd_period': 12},
    'KAVA/USDT': {'k_period': 125, 'k_smooth': 34, 'd_period': 24},
    'DOT/USDT': {'k_period': 100, 'k_smooth': 28, 'd_period': 6},
    'QNT/USDT': {'k_period': 185, 'k_smooth': 24, 'd_period': 17},
    'LQTY/USDT': {'k_period': 160, 'k_smooth': 26, 'd_period': 18},
    'TWT/USDT': {'k_period': 80, 'k_smooth': 20, 'd_period': 12},
    'XVG/USDT': {'k_period': 125, 'k_smooth': 22, 'd_period': 6},
    'WBTC/USDT': {'k_period': 85, 'k_smooth': 30, 'd_period': 20},
    'XLM/USDT': {'k_period': 120, 'k_smooth': 46, 'd_period': 5},
    'KSM/USDT': {'k_period': 50, 'k_smooth': 20, 'd_period': 5},
    'CAKE/USDT': {'k_period': 140, 'k_smooth': 20, 'd_period': 6},
    'EGLD/USDT': {'k_period': 55, 'k_smooth': 20, 'd_period': 5},
    '1INCH/USDT': {'k_period': 140, 'k_smooth': 20, 'd_period': 10},
    'RPL/USDT': {'k_period': 110, 'k_smooth': 54, 'd_period': 28},
    'SUSHI/USDT': {'k_period': 80, 'k_smooth': 28, 'd_period': 20},
    'VANA/USDT': {'k_period': 115, 'k_smooth': 50, 'd_period': 25},
    'ALGO/USDT': {'k_period': 130, 'k_smooth': 26, 'd_period': 12},
    'T/USDT': {'k_period': 145, 'k_smooth': 56, 'd_period': 21},
    'VANRY/USDT': {'k_period': 115, 'k_smooth': 60, 'd_period': 21},
    'GNS/USDT': {'k_period': 165, 'k_smooth': 34, 'd_period': 9},
    'ETH/USDT': {'k_period': 170, 'k_smooth': 26, 'd_period': 24},
    'PIXEL/USDT': {'k_period': 135, 'k_smooth': 26, 'd_period': 13},
    'FIL/USDT': {'k_period': 70, 'k_smooth': 32, 'd_period': 6},
    'ETC/USDT': {'k_period': 200, 'k_smooth': 32, 'd_period': 24},
}

# ============================================================
# 역방향 전략 설정 - 파인튜닝 결과
# ============================================================

REVERSE_CONFIG = {
    'TURBO/USDT': {'error_rate': -34, 'hold_hours': 112},
    'NEIRO/USDT': {'error_rate': -22, 'hold_hours': 88},
    'FLOKI/USDT': {'error_rate': -30, 'hold_hours': 240},
    'BONK/USDT': {'error_rate': -20, 'hold_hours': 64},
    'PNUT/USDT': {'error_rate': -20, 'hold_hours': 152},
    'ENA/USDT': {'error_rate': -38, 'hold_hours': 56},
    'PEPE/USDT': {'error_rate': -26, 'hold_hours': 208},
    'SUI/USDT': {'error_rate': -30, 'hold_hours': 72},
    'COW/USDT': {'error_rate': -22, 'hold_hours': 72},
    'SAND/USDT': {'error_rate': -36, 'hold_hours': 280},
    'TAO/USDT': {'error_rate': -28, 'hold_hours': 400},
    'BNSOL/USDT': {'error_rate': -10, 'hold_hours': 96},
    'ZK/USDT': {'error_rate': -12, 'hold_hours': 48},
    'SOL/USDT': {'error_rate': -54, 'hold_hours': 48},
    'FET/USDT': {'error_rate': -58, 'hold_hours': 192},
    'ARKM/USDT': {'error_rate': -26, 'hold_hours': 64},
    'GAS/USDT': {'error_rate': -24, 'hold_hours': 344},
    'PENDLE/USDT': {'error_rate': -36, 'hold_hours': 288},
    'WIF/USDT': {'error_rate': -46, 'hold_hours': 48},
    'EIGEN/USDT': {'error_rate': -20, 'hold_hours': 80},
    'ONE/USDT': {'error_rate': -34, 'hold_hours': 96},
    'CHR/USDT': {'error_rate': -44, 'hold_hours': 208},
    'RAY/USDT': {'error_rate': -60, 'hold_hours': 48},
    'MANA/USDT': {'error_rate': -26, 'hold_hours': 208},
    'ETHFI/USDT': {'error_rate': -30, 'hold_hours': 56},
    'HBAR/USDT': {'error_rate': -18, 'hold_hours': 64},
    'JASMY/USDT': {'error_rate': -26, 'hold_hours': 384},
    'SHIB/USDT': {'error_rate': -24, 'hold_hours': 224},
    'BANANA/USDT': {'error_rate': -30, 'hold_hours': 64},
    'CHZ/USDT': {'error_rate': -26, 'hold_hours': 232},
    'DENT/USDT': {'error_rate': -66, 'hold_hours': 48},
    'CETUS/USDT': {'error_rate': -44, 'hold_hours': 240},
    'CFX/USDT': {'error_rate': -32, 'hold_hours': 192},
    'GALA/USDT': {'error_rate': -28, 'hold_hours': 136},
    'HIVE/USDT': {'error_rate': -24, 'hold_hours': 392},
    '1MBABYDOGE/USDT': {'error_rate': -20, 'hold_hours': 72},
    'NFP/USDT': {'error_rate': -26, 'hold_hours': 368},
    'RENDER/USDT': {'error_rate': -34, 'hold_hours': 360},
    'OP/USDT': {'error_rate': -14, 'hold_hours': 64},
    'FLUX/USDT': {'error_rate': -32, 'hold_hours': 320},
    'SCR/USDT': {'error_rate': -32, 'hold_hours': 56},
    'GLM/USDT': {'error_rate': -16, 'hold_hours': 336},
    'ADA/USDT': {'error_rate': -16, 'hold_hours': 56},
    'AMP/USDT': {'error_rate': -26, 'hold_hours': 96},
    'ANKR/USDT': {'error_rate': -52, 'hold_hours': 256},
    'CVX/USDT': {'error_rate': -26, 'hold_hours': 312},
    'PHB/USDT': {'error_rate': -34, 'hold_hours': 360},
    'VIC/USDT': {'error_rate': -30, 'hold_hours': 56},
    'CVC/USDT': {'error_rate': -22, 'hold_hours': 248},
    'ARB/USDT': {'error_rate': -22, 'hold_hours': 328},
    'STORJ/USDT': {'error_rate': -28, 'hold_hours': 352},
    'ACH/USDT': {'error_rate': -18, 'hold_hours': 96},
    'BB/USDT': {'error_rate': -22, 'hold_hours': 208},
    'AI/USDT': {'error_rate': -28, 'hold_hours': 392},
    'BLUR/USDT': {'error_rate': -16, 'hold_hours': 344},
    'BOME/USDT': {'error_rate': -34, 'hold_hours': 288},
    'OSMO/USDT': {'error_rate': -34, 'hold_hours': 176},
    'LISTA/USDT': {'error_rate': -26, 'hold_hours': 56},
    'CELR/USDT': {'error_rate': -56, 'hold_hours': 80},
    'AVAX/USDT': {'error_rate': -74, 'hold_hours': 128},
    'MAGIC/USDT': {'error_rate': -34, 'hold_hours': 368},
    'COTI/USDT': {'error_rate': -30, 'hold_hours': 176},
    'IMX/USDT': {'error_rate': -26, 'hold_hours': 352},
    'AXS/USDT': {'error_rate': -30, 'hold_hours': 400},
    'USTC/USDT': {'error_rate': -26, 'hold_hours': 392},
    'RUNE/USDT': {'error_rate': -58, 'hold_hours': 192},
    'LUNC/USDT': {'error_rate': -24, 'hold_hours': 288},
    'OG/USDT': {'error_rate': -34, 'hold_hours': 384},
    'ZEC/USDT': {'error_rate': -30, 'hold_hours': 176},
    'ENJ/USDT': {'error_rate': -58, 'hold_hours': 256},
    'VTHO/USDT': {'error_rate': -46, 'hold_hours': 56},
    'WBETH/USDT': {'error_rate': -12, 'hold_hours': 48},
    'METIS/USDT': {'error_rate': -16, 'hold_hours': 128},
    'ENS/USDT': {'error_rate': -16, 'hold_hours': 320},
    'AR/USDT': {'error_rate': -30, 'hold_hours': 288},
    'JOE/USDT': {'error_rate': -24, 'hold_hours': 328},
    'ID/USDT': {'error_rate': -36, 'hold_hours': 200},
    'LUMIA/USDT': {'error_rate': -20, 'hold_hours': 56},
    'POL/USDT': {'error_rate': -16, 'hold_hours': 88},
    'SSV/USDT': {'error_rate': -24, 'hold_hours': 272},
    'RVN/USDT': {'error_rate': -26, 'hold_hours': 216},
    '1000CAT/USDT': {'error_rate': -34, 'hold_hours': 328},
    'STX/USDT': {'error_rate': -54, 'hold_hours': 88},
    'KAIA/USDT': {'error_rate': -30, 'hold_hours': 80},
    'DEXE/USDT': {'error_rate': -20, 'hold_hours': 208},
    'POLYX/USDT': {'error_rate': -16, 'hold_hours': 224},
    'IQ/USDT': {'error_rate': -20, 'hold_hours': 208},
    'MDT/USDT': {'error_rate': -30, 'hold_hours': 304},
    'SEI/USDT': {'error_rate': -26, 'hold_hours': 168},
    'CYBER/USDT': {'error_rate': -34, 'hold_hours': 240},
    'HOT/USDT': {'error_rate': -54, 'hold_hours': 192},
    'WLD/USDT': {'error_rate': -50, 'hold_hours': 264},
    'TRU/USDT': {'error_rate': -40, 'hold_hours': 368},
    'SNX/USDT': {'error_rate': -26, 'hold_hours': 56},
    'ZIL/USDT': {'error_rate': -54, 'hold_hours': 264},
    'MOVR/USDT': {'error_rate': -24, 'hold_hours': 336},
    'BNT/USDT': {'error_rate': -56, 'hold_hours': 360},
    'SUN/USDT': {'error_rate': -32, 'hold_hours': 256},
    'SYN/USDT': {'error_rate': -28, 'hold_hours': 72},
    'QKC/USDT': {'error_rate': -22, 'hold_hours': 384},
    'XAI/USDT': {'error_rate': -32, 'hold_hours': 360},
    'MEME/USDT': {'error_rate': -30, 'hold_hours': 248},
    'LDO/USDT': {'error_rate': -16, 'hold_hours': 104},
    'IOST/USDT': {'error_rate': -18, 'hold_hours': 112},
    'LPT/USDT': {'error_rate': -32, 'hold_hours': 120},
    'PROM/USDT': {'error_rate': -12, 'hold_hours': 80},
    'XTZ/USDT': {'error_rate': -24, 'hold_hours': 280},
    'OM/USDT': {'error_rate': -44, 'hold_hours': 112},
    'ASTR/USDT': {'error_rate': -24, 'hold_hours': 64},
    'CKB/USDT': {'error_rate': -26, 'hold_hours': 56},
    'ORDI/USDT': {'error_rate': -28, 'hold_hours': 312},
    'LINK/USDT': {'error_rate': -60, 'hold_hours': 248},
    'KAVA/USDT': {'error_rate': -26, 'hold_hours': 280},
    'DOT/USDT': {'error_rate': -28, 'hold_hours': 208},
    'QNT/USDT': {'error_rate': -10, 'hold_hours': 56},
    'LQTY/USDT': {'error_rate': -34, 'hold_hours': 80},
    'TWT/USDT': {'error_rate': -50, 'hold_hours': 192},
    'XVG/USDT': {'error_rate': -26, 'hold_hours': 136},
    'WBTC/USDT': {'error_rate': -10, 'hold_hours': 392},
    'XLM/USDT': {'error_rate': -22, 'hold_hours': 336},
    'KSM/USDT': {'error_rate': -52, 'hold_hours': 64},
    'CAKE/USDT': {'error_rate': -22, 'hold_hours': 352},
    'EGLD/USDT': {'error_rate': -70, 'hold_hours': 224},
    '1INCH/USDT': {'error_rate': -22, 'hold_hours': 128},
    'RPL/USDT': {'error_rate': -22, 'hold_hours': 176},
    'SUSHI/USDT': {'error_rate': -36, 'hold_hours': 344},
    'VANA/USDT': {'error_rate': -28, 'hold_hours': 56},
    'ALGO/USDT': {'error_rate': -34, 'hold_hours': 128},
    'T/USDT': {'error_rate': -20, 'hold_hours': 368},
    'VANRY/USDT': {'error_rate': -34, 'hold_hours': 400},
    'GNS/USDT': {'error_rate': -20, 'hold_hours': 264},
    'ETH/USDT': {'error_rate': -30, 'hold_hours': 400},
    'PIXEL/USDT': {'error_rate': -66, 'hold_hours': 64},
    'FIL/USDT': {'error_rate': -40, 'hold_hours': 48},
    'ETC/USDT': {'error_rate': -22, 'hold_hours': 344},
}

# ============================================================
# 전역 변수
# ============================================================

buy_status = {}
stoch_cache = {}
stoch_cache_date = None
exchange = None

# ============================================================
# 거래소 초기화
# ============================================================

def init_exchange():
    """바이낸스 거래소 초기화"""
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
    """텔레그램 메시지 전송"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }
        response = requests.post(url, data=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logging.error(f"텔레그램 전송 중 오류: {e}")
        return False


def send_trade_summary(buy_list, sell_list, total_asset, usdt_balance, bnb_info, errors):
    """거래 종합 요약 전송 (4시간마다 한 번)"""
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
            msg += f"  • {item['symbol']}: ${item['amount']:.2f} ({item['strategy']})\n"
        if len(buy_list) > 10:
            msg += f"  ... 외 {len(buy_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    if sell_list:
        msg += f"🔴 <b>매도 {len(sell_list)}건</b>\n"
        for item in sell_list[:10]:
            msg += f"  • {item['symbol']}: {item['reason']}\n"
        if len(sell_list) > 10:
            msg += f"  ... 외 {len(sell_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    if not buy_list and not sell_list:
        msg += f"ℹ️ 거래 없음\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    if errors:
        msg += f"⚠️ <b>오류 {len(errors)}건</b>\n"
        for err in errors[:5]:
            msg += f"  • {err}\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    msg += f"🕐 {now}"
    
    send_telegram(msg)


def send_start_alert(status_loaded=False):
    """봇 시작 알림"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    msg = f"🚀 <b>바이낸스 자동매매 봇 시작 (v1.1.0)</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"📈 전략: MA + 스토캐스틱 + 역방향\n"
    msg += f"💰 수수료: 0.075% (BNB 할인)\n"
    msg += f"🔶 BNB 자동충전: ${BNB_MIN_BALANCE} 이하시 ${BNB_RECHARGE_AMOUNT} 충전\n"
    msg += f"🪙 대상: {len(COINS)}개 코인 (파인튜닝)\n"
    if status_loaded:
        msg += f"📂 이전 상태: 복원됨\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"🕐 {now}"
    
    send_telegram(msg)


def send_shutdown_alert(reason="수동 종료"):
    """봇 종료 알림"""
    global SHUTDOWN_SENT
    
    if SHUTDOWN_SENT:
        return
    SHUTDOWN_SENT = True
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if BOT_START_TIME:
        uptime = datetime.now() - BOT_START_TIME
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        
        if days > 0:
            uptime_str = f"{days}일 {hours}시간 {minutes}분"
        elif hours > 0:
            uptime_str = f"{hours}시간 {minutes}분"
        else:
            uptime_str = f"{minutes}분"
    else:
        uptime_str = "알 수 없음"
    
    msg = f"🛑 <b>바이낸스 봇 종료</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"📋 종료 사유: {reason}\n"
    msg += f"⏱️ 실행 시간: {uptime_str}\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"🕐 {now}"
    
    send_telegram(msg)
    logging.info(f"종료 알림 전송 완료: {reason}")


# ============================================================
# 종료 핸들러 설정
# ============================================================

def signal_handler(signum, frame):
    """시그널 핸들러"""
    signal_names = {
        signal.SIGINT: "SIGINT (Ctrl+C)",
        signal.SIGTERM: "SIGTERM (kill)",
    }
    signal_name = signal_names.get(signum, f"Signal {signum}")
    
    logging.info(f"종료 시그널 수신: {signal_name}")
    send_shutdown_alert(reason=signal_name)
    
    try:
        save_status()
        logging.info("상태 저장 완료")
    except Exception as e:
        logging.error(f"상태 저장 실패: {e}")
    
    sys.exit(0)


def exit_handler():
    """프로그램 종료 시 호출"""
    send_shutdown_alert(reason="프로그램 종료")


def setup_shutdown_handlers():
    """종료 핸들러 설정"""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, signal_handler)
    
    atexit.register(exit_handler)
    logging.info("종료 핸들러 설정 완료")


# ============================================================
# 상태 저장/로드 함수
# ============================================================

def save_status():
    """매수 상태를 파일에 저장"""
    global buy_status
    try:
        save_data = {}
        for symbol, status in buy_status.items():
            save_data[symbol] = {
                'is_reverse_holding': status['is_reverse_holding'],
                'reverse_start_time': status['reverse_start_time'].isoformat() if status['reverse_start_time'] else None,
                'reverse_hold_hours': status['reverse_hold_hours']
            }
        
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        return True
    except Exception as e:
        logging.error(f"상태 저장 중 오류: {e}")
        return False


def load_status():
    """저장된 매수 상태 불러오기"""
    global buy_status
    try:
        if not os.path.exists(STATUS_FILE):
            logging.info("저장된 상태 파일이 없습니다. 새로 시작합니다.")
            return False
        
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        loaded_count = 0
        for symbol, status in data.items():
            if symbol in buy_status:
                buy_status[symbol]['is_reverse_holding'] = status.get('is_reverse_holding', False)
                
                start_time_str = status.get('reverse_start_time')
                if start_time_str:
                    buy_status[symbol]['reverse_start_time'] = datetime.fromisoformat(start_time_str)
                else:
                    buy_status[symbol]['reverse_start_time'] = None
                
                buy_status[symbol]['reverse_hold_hours'] = status.get('reverse_hold_hours', 0)
                
                if buy_status[symbol]['is_reverse_holding']:
                    loaded_count += 1
                    logging.info(f"📂 {symbol} 역방향 상태 복원")
        
        logging.info(f"상태 로드 완료: {loaded_count}개 역방향 보유 중")
        return loaded_count > 0
    except Exception as e:
        logging.error(f"상태 로드 중 오류: {e}")
        return False


def save_stoch_cache():
    """스토캐스틱 캐시 저장"""
    global stoch_cache, stoch_cache_date
    try:
        save_data = {
            'cache_date': stoch_cache_date.isoformat() if stoch_cache_date else None,
            'data': stoch_cache
        }
        
        with open(STOCH_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        return True
    except Exception as e:
        logging.error(f"스토캐스틱 캐시 저장 중 오류: {e}")
        return False


def load_stoch_cache():
    """스토캐스틱 캐시 로드"""
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


def initialize_status():
    """매수 상태 초기화"""
    global buy_status
    for symbol in COINS:
        buy_status[symbol] = {
            'is_reverse_holding': False,
            'reverse_start_time': None,
            'reverse_hold_hours': 0
        }


# ============================================================
# BNB 자동 충전 기능
# ============================================================

def get_bnb_balance():
    """BNB 잔고 조회 (USDT 가치 포함)"""
    try:
        balance = exchange.fetch_balance()
        bnb_amount = float(balance.get('BNB', {}).get('free', 0))
        
        if bnb_amount > 0:
            ticker = exchange.fetch_ticker('BNB/USDT')
            bnb_price = ticker['last']
            bnb_value = bnb_amount * bnb_price
        else:
            bnb_value = 0
            bnb_price = 0
        
        return {
            'balance': bnb_amount,
            'price': bnb_price,
            'value': bnb_value
        }
    except Exception as e:
        logging.error(f"BNB 잔고 조회 중 오류: {e}")
        return {'balance': 0, 'price': 0, 'value': 0}


def check_and_recharge_bnb():
    """BNB 잔고 확인 및 자동 충전"""
    try:
        bnb_info = get_bnb_balance()
        bnb_value = bnb_info['value']
        
        logging.info(f"🔶 BNB 잔고: {bnb_info['balance']:.4f} BNB (${bnb_value:.2f})")
        
        if bnb_value < BNB_MIN_BALANCE:
            logging.info(f"🔶 BNB 잔고 부족 (${bnb_value:.2f} < ${BNB_MIN_BALANCE}), 충전 시작...")
            
            # USDT 잔고 확인
            usdt_balance = get_usdt_balance()
            
            if usdt_balance < BNB_RECHARGE_AMOUNT:
                logging.warning(f"⚠️ USDT 잔고 부족으로 BNB 충전 불가 (${usdt_balance:.2f} < ${BNB_RECHARGE_AMOUNT})")
                return None
            
            # BNB 시장가 매수
            try:
                order = exchange.create_market_buy_order('BNB/USDT', None, {'quoteOrderQty': BNB_RECHARGE_AMOUNT})
                
                # 매수 후 잔고 다시 조회
                time.sleep(1)
                new_bnb_info = get_bnb_balance()
                
                logging.info(f"✅ BNB 충전 완료: ${BNB_RECHARGE_AMOUNT} → {new_bnb_info['balance']:.4f} BNB")
                
                return {
                    'action': 'recharged',
                    'amount': BNB_RECHARGE_AMOUNT,
                    'new_balance': new_bnb_info['balance'],
                    'new_value': new_bnb_info['value']
                }
                
            except Exception as e:
                logging.error(f"❌ BNB 충전 실패: {e}")
                return None
        else:
            return {
                'action': 'sufficient',
                'balance': bnb_info['balance'],
                'value': bnb_value
            }
            
    except Exception as e:
        logging.error(f"BNB 충전 확인 중 오류: {e}")
        return None


# ============================================================
# 잔고 조회 함수
# ============================================================

def get_usdt_balance():
    """USDT 잔고 조회"""
    try:
        balance = exchange.fetch_balance()
        return float(balance['USDT']['free'])
    except Exception as e:
        logging.error(f"USDT 잔고 조회 중 오류: {e}")
        return 0


def get_total_asset():
    """총 자산 계산 (USDT 기준)"""
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
    """특정 코인 잔고 조회"""
    try:
        base = symbol.split('/')[0]
        balance = exchange.fetch_balance()
        return float(balance.get(base, {}).get('free', 0))
    except Exception as e:
        logging.error(f"{symbol} 잔고 조회 중 오류: {e}")
        return 0


def count_empty_slots():
    """매수 가능한 빈 슬롯 수 계산"""
    try:
        balance = exchange.fetch_balance()
        empty_count = 0
        
        for symbol in COINS:
            base = symbol.split('/')[0]
            coin_balance = float(balance.get(base, {}).get('free', 0))
            
            if symbol in exchange.markets:
                try:
                    ticker = exchange.fetch_ticker(symbol)
                    coin_value = coin_balance * ticker['last']
                    if coin_value < MIN_ORDER_USDT:
                        empty_count += 1
                    time.sleep(0.02)
                except:
                    empty_count += 1
            else:
                empty_count += 1
        
        return empty_count
    except Exception as e:
        logging.error(f"빈 슬롯 계산 중 오류: {e}")
        return len(COINS)


def calculate_invest_amount():
    """투자금액 계산"""
    usdt_balance = get_usdt_balance()
    empty_slots = count_empty_slots()
    
    if empty_slots == 0:
        logging.info("매수 가능한 빈 슬롯이 없습니다.")
        return 0
    
    # 가용 USDT / 빈 슬롯 수
    available_usdt = usdt_balance * 0.995
    amount_by_available = available_usdt / empty_slots
    
    # 총자산 / 코인 개수 (상한선)
    total_asset = get_total_asset()
    num_coins = len(COINS)
    max_by_equity = total_asset / num_coins
    
    # 두 방식 중 작은 값 선택
    invest_amount = min(amount_by_available, max_by_equity)
    
    logging.info(f"💰 자금 배분: 가용잔고=${amount_by_available:.2f}, 총자산=${max_by_equity:.2f} → 선택: ${invest_amount:.2f}")
    
    if invest_amount < MIN_ORDER_USDT:
        logging.warning(f"투자금액(${invest_amount:.2f})이 최소 주문금액(${MIN_ORDER_USDT}) 미만입니다.")
        return 0
    
    return invest_amount


# ============================================================
# 시세 조회 함수
# ============================================================

def fetch_ohlcv_batch(symbol, timeframe, limit):
    """OHLCV 데이터 조회 (재시도 포함)"""
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
    """현재가 조회"""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker['last'])
    except Exception as e:
        logging.error(f"{symbol} 현재가 조회 중 오류: {e}")
        return None


def get_ma_price(symbol, period):
    """4시간봉 MA 계산"""
    try:
        df = fetch_ohlcv_batch(symbol, '4h', period + 10)
        if df is None or len(df) < period:
            return None
        return float(df['close'].tail(period).mean())
    except Exception as e:
        logging.error(f"{symbol} MA 계산 중 오류: {e}")
        return None


# ============================================================
# 스토캐스틱 함수
# ============================================================

def calculate_stochastic(df, k_period, k_smooth, d_period):
    """스토캐스틱 계산"""
    if df is None or len(df) < k_period:
        return None, None
    
    low_min = df['low'].rolling(window=k_period).min()
    high_max = df['high'].rolling(window=k_period).max()
    
    denom = high_max - low_min
    denom = denom.replace(0, np.nan)
    
    fast_k = ((df['close'] - low_min) / denom) * 100
    slow_k = fast_k.rolling(window=k_smooth).mean()
    slow_d = slow_k.rolling(window=d_period).mean()
    
    if pd.isna(slow_k.iloc[-1]) or pd.isna(slow_d.iloc[-1]):
        return None, None
    
    return float(slow_k.iloc[-1]), float(slow_d.iloc[-1])


def should_refresh_stoch_cache():
    """스토캐스틱 캐시 갱신 필요 여부 확인 (UTC 00:00 기준)"""
    global stoch_cache_date
    
    now_utc = datetime.now(timezone.utc)
    today_utc = now_utc.date()
    
    if stoch_cache_date is None:
        return True
    
    if stoch_cache_date < today_utc:
        return True
    
    return False


def refresh_all_stochastic():
    """모든 코인의 스토캐스틱 데이터 갱신"""
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
                stoch_cache[symbol] = {
                    'signal': bool(slow_k > slow_d),
                    'slow_k': slow_k,
                    'slow_d': slow_d
                }
            
            time.sleep(0.1)
            
        except Exception as e:
            logging.error(f"{symbol} 스토캐스틱 계산 중 오류: {e}")
    
    stoch_cache_date = datetime.now(timezone.utc).date()
    save_stoch_cache()
    
    logging.info(f"📊 스토캐스틱 데이터 갱신 완료: {len(stoch_cache)}개 코인")


def get_stochastic_signal(symbol):
    """스토캐스틱 시그널 조회"""
    if should_refresh_stoch_cache():
        refresh_all_stochastic()
    
    return stoch_cache.get(symbol)


# ============================================================
# 전략 함수
# ============================================================

def calculate_error_rate(price, ma_price):
    """오차율 계산"""
    if ma_price is None or ma_price <= 0:
        return 0
    return ((price - ma_price) / ma_price) * 100


def check_reverse_strategy(symbol, current_price, ma_price):
    """역방향 전략 체크"""
    global buy_status
    
    if symbol not in REVERSE_CONFIG:
        return False, False, 0
    
    config = REVERSE_CONFIG[symbol]
    error_rate_threshold = config['error_rate']
    hold_duration_hours = config['hold_hours']
    
    current_time = datetime.now()
    error_rate = calculate_error_rate(current_price, ma_price)
    
    if buy_status[symbol]['is_reverse_holding']:
        start_time = buy_status[symbol]['reverse_start_time']
        if start_time:
            elapsed_hours = (current_time - start_time).total_seconds() / 3600
            
            if elapsed_hours >= hold_duration_hours:
                buy_status[symbol]['is_reverse_holding'] = False
                buy_status[symbol]['reverse_start_time'] = None
                buy_status[symbol]['reverse_hold_hours'] = 0
                save_status()
                
                logging.info(f"🔚 {symbol} 역방향 보유 기간 종료 ({elapsed_hours:.1f}h / {hold_duration_hours}h)")
                return False, False, error_rate
            else:
                remaining = hold_duration_hours - elapsed_hours
                logging.debug(f"⏳ {symbol} 역방향 보유 중 - 남은시간: {remaining:.1f}h")
                return True, True, error_rate
    
    if current_price < ma_price and error_rate <= error_rate_threshold:
        buy_status[symbol]['is_reverse_holding'] = True
        buy_status[symbol]['reverse_start_time'] = current_time
        buy_status[symbol]['reverse_hold_hours'] = hold_duration_hours
        save_status()
        
        logging.info(f"🔴 {symbol} 역방향 매수 신호! 오차율: {error_rate:.2f}% (임계값: {error_rate_threshold}%)")
        
        return True, True, error_rate
    
    return False, False, error_rate


# ============================================================
# 메인 거래 전략
# ============================================================

def trade_strategy():
    """거래 전략 실행"""
    buy_list = []
    sell_list = []
    errors = []
    
    try:
        # BNB 자동 충전 먼저 확인
        bnb_result = check_and_recharge_bnb()
        if bnb_result and bnb_result.get('action') == 'recharged':
            logging.info(f"🔶 BNB 충전됨: ${bnb_result['amount']} → {bnb_result['new_balance']:.4f} BNB")
        
        usdt_balance = get_usdt_balance()
        total_asset = get_total_asset()
        bnb_info = get_bnb_balance()
        
        logging.info("=" * 80)
        logging.info(f"📊 거래 전략 실행 시작 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"💰 총 자산: ${total_asset:,.2f} USDT")
        logging.info(f"💵 USDT 잔고: ${usdt_balance:,.2f}")
        logging.info(f"🔶 BNB 잔고: {bnb_info['balance']:.4f} (${bnb_info['value']:.2f})")
        logging.info("=" * 80)
        
        for symbol in COINS:
            try:
                time.sleep(0.15)
                
                # MA 계산
                ma_period = MA_PERIODS.get(symbol, 100)
                ma_price = get_ma_price(symbol, ma_period)
                time.sleep(0.05)
                
                # 현재가 조회
                current_price = get_current_price(symbol)
                time.sleep(0.05)
                
                # 스토캐스틱 조회
                stoch_data = get_stochastic_signal(symbol)
                
                if ma_price is None or current_price is None:
                    logging.warning(f"{symbol} 데이터 유효하지 않음")
                    continue
                
                # 현재 잔고 확인
                coin_balance = get_coin_balance(symbol)
                coin_value = coin_balance * current_price
                
                # 역방향 전략 체크
                reverse_signal, is_reverse_holding, error_rate = check_reverse_strategy(
                    symbol, current_price, ma_price
                )
                
                # MA 조건
                ma_condition = current_price > ma_price
                
                # 스토캐스틱 조건
                if stoch_data and stoch_data.get('signal') is not None:
                    stoch_condition = stoch_data['signal']
                else:
                    stoch_condition = True
                
                # 최종 매수 조건 결정
                if is_reverse_holding:
                    final_buy_condition = True
                    strategy_type = "역방향"
                elif ma_condition and stoch_condition:
                    final_buy_condition = True
                    strategy_type = "상승"
                else:
                    final_buy_condition = False
                    strategy_type = "없음"
                
                logging.debug(f"{symbol} | 가격:{current_price:.6f} | MA:{ma_price:.6f} | "
                            f"오차율:{error_rate:.1f}% | MA:{ma_condition} | Stoch:{stoch_condition} | "
                            f"최종:{final_buy_condition} ({strategy_type})")
                
                # 매매 실행
                if final_buy_condition:
                    if coin_value < MIN_ORDER_USDT:
                        invest_amount = calculate_invest_amount()
                        
                        if invest_amount >= MIN_ORDER_USDT:
                            try:
                                order = exchange.create_market_buy_order(symbol, None, {'quoteOrderQty': invest_amount})
                                
                                buy_list.append({
                                    'symbol': symbol,
                                    'amount': invest_amount,
                                    'strategy': strategy_type
                                })
                                
                                logging.info(f"🟢 {symbol} 매수 완료: ${invest_amount:.2f} ({strategy_type})")
                                time.sleep(0.2)
                                
                            except Exception as e:
                                err_msg = f"{symbol} 매수 실패: {e}"
                                logging.error(err_msg)
                                errors.append(err_msg)
                else:
                    if coin_value >= MIN_ORDER_USDT:
                        try:
                            order = exchange.create_market_sell_order(symbol, coin_balance)
                            
                            if not ma_condition:
                                sell_reason = "MA 조건 위반"
                            elif not stoch_condition:
                                sell_reason = "스토캐스틱 조건 위반"
                            else:
                                sell_reason = "조건 미충족"
                            
                            sell_list.append({
                                'symbol': symbol,
                                'reason': sell_reason
                            })
                            
                            logging.info(f"🔴 {symbol} 매도 완료 ({sell_reason})")
                            time.sleep(0.2)
                            
                        except Exception as e:
                            err_msg = f"{symbol} 매도 실패: {e}"
                            logging.error(err_msg)
                            errors.append(err_msg)
                
            except Exception as e:
                err_msg = f"{symbol} 처리 중 오류: {e}"
                logging.error(err_msg)
                errors.append(err_msg)
        
        # 종합 리포트 전송
        final_total = get_total_asset()
        final_usdt = get_usdt_balance()
        final_bnb = get_bnb_balance()
        send_trade_summary(buy_list, sell_list, final_total, final_usdt, final_bnb, errors)
        
        logging.info("=" * 80)
        logging.info(f"📊 거래 전략 실행 완료 - 매수: {len(buy_list)}건 / 매도: {len(sell_list)}건")
        logging.info("=" * 80)
        
        save_status()
        
    except Exception as e:
        logging.error(f"자동매매 전략 실행 중 오류: {e}")
        errors.append(f"전략 실행 오류: {e}")
        send_trade_summary([], [], 0, 0, None, errors)


def log_strategy_info():
    """전략 정보 로깅"""
    logging.info("=" * 80)
    logging.info("🤖 바이낸스 자동매매 봇 v1.1.0 (파인튜닝 파라미터)")
    logging.info("=" * 80)
    logging.info("📈 상승 전략:")
    logging.info("   - 조건1: 현재가 > MA (4H봉 기준)")
    logging.info("   - 조건2: Slow %K > Slow %D (1D봉 기준)")
    logging.info("-" * 80)
    logging.info("📉 역방향 전략:")
    logging.info("   - 조건: 현재가 < MA AND 오차율 <= 임계값")
    logging.info("   - 조건 충족 시 지정된 시간 동안 무조건 보유")
    logging.info("-" * 80)
    logging.info(f"🪙 거래 대상: {len(COINS)}개 코인")
    logging.info(f"💰 수수료: {FEE_RATE * 100:.3f}%")
    logging.info(f"🔶 BNB 자동충전: ${BNB_MIN_BALANCE} 이하시 ${BNB_RECHARGE_AMOUNT} 매수")
    logging.info("=" * 80)


def main():
    """메인 함수"""
    global BOT_START_TIME
    
    BOT_START_TIME = datetime.now()
    
    # 종료 핸들러 설정
    setup_shutdown_handlers()
    
    # 거래소 초기화
    if not init_exchange():
        logging.error("거래소 초기화 실패. 프로그램 종료.")
        return
    
    # 상태 초기화 및 로드
    initialize_status()
    status_loaded = load_status()
    load_stoch_cache()
    
    # 전략 정보 로깅
    log_strategy_info()
    
    # 시작 알림
    send_start_alert(status_loaded)
    
# 스케줄 설정 (KST 서버 기준 - 바이낸스 UTC 4H 캔들 시작에 맞춤)
    # UTC 00:00 = KST 09:00
    # UTC 04:00 = KST 13:00
    # UTC 08:00 = KST 17:00
    # UTC 12:00 = KST 21:00
    # UTC 16:00 = KST 01:00 (다음날)
    # UTC 20:00 = KST 05:00 (다음날)
    
    schedule.every().day.at("01:00").do(trade_strategy)  # UTC 16:00
    schedule.every().day.at("05:00").do(trade_strategy)  # UTC 20:00
    schedule.every().day.at("09:00").do(trade_strategy)  # UTC 00:00
    schedule.every().day.at("13:00").do(trade_strategy)  # UTC 04:00
    schedule.every().day.at("17:00").do(trade_strategy)  # UTC 08:00
    schedule.every().day.at("21:00").do(trade_strategy)  # UTC 12:00
    
    logging.info("자동매매 스크립트 시작")
    logging.info("실행 시간 (KST): 01:00, 05:00, 09:00, 13:00, 17:00, 21:00")
    logging.info("바이낸스 4H 캔들 시작 (UTC): 16:00, 20:00, 00:00, 04:00, 08:00, 12:00")
    
    # 시작 시 즉시 실행
    logging.info("🚀 시작 시 전략 즉시 실행...")
    trade_strategy()
    
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
