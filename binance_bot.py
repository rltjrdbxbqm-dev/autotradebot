"""
================================================================================
바이낸스 Futures 자동매매 봇 v6.0.0 (USDS-M Futures 숏+롱 코인별 우선순위)
================================================================================
- 코인별 롱/숏 우선순위 적용 (롱우선: 롱→숏 순, 숏우선: 숏→롱 순)
- USDS-M Futures 숏 포지션: 가격 < MA AND K < D → 숏 진입
- USDS-M Futures 롱 포지션: NOT 숏필터 AND 가격 > MA AND K > D → 롱 진입
- Futures BNB 자동 충전 (수수료 할인용, Spot 거래소 경유)
- 포지션 사이징: 총 자산 / 숏 코인 수 기준 균등 배분
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

FUTURES_STOCH_CACHE_FILE = os.path.join(os.path.expanduser('~'), 'binance_futures_stoch_cache.json')
LONG_STOCH_CACHE_FILE = os.path.join(os.path.expanduser('~'), 'binance_long_stoch_cache.json')

# ============================================================
# 거래 설정
# ============================================================

# Futures 설정
FUTURES_FEE_RATE = 0.0006  # 0.06% (BNB 할인 적용 시)
FUTURES_MIN_ORDER_USDT = 6  # Futures 최소 주문 금액

# Futures BNB 자동 충전 설정 (Spot 거래소 경유)
FUTURES_BNB_MIN_BALANCE = 10  # Futures 지갑 BNB 최소 보유량 (USDT 기준)
FUTURES_BNB_RECHARGE_AMOUNT = 20  # 충전 시 매수할 금액 (USDT)

# 종료 알림 관련 전역 변수
# ============================================================

BOT_START_TIME = None
SHUTDOWN_SENT = False


# ============================================================
# USDS-M Futures 숏 포지션 설정 (롱/숏 우선 최적화 반영 2026-03-14)
# ============================================================
# CSV 최적화 결과 반영: 코인별 롱우선/숏우선 Winner 파라미터 적용
# 최적화 미완료 코인은 기존 파라미터 유지 (기본 숏우선)
# 비용 반영: 수수료 0.04%, 슬리피지 0.05%, 펀딩비 0.01%/8h
# ============================================================

SHORT_TRADING_CONFIGS = [
    {'symbol': 'SUSDT', 'ma_period': 141, 'stoch_k_period': 145, 'stoch_k_smooth': 77, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'SOLVUSDT', 'ma_period': 242, 'stoch_k_period': 148, 'stoch_k_smooth': 78, 'stoch_d_period': 42, 'leverage': 5},
    {'symbol': 'RAYSOLUSDT', 'ma_period': 247, 'stoch_k_period': 37, 'stoch_k_smooth': 28, 'stoch_d_period': 14, 'leverage': 5},
    {'symbol': 'BERAUSDT', 'ma_period': 32, 'stoch_k_period': 117, 'stoch_k_smooth': 52, 'stoch_d_period': 42, 'leverage': 5},
    {'symbol': 'DUSDT', 'ma_period': 325, 'stoch_k_period': 145, 'stoch_k_smooth': 57, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'CGPTUSDT', 'ma_period': 112, 'stoch_k_period': 150, 'stoch_k_smooth': 60, 'stoch_d_period': 27, 'leverage': 5},
    {'symbol': '1000000MOGUSDT', 'ma_period': 144, 'stoch_k_period': 115, 'stoch_k_smooth': 77, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'VELODROMEUSDT', 'ma_period': 333, 'stoch_k_period': 148, 'stoch_k_smooth': 57, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'PENGUUSDT', 'ma_period': 101, 'stoch_k_period': 90, 'stoch_k_smooth': 77, 'stoch_d_period': 19, 'leverage': 5},
    {'symbol': 'AIXBTUSDT', 'ma_period': 154, 'stoch_k_period': 109, 'stoch_k_smooth': 15, 'stoch_d_period': 9, 'leverage': 5},
    {'symbol': 'MEUSDT', 'ma_period': 180, 'stoch_k_period': 30, 'stoch_k_smooth': 77, 'stoch_d_period': 26, 'leverage': 5},
    {'symbol': 'SONICUSDT', 'ma_period': 344, 'stoch_k_period': 21, 'stoch_k_smooth': 6, 'stoch_d_period': 7, 'leverage': 5},
    {'symbol': 'AEROUSDT', 'ma_period': 30, 'stoch_k_period': 148, 'stoch_k_smooth': 63, 'stoch_d_period': 34, 'leverage': 4},
    {'symbol': 'FARTCOINUSDT', 'ma_period': 188, 'stoch_k_period': 123, 'stoch_k_smooth': 61, 'stoch_d_period': 6, 'leverage': 5},
    {'symbol': 'CETUSUSDT', 'ma_period': 200, 'stoch_k_period': 132, 'stoch_k_smooth': 29, 'stoch_d_period': 8, 'leverage': 5},
    {'symbol': 'VTHOUSDT', 'ma_period': 310, 'stoch_k_period': 136, 'stoch_k_smooth': 59, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'PNUTUSDT', 'ma_period': 155, 'stoch_k_period': 122, 'stoch_k_smooth': 53, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'VINEUSDT', 'ma_period': 227, 'stoch_k_period': 88, 'stoch_k_smooth': 76, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'MOVEUSDT', 'ma_period': 48, 'stoch_k_period': 59, 'stoch_k_smooth': 62, 'stoch_d_period': 33, 'leverage': 5},
    {'symbol': 'MEWUSDT', 'ma_period': 107, 'stoch_k_period': 124, 'stoch_k_smooth': 56, 'stoch_d_period': 41, 'leverage': 5},
    {'symbol': 'PHAUSDT', 'ma_period': 205, 'stoch_k_period': 148, 'stoch_k_smooth': 80, 'stoch_d_period': 45, 'leverage': 5},
    {'symbol': 'VIRTUALUSDT', 'ma_period': 156, 'stoch_k_period': 108, 'stoch_k_smooth': 36, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'TRUMPUSDT', 'ma_period': 54, 'stoch_k_period': 144, 'stoch_k_smooth': 79, 'stoch_d_period': 17, 'leverage': 5},
    {'symbol': '1000CATUSDT', 'ma_period': 41, 'stoch_k_period': 42, 'stoch_k_smooth': 13, 'stoch_d_period': 16, 'leverage': 4},
    {'symbol': 'ZKUSDT', 'ma_period': 84, 'stoch_k_period': 95, 'stoch_k_smooth': 57, 'stoch_d_period': 41, 'leverage': 5},
    {'symbol': 'DEXEUSDT', 'ma_period': 87, 'stoch_k_period': 146, 'stoch_k_smooth': 30, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'GOATUSDT', 'ma_period': 186, 'stoch_k_period': 54, 'stoch_k_smooth': 14, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'EIGENUSDT', 'ma_period': 35, 'stoch_k_period': 150, 'stoch_k_smooth': 78, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'VANRYUSDT', 'ma_period': 115, 'stoch_k_period': 148, 'stoch_k_smooth': 60, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'COOKIEUSDT', 'ma_period': 149, 'stoch_k_period': 148, 'stoch_k_smooth': 80, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'BOMEUSDT', 'ma_period': 93, 'stoch_k_period': 148, 'stoch_k_smooth': 54, 'stoch_d_period': 37, 'leverage': 5},
    {'symbol': 'SWARMSUSDT', 'ma_period': 239, 'stoch_k_period': 149, 'stoch_k_smooth': 74, 'stoch_d_period': 22, 'leverage': 5},
    {'symbol': 'SYNUSDT', 'ma_period': 129, 'stoch_k_period': 107, 'stoch_k_smooth': 25, 'stoch_d_period': 25, 'leverage': 5},
    {'symbol': 'DEGENUSDT', 'ma_period': 86, 'stoch_k_period': 15, 'stoch_k_smooth': 12, 'stoch_d_period': 33, 'leverage': 4},
    {'symbol': 'HIVEUSDT', 'ma_period': 142, 'stoch_k_period': 150, 'stoch_k_smooth': 80, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'BIOUSDT', 'ma_period': 272, 'stoch_k_period': 39, 'stoch_k_smooth': 6, 'stoch_d_period': 45, 'leverage': 5},
    {'symbol': '1MBABYDOGEUSDT', 'ma_period': 76, 'stoch_k_period': 79, 'stoch_k_smooth': 9, 'stoch_d_period': 7, 'leverage': 5},
    {'symbol': 'ACXUSDT', 'ma_period': 146, 'stoch_k_period': 101, 'stoch_k_smooth': 35, 'stoch_d_period': 8, 'leverage': 5},
    {'symbol': 'SYSUSDT', 'ma_period': 162, 'stoch_k_period': 64, 'stoch_k_smooth': 5, 'stoch_d_period': 42, 'leverage': 5},
    {'symbol': 'VVVUSDT', 'ma_period': 129, 'stoch_k_period': 104, 'stoch_k_smooth': 73, 'stoch_d_period': 36, 'leverage': 5},
    {'symbol': 'HMSTRUSDT', 'ma_period': 222, 'stoch_k_period': 29, 'stoch_k_smooth': 24, 'stoch_d_period': 22, 'leverage': 5},
    {'symbol': 'NOTUSDT', 'ma_period': 40, 'stoch_k_period': 145, 'stoch_k_smooth': 67, 'stoch_d_period': 34, 'leverage': 5},
    {'symbol': 'GRIFFAINUSDT', 'ma_period': 91, 'stoch_k_period': 147, 'stoch_k_smooth': 5, 'stoch_d_period': 7, 'leverage': 3},
    {'symbol': 'KOMAUSDT', 'ma_period': 314, 'stoch_k_period': 150, 'stoch_k_smooth': 74, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'AVAAIUSDT', 'ma_period': 305, 'stoch_k_period': 94, 'stoch_k_smooth': 77, 'stoch_d_period': 39, 'leverage': 5},
    {'symbol': 'VANAUSDT', 'ma_period': 101, 'stoch_k_period': 150, 'stoch_k_smooth': 79, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'SAGAUSDT', 'ma_period': 234, 'stoch_k_period': 124, 'stoch_k_smooth': 79, 'stoch_d_period': 26, 'leverage': 5},
    {'symbol': 'PIXELUSDT', 'ma_period': 82, 'stoch_k_period': 146, 'stoch_k_smooth': 40, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'PROMUSDT', 'ma_period': 22, 'stoch_k_period': 147, 'stoch_k_smooth': 80, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'DRIFTUSDT', 'ma_period': 326, 'stoch_k_period': 150, 'stoch_k_smooth': 75, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'BRETTUSDT', 'ma_period': 250, 'stoch_k_period': 147, 'stoch_k_smooth': 68, 'stoch_d_period': 32, 'leverage': 4},
    {'symbol': 'POLUSDT', 'ma_period': 102, 'stoch_k_period': 15, 'stoch_k_smooth': 76, 'stoch_d_period': 22, 'leverage': 5},
    {'symbol': 'AKTUSDT', 'ma_period': 323, 'stoch_k_period': 100, 'stoch_k_smooth': 13, 'stoch_d_period': 13, 'leverage': 5},
    {'symbol': 'SCRUSDT', 'ma_period': 168, 'stoch_k_period': 133, 'stoch_k_smooth': 75, 'stoch_d_period': 30, 'leverage': 5},
    {'symbol': 'KAIAUSDT', 'ma_period': 23, 'stoch_k_period': 150, 'stoch_k_smooth': 80, 'stoch_d_period': 33, 'leverage': 5},
    {'symbol': 'SPXUSDT', 'ma_period': 62, 'stoch_k_period': 148, 'stoch_k_smooth': 75, 'stoch_d_period': 17, 'leverage': 4},
    {'symbol': 'FIDAUSDT', 'ma_period': 115, 'stoch_k_period': 91, 'stoch_k_smooth': 51, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'RPLUSDT', 'ma_period': 79, 'stoch_k_period': 87, 'stoch_k_smooth': 18, 'stoch_d_period': 28, 'leverage': 5},
    {'symbol': 'ANIMEUSDT', 'ma_period': 240, 'stoch_k_period': 150, 'stoch_k_smooth': 79, 'stoch_d_period': 30, 'leverage': 5},
    {'symbol': 'TURBOUSDT', 'ma_period': 68, 'stoch_k_period': 135, 'stoch_k_smooth': 79, 'stoch_d_period': 11, 'leverage': 5},
    {'symbol': 'KMNOUSDT', 'ma_period': 75, 'stoch_k_period': 149, 'stoch_k_smooth': 69, 'stoch_d_period': 38, 'leverage': 5},
    {'symbol': 'ENAUSDT', 'ma_period': 203, 'stoch_k_period': 144, 'stoch_k_smooth': 54, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'PIPPINUSDT', 'ma_period': 29, 'stoch_k_period': 147, 'stoch_k_smooth': 76, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'POPCATUSDT', 'ma_period': 213, 'stoch_k_period': 91, 'stoch_k_smooth': 45, 'stoch_d_period': 5, 'leverage': 3},
    {'symbol': 'ACTUSDT', 'ma_period': 171, 'stoch_k_period': 128, 'stoch_k_smooth': 16, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'NFPUSDT', 'ma_period': 132, 'stoch_k_period': 115, 'stoch_k_smooth': 80, 'stoch_d_period': 15, 'leverage': 5},
    {'symbol': 'ZETAUSDT', 'ma_period': 163, 'stoch_k_period': 46, 'stoch_k_smooth': 64, 'stoch_d_period': 10, 'leverage': 5},
    {'symbol': 'MOCAUSDT', 'ma_period': 150, 'stoch_k_period': 23, 'stoch_k_smooth': 16, 'stoch_d_period': 32, 'leverage': 5},
    {'symbol': 'AEVOUSDT', 'ma_period': 183, 'stoch_k_period': 121, 'stoch_k_smooth': 77, 'stoch_d_period': 13, 'leverage': 5},
    {'symbol': 'DEGOUSDT', 'ma_period': 287, 'stoch_k_period': 137, 'stoch_k_smooth': 7, 'stoch_d_period': 26, 'leverage': 5},
    {'symbol': 'USUALUSDT', 'ma_period': 322, 'stoch_k_period': 104, 'stoch_k_smooth': 80, 'stoch_d_period': 19, 'leverage': 5},
    {'symbol': 'IOUSDT', 'ma_period': 124, 'stoch_k_period': 132, 'stoch_k_smooth': 57, 'stoch_d_period': 40, 'leverage': 5},
    {'symbol': 'GRASSUSDT', 'ma_period': 213, 'stoch_k_period': 108, 'stoch_k_smooth': 73, 'stoch_d_period': 12, 'leverage': 5},
    {'symbol': 'RAREUSDT', 'ma_period': 171, 'stoch_k_period': 75, 'stoch_k_smooth': 77, 'stoch_d_period': 20, 'leverage': 5},
    {'symbol': 'HIPPOUSDT', 'ma_period': 87, 'stoch_k_period': 146, 'stoch_k_smooth': 73, 'stoch_d_period': 43, 'leverage': 5},
    {'symbol': 'ALTUSDT', 'ma_period': 148, 'stoch_k_period': 147, 'stoch_k_smooth': 51, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'PORTALUSDT', 'ma_period': 161, 'stoch_k_period': 142, 'stoch_k_smooth': 74, 'stoch_d_period': 31, 'leverage': 5},
    {'symbol': 'ORCAUSDT', 'ma_period': 198, 'stoch_k_period': 64, 'stoch_k_smooth': 59, 'stoch_d_period': 5, 'leverage': 4},
    {'symbol': 'MBOXUSDT', 'ma_period': 276, 'stoch_k_period': 77, 'stoch_k_smooth': 78, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'BANANAUSDT', 'ma_period': 270, 'stoch_k_period': 133, 'stoch_k_smooth': 63, 'stoch_d_period': 39, 'leverage': 5},
    {'symbol': 'RONINUSDT', 'ma_period': 67, 'stoch_k_period': 143, 'stoch_k_smooth': 58, 'stoch_d_period': 41, 'leverage': 5},
    {'symbol': 'RENDERUSDT', 'ma_period': 157, 'stoch_k_period': 44, 'stoch_k_smooth': 57, 'stoch_d_period': 13, 'leverage': 5},
    {'symbol': 'NTRNUSDT', 'ma_period': 85, 'stoch_k_period': 141, 'stoch_k_smooth': 50, 'stoch_d_period': 33, 'leverage': 5},
    {'symbol': 'AIUSDT', 'ma_period': 126, 'stoch_k_period': 118, 'stoch_k_smooth': 57, 'stoch_d_period': 28, 'leverage': 5},
    {'symbol': 'WUSDT', 'ma_period': 303, 'stoch_k_period': 138, 'stoch_k_smooth': 76, 'stoch_d_period': 4, 'leverage': 5},
    {'symbol': 'DYMUSDT', 'ma_period': 301, 'stoch_k_period': 150, 'stoch_k_smooth': 70, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': '1000WHYUSDT', 'ma_period': 114, 'stoch_k_period': 97, 'stoch_k_smooth': 56, 'stoch_d_period': 15, 'leverage': 4},
    {'symbol': 'BLURUSDT', 'ma_period': 126, 'stoch_k_period': 113, 'stoch_k_smooth': 52, 'stoch_d_period': 25, 'leverage': 5},
    {'symbol': 'LSKUSDT', 'ma_period': 80, 'stoch_k_period': 88, 'stoch_k_smooth': 80, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'CHILLGUYUSDT', 'ma_period': 126, 'stoch_k_period': 96, 'stoch_k_smooth': 76, 'stoch_d_period': 27, 'leverage': 5},
    {'symbol': 'BBUSDT', 'ma_period': 327, 'stoch_k_period': 150, 'stoch_k_smooth': 75, 'stoch_d_period': 25, 'leverage': 5},
    {'symbol': 'GUSDT', 'ma_period': 112, 'stoch_k_period': 93, 'stoch_k_smooth': 54, 'stoch_d_period': 8, 'leverage': 5},
    {'symbol': 'WIFUSDT', 'ma_period': 20, 'stoch_k_period': 107, 'stoch_k_smooth': 64, 'stoch_d_period': 16, 'leverage': 5},
    {'symbol': '1000CHEEMSUSDT', 'ma_period': 71, 'stoch_k_period': 150, 'stoch_k_smooth': 46, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'FLUXUSDT', 'ma_period': 75, 'stoch_k_period': 89, 'stoch_k_smooth': 57, 'stoch_d_period': 37, 'leverage': 5},
    {'symbol': 'DIAUSDT', 'ma_period': 92, 'stoch_k_period': 148, 'stoch_k_smooth': 56, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'METISUSDT', 'ma_period': 175, 'stoch_k_period': 137, 'stoch_k_smooth': 57, 'stoch_d_period': 23, 'leverage': 5},
    {'symbol': 'BICOUSDT', 'ma_period': 287, 'stoch_k_period': 120, 'stoch_k_smooth': 70, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'STRKUSDT', 'ma_period': 95, 'stoch_k_period': 119, 'stoch_k_smooth': 58, 'stoch_d_period': 34, 'leverage': 5},
    {'symbol': 'PYTHUSDT', 'ma_period': 171, 'stoch_k_period': 58, 'stoch_k_smooth': 77, 'stoch_d_period': 19, 'leverage': 5},
    {'symbol': 'COSUSDT', 'ma_period': 86, 'stoch_k_period': 102, 'stoch_k_smooth': 58, 'stoch_d_period': 39, 'leverage': 5},
    {'symbol': 'ETHWUSDT', 'ma_period': 96, 'stoch_k_period': 146, 'stoch_k_smooth': 58, 'stoch_d_period': 42, 'leverage': 5},
    {'symbol': 'TNSRUSDT', 'ma_period': 228, 'stoch_k_period': 104, 'stoch_k_smooth': 64, 'stoch_d_period': 37, 'leverage': 5},
    {'symbol': 'MEMEUSDT', 'ma_period': 97, 'stoch_k_period': 93, 'stoch_k_smooth': 66, 'stoch_d_period': 33, 'leverage': 5},
    {'symbol': 'LUMIAUSDT', 'ma_period': 296, 'stoch_k_period': 137, 'stoch_k_smooth': 52, 'stoch_d_period': 31, 'leverage': 5},
    {'symbol': 'SEIUSDT', 'ma_period': 192, 'stoch_k_period': 119, 'stoch_k_smooth': 66, 'stoch_d_period': 42, 'leverage': 5},
    {'symbol': 'REZUSDT', 'ma_period': 221, 'stoch_k_period': 16, 'stoch_k_smooth': 13, 'stoch_d_period': 34, 'leverage': 5},
    {'symbol': 'CATIUSDT', 'ma_period': 330, 'stoch_k_period': 87, 'stoch_k_smooth': 15, 'stoch_d_period': 17, 'leverage': 5},
    {'symbol': 'MOVRUSDT', 'ma_period': 258, 'stoch_k_period': 128, 'stoch_k_smooth': 53, 'stoch_d_period': 40, 'leverage': 5},
    {'symbol': 'BIGTIMEUSDT', 'ma_period': 163, 'stoch_k_period': 80, 'stoch_k_smooth': 57, 'stoch_d_period': 8, 'leverage': 5},
    {'symbol': 'AVAUSDT', 'ma_period': 127, 'stoch_k_period': 150, 'stoch_k_smooth': 80, 'stoch_d_period': 33, 'leverage': 4},
    {'symbol': 'MELANIAUSDT', 'ma_period': 143, 'stoch_k_period': 70, 'stoch_k_smooth': 12, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'MOODENGUSDT', 'ma_period': 245, 'stoch_k_period': 28, 'stoch_k_smooth': 12, 'stoch_d_period': 4, 'leverage': 2},
    {'symbol': 'NEIROUSDT', 'ma_period': 289, 'stoch_k_period': 104, 'stoch_k_smooth': 58, 'stoch_d_period': 6, 'leverage': 3},
    {'symbol': 'POLYXUSDT', 'ma_period': 238, 'stoch_k_period': 137, 'stoch_k_smooth': 68, 'stoch_d_period': 24, 'leverage': 5},
    {'symbol': 'IDUSDT', 'ma_period': 46, 'stoch_k_period': 144, 'stoch_k_smooth': 75, 'stoch_d_period': 28, 'leverage': 5},
    {'symbol': 'TONUSDT', 'ma_period': 130, 'stoch_k_period': 117, 'stoch_k_smooth': 80, 'stoch_d_period': 19, 'leverage': 5},
    {'symbol': 'SAFEUSDT', 'ma_period': 331, 'stoch_k_period': 14, 'stoch_k_smooth': 18, 'stoch_d_period': 36, 'leverage': 5},
    {'symbol': 'WAXPUSDT', 'ma_period': 135, 'stoch_k_period': 142, 'stoch_k_smooth': 80, 'stoch_d_period': 15, 'leverage': 5},
    {'symbol': 'FIOUSDT', 'ma_period': 194, 'stoch_k_period': 123, 'stoch_k_smooth': 30, 'stoch_d_period': 10, 'leverage': 5},
    {'symbol': 'XAIUSDT', 'ma_period': 113, 'stoch_k_period': 52, 'stoch_k_smooth': 71, 'stoch_d_period': 24, 'leverage': 5},
    {'symbol': 'ILVUSDT', 'ma_period': 36, 'stoch_k_period': 112, 'stoch_k_smooth': 55, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'HFTUSDT', 'ma_period': 331, 'stoch_k_period': 148, 'stoch_k_smooth': 78, 'stoch_d_period': 43, 'leverage': 5},
    {'symbol': '1000FLOKIUSDT', 'ma_period': 118, 'stoch_k_period': 93, 'stoch_k_smooth': 78, 'stoch_d_period': 43, 'leverage': 5},
    {'symbol': 'STEEMUSDT', 'ma_period': 49, 'stoch_k_period': 115, 'stoch_k_smooth': 53, 'stoch_d_period': 36, 'leverage': 5},
    {'symbol': 'ACEUSDT', 'ma_period': 191, 'stoch_k_period': 70, 'stoch_k_smooth': 65, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'ARKMUSDT', 'ma_period': 233, 'stoch_k_period': 38, 'stoch_k_smooth': 25, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'CAKEUSDT', 'ma_period': 35, 'stoch_k_period': 109, 'stoch_k_smooth': 80, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'ETHFIUSDT', 'ma_period': 350, 'stoch_k_period': 146, 'stoch_k_smooth': 45, 'stoch_d_period': 38, 'leverage': 5},
    {'symbol': 'ARBUSDT', 'ma_period': 221, 'stoch_k_period': 150, 'stoch_k_smooth': 74, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'BEAMXUSDT', 'ma_period': 126, 'stoch_k_period': 89, 'stoch_k_smooth': 70, 'stoch_d_period': 44, 'leverage': 5},
    {'symbol': 'THEUSDT', 'ma_period': 78, 'stoch_k_period': 112, 'stoch_k_smooth': 71, 'stoch_d_period': 40, 'leverage': 5},
    {'symbol': '1000BONKUSDT', 'ma_period': 24, 'stoch_k_period': 146, 'stoch_k_smooth': 71, 'stoch_d_period': 48, 'leverage': 4},
    {'symbol': 'DOGSUSDT', 'ma_period': 37, 'stoch_k_period': 25, 'stoch_k_smooth': 24, 'stoch_d_period': 50, 'leverage': 4},
    {'symbol': 'CYBERUSDT', 'ma_period': 338, 'stoch_k_period': 135, 'stoch_k_smooth': 75, 'stoch_d_period': 35, 'leverage': 5},
    {'symbol': 'LISTAUSDT', 'ma_period': 67, 'stoch_k_period': 27, 'stoch_k_smooth': 25, 'stoch_d_period': 50, 'leverage': 4},
    {'symbol': 'BNTUSDT', 'ma_period': 292, 'stoch_k_period': 120, 'stoch_k_smooth': 80, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'OXTUSDT', 'ma_period': 283, 'stoch_k_period': 138, 'stoch_k_smooth': 60, 'stoch_d_period': 38, 'leverage': 5},
    {'symbol': 'RIFUSDT', 'ma_period': 129, 'stoch_k_period': 139, 'stoch_k_smooth': 45, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'FLOWUSDT', 'ma_period': 109, 'stoch_k_period': 106, 'stoch_k_smooth': 45, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'POWRUSDT', 'ma_period': 183, 'stoch_k_period': 146, 'stoch_k_smooth': 69, 'stoch_d_period': 25, 'leverage': 5},
    {'symbol': 'SUIUSDT', 'ma_period': 332, 'stoch_k_period': 146, 'stoch_k_smooth': 77, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'ORDIUSDT', 'ma_period': 144, 'stoch_k_period': 85, 'stoch_k_smooth': 77, 'stoch_d_period': 42, 'leverage': 5},
    {'symbol': 'APEUSDT', 'ma_period': 145, 'stoch_k_period': 129, 'stoch_k_smooth': 79, 'stoch_d_period': 18, 'leverage': 5},
    {'symbol': 'USTCUSDT', 'ma_period': 141, 'stoch_k_period': 144, 'stoch_k_smooth': 55, 'stoch_d_period': 10, 'leverage': 5},
    {'symbol': 'COWUSDT', 'ma_period': 338, 'stoch_k_period': 147, 'stoch_k_smooth': 53, 'stoch_d_period': 43, 'leverage': 5},
    {'symbol': 'MANAUSDT', 'ma_period': 168, 'stoch_k_period': 132, 'stoch_k_smooth': 73, 'stoch_d_period': 42, 'leverage': 5},
    {'symbol': 'YGGUSDT', 'ma_period': 207, 'stoch_k_period': 142, 'stoch_k_smooth': 39, 'stoch_d_period': 22, 'leverage': 5},
    {'symbol': 'HIGHUSDT', 'ma_period': 289, 'stoch_k_period': 145, 'stoch_k_smooth': 63, 'stoch_d_period': 44, 'leverage': 5},
    {'symbol': 'ONDOUSDT', 'ma_period': 313, 'stoch_k_period': 89, 'stoch_k_smooth': 80, 'stoch_d_period': 27, 'leverage': 5},
    {'symbol': 'SUNUSDT', 'ma_period': 160, 'stoch_k_period': 137, 'stoch_k_smooth': 77, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'LUNA2USDT', 'ma_period': 237, 'stoch_k_period': 137, 'stoch_k_smooth': 10, 'stoch_d_period': 25, 'leverage': 5},
    {'symbol': 'ZROUSDT', 'ma_period': 116, 'stoch_k_period': 84, 'stoch_k_smooth': 50, 'stoch_d_period': 29, 'leverage': 5},
    {'symbol': 'MAVUSDT', 'ma_period': 25, 'stoch_k_period': 73, 'stoch_k_smooth': 40, 'stoch_d_period': 17, 'leverage': 3},
    {'symbol': 'MAGICUSDT', 'ma_period': 275, 'stoch_k_period': 144, 'stoch_k_smooth': 72, 'stoch_d_period': 34, 'leverage': 5},
    {'symbol': 'AXLUSDT', 'ma_period': 346, 'stoch_k_period': 130, 'stoch_k_smooth': 59, 'stoch_d_period': 34, 'leverage': 5},
    {'symbol': '1000PEPEUSDT', 'ma_period': 48, 'stoch_k_period': 25, 'stoch_k_smooth': 24, 'stoch_d_period': 31, 'leverage': 5},
    {'symbol': 'GALAUSDT', 'ma_period': 57, 'stoch_k_period': 139, 'stoch_k_smooth': 33, 'stoch_d_period': 10, 'leverage': 4},
    {'symbol': 'ONEUSDT', 'ma_period': 123, 'stoch_k_period': 78, 'stoch_k_smooth': 50, 'stoch_d_period': 44, 'leverage': 4},
    {'symbol': 'JTOUSDT', 'ma_period': 126, 'stoch_k_period': 145, 'stoch_k_smooth': 78, 'stoch_d_period': 25, 'leverage': 3},
    {'symbol': 'AUCTIONUSDT', 'ma_period': 53, 'stoch_k_period': 34, 'stoch_k_smooth': 41, 'stoch_d_period': 8, 'leverage': 3},
    {'symbol': 'ALCHUSDT', 'ma_period': 298, 'stoch_k_period': 48, 'stoch_k_smooth': 7, 'stoch_d_period': 41, 'leverage': 5},
    {'symbol': 'ASTRUSDT', 'ma_period': 36, 'stoch_k_period': 60, 'stoch_k_smooth': 69, 'stoch_d_period': 39, 'leverage': 5},
    {'symbol': 'GMTUSDT', 'ma_period': 107, 'stoch_k_period': 140, 'stoch_k_smooth': 67, 'stoch_d_period': 47, 'leverage': 5},
    {'symbol': 'OGNUSDT', 'ma_period': 118, 'stoch_k_period': 119, 'stoch_k_smooth': 73, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'WLDUSDT', 'ma_period': 253, 'stoch_k_period': 125, 'stoch_k_smooth': 16, 'stoch_d_period': 5, 'leverage': 2},
    {'symbol': 'RDNTUSDT', 'ma_period': 244, 'stoch_k_period': 63, 'stoch_k_smooth': 14, 'stoch_d_period': 6, 'leverage': 3},
    {'symbol': 'TIAUSDT', 'ma_period': 279, 'stoch_k_period': 95, 'stoch_k_smooth': 36, 'stoch_d_period': 7, 'leverage': 3},
    {'symbol': 'ZEREBROUSDT', 'ma_period': 296, 'stoch_k_period': 70, 'stoch_k_smooth': 39, 'stoch_d_period': 36, 'leverage': 2},
    {'symbol': 'JOEUSDT', 'ma_period': 245, 'stoch_k_period': 144, 'stoch_k_smooth': 40, 'stoch_d_period': 32, 'leverage': 5},
    {'symbol': 'SANDUSDT', 'ma_period': 125, 'stoch_k_period': 140, 'stoch_k_smooth': 72, 'stoch_d_period': 45, 'leverage': 5},
    {'symbol': 'AGLDUSDT', 'ma_period': 116, 'stoch_k_period': 148, 'stoch_k_smooth': 75, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'KASUSDT', 'ma_period': 78, 'stoch_k_period': 98, 'stoch_k_smooth': 75, 'stoch_d_period': 40, 'leverage': 4},
    {'symbol': 'OPUSDT', 'ma_period': 343, 'stoch_k_period': 148, 'stoch_k_smooth': 76, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'MANTAUSDT', 'ma_period': 237, 'stoch_k_period': 81, 'stoch_k_smooth': 39, 'stoch_d_period': 10, 'leverage': 5},
    {'symbol': 'PENDLEUSDT', 'ma_period': 153, 'stoch_k_period': 88, 'stoch_k_smooth': 56, 'stoch_d_period': 32, 'leverage': 4},
    {'symbol': 'STXUSDT', 'ma_period': 93, 'stoch_k_period': 148, 'stoch_k_smooth': 74, 'stoch_d_period': 48, 'leverage': 4},
    {'symbol': 'HBARUSDT', 'ma_period': 129, 'stoch_k_period': 102, 'stoch_k_smooth': 76, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'HOOKUSDT', 'ma_period': 321, 'stoch_k_period': 135, 'stoch_k_smooth': 71, 'stoch_d_period': 44, 'leverage': 4},
    {'symbol': 'ICXUSDT', 'ma_period': 120, 'stoch_k_period': 148, 'stoch_k_smooth': 62, 'stoch_d_period': 30, 'leverage': 5},
    {'symbol': 'MINAUSDT', 'ma_period': 346, 'stoch_k_period': 55, 'stoch_k_smooth': 32, 'stoch_d_period': 9, 'leverage': 4},
    {'symbol': 'BSVUSDT', 'ma_period': 38, 'stoch_k_period': 92, 'stoch_k_smooth': 37, 'stoch_d_period': 39, 'leverage': 5},
    {'symbol': '1000LUNCUSDT', 'ma_period': 126, 'stoch_k_period': 121, 'stoch_k_smooth': 38, 'stoch_d_period': 12, 'leverage': 3},
    {'symbol': 'CKBUSDT', 'ma_period': 315, 'stoch_k_period': 127, 'stoch_k_smooth': 80, 'stoch_d_period': 9, 'leverage': 5},
    {'symbol': 'FETUSDT', 'ma_period': 132, 'stoch_k_period': 150, 'stoch_k_smooth': 70, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'PHBUSDT', 'ma_period': 144, 'stoch_k_period': 149, 'stoch_k_smooth': 76, 'stoch_d_period': 22, 'leverage': 3},
    {'symbol': 'SANTOSUSDT', 'ma_period': 179, 'stoch_k_period': 103, 'stoch_k_smooth': 62, 'stoch_d_period': 39, 'leverage': 5},
    {'symbol': 'SUSHIUSDT', 'ma_period': 221, 'stoch_k_period': 108, 'stoch_k_smooth': 60, 'stoch_d_period': 14, 'leverage': 3},
    {'symbol': 'EDUUSDT', 'ma_period': 40, 'stoch_k_period': 150, 'stoch_k_smooth': 60, 'stoch_d_period': 31, 'leverage': 3},
    {'symbol': 'JUPUSDT', 'ma_period': 24, 'stoch_k_period': 104, 'stoch_k_smooth': 61, 'stoch_d_period': 30, 'leverage': 5},
    {'symbol': '1000SATSUSDT', 'ma_period': 344, 'stoch_k_period': 99, 'stoch_k_smooth': 34, 'stoch_d_period': 7, 'leverage': 3},
    {'symbol': 'ONGUSDT', 'ma_period': 312, 'stoch_k_period': 122, 'stoch_k_smooth': 6, 'stoch_d_period': 11, 'leverage': 5},
    {'symbol': 'IMXUSDT', 'ma_period': 131, 'stoch_k_period': 105, 'stoch_k_smooth': 53, 'stoch_d_period': 37, 'leverage': 5},
    {'symbol': 'AXSUSDT', 'ma_period': 198, 'stoch_k_period': 90, 'stoch_k_smooth': 63, 'stoch_d_period': 12, 'leverage': 3},
    {'symbol': 'MORPHOUSDT', 'ma_period': 20, 'stoch_k_period': 125, 'stoch_k_smooth': 16, 'stoch_d_period': 6, 'leverage': 1},
    {'symbol': 'WOOUSDT', 'ma_period': 199, 'stoch_k_period': 150, 'stoch_k_smooth': 76, 'stoch_d_period': 39, 'leverage': 3},
    {'symbol': 'API3USDT', 'ma_period': 226, 'stoch_k_period': 147, 'stoch_k_smooth': 50, 'stoch_d_period': 43, 'leverage': 4},
    {'symbol': '1000XECUSDT', 'ma_period': 232, 'stoch_k_period': 145, 'stoch_k_smooth': 32, 'stoch_d_period': 4, 'leverage': 3},
    {'symbol': 'SKLUSDT', 'ma_period': 210, 'stoch_k_period': 125, 'stoch_k_smooth': 20, 'stoch_d_period': 35, 'leverage': 3},
    {'symbol': 'C98USDT', 'ma_period': 210, 'stoch_k_period': 29, 'stoch_k_smooth': 76, 'stoch_d_period': 47, 'leverage': 3},
    {'symbol': 'IOSTUSDT', 'ma_period': 317, 'stoch_k_period': 90, 'stoch_k_smooth': 77, 'stoch_d_period': 50, 'leverage': 5},
    {'symbol': 'CTSIUSDT', 'ma_period': 220, 'stoch_k_period': 146, 'stoch_k_smooth': 64, 'stoch_d_period': 46, 'leverage': 5},
    {'symbol': 'ENJUSDT', 'ma_period': 234, 'stoch_k_period': 83, 'stoch_k_smooth': 14, 'stoch_d_period': 12, 'leverage': 3},
    {'symbol': 'CFXUSDT', 'ma_period': 253, 'stoch_k_period': 149, 'stoch_k_smooth': 72, 'stoch_d_period': 41, 'leverage': 5},
    {'symbol': 'IOTXUSDT', 'ma_period': 336, 'stoch_k_period': 144, 'stoch_k_smooth': 67, 'stoch_d_period': 45, 'leverage': 4},
    {'symbol': 'VETUSDT', 'ma_period': 202, 'stoch_k_period': 116, 'stoch_k_smooth': 73, 'stoch_d_period': 32, 'leverage': 4},
    {'symbol': '1000SHIBUSDT', 'ma_period': 96, 'stoch_k_period': 82, 'stoch_k_smooth': 60, 'stoch_d_period': 23, 'leverage': 3},
    {'symbol': 'XVGUSDT', 'ma_period': 70, 'stoch_k_period': 121, 'stoch_k_smooth': 43, 'stoch_d_period': 18, 'leverage': 4},
    {'symbol': 'GLMUSDT', 'ma_period': 86, 'stoch_k_period': 77, 'stoch_k_smooth': 29, 'stoch_d_period': 17, 'leverage': 3},
    {'symbol': 'ANKRUSDT', 'ma_period': 343, 'stoch_k_period': 148, 'stoch_k_smooth': 66, 'stoch_d_period': 35, 'leverage': 4},
    {'symbol': 'RVNUSDT', 'ma_period': 165, 'stoch_k_period': 146, 'stoch_k_smooth': 46, 'stoch_d_period': 22, 'leverage': 3},
    {'symbol': 'ROSEUSDT', 'ma_period': 55, 'stoch_k_period': 34, 'stoch_k_smooth': 7, 'stoch_d_period': 40, 'leverage': 3},
    {'symbol': 'AVAXUSDT', 'ma_period': 101, 'stoch_k_period': 28, 'stoch_k_smooth': 23, 'stoch_d_period': 31, 'leverage': 4},
    {'symbol': 'KSMUSDT', 'ma_period': 115, 'stoch_k_period': 113, 'stoch_k_smooth': 39, 'stoch_d_period': 50, 'leverage': 3},
    {'symbol': 'HOTUSDT', 'ma_period': 181, 'stoch_k_period': 139, 'stoch_k_smooth': 57, 'stoch_d_period': 33, 'leverage': 3},
    {'symbol': 'ENSUSDT', 'ma_period': 290, 'stoch_k_period': 41, 'stoch_k_smooth': 57, 'stoch_d_period': 49, 'leverage': 3},
    {'symbol': 'TUSDT', 'ma_period': 124, 'stoch_k_period': 146, 'stoch_k_smooth': 80, 'stoch_d_period': 31, 'leverage': 3},
    {'symbol': 'IOTAUSDT', 'ma_period': 147, 'stoch_k_period': 131, 'stoch_k_smooth': 59, 'stoch_d_period': 47, 'leverage': 4},
    {'symbol': 'GTCUSDT', 'ma_period': 219, 'stoch_k_period': 134, 'stoch_k_smooth': 76, 'stoch_d_period': 35, 'leverage': 5},
    {'symbol': 'NEARUSDT', 'ma_period': 174, 'stoch_k_period': 81, 'stoch_k_smooth': 70, 'stoch_d_period': 26, 'leverage': 3},
    {'symbol': 'TWTUSDT', 'ma_period': 34, 'stoch_k_period': 85, 'stoch_k_smooth': 80, 'stoch_d_period': 19, 'leverage': 3},
    {'symbol': 'SPELLUSDT', 'ma_period': 236, 'stoch_k_period': 104, 'stoch_k_smooth': 57, 'stoch_d_period': 40, 'leverage': 5},
    {'symbol': 'RSRUSDT', 'ma_period': 84, 'stoch_k_period': 115, 'stoch_k_smooth': 62, 'stoch_d_period': 20, 'leverage': 3},
    {'symbol': 'FILUSDT', 'ma_period': 308, 'stoch_k_period': 17, 'stoch_k_smooth': 37, 'stoch_d_period': 7, 'leverage': 3},
    {'symbol': 'TRUUSDT', 'ma_period': 273, 'stoch_k_period': 143, 'stoch_k_smooth': 71, 'stoch_d_period': 34, 'leverage': 5},
    {'symbol': 'GASUSDT', 'ma_period': 52, 'stoch_k_period': 134, 'stoch_k_smooth': 55, 'stoch_d_period': 24, 'leverage': 3},
    {'symbol': 'SNXUSDT', 'ma_period': 38, 'stoch_k_period': 130, 'stoch_k_smooth': 57, 'stoch_d_period': 16, 'leverage': 2},
    {'symbol': 'SUPERUSDT', 'ma_period': 26, 'stoch_k_period': 150, 'stoch_k_smooth': 55, 'stoch_d_period': 44, 'leverage': 3},
    {'symbol': 'LDOUSDT', 'ma_period': 54, 'stoch_k_period': 134, 'stoch_k_smooth': 80, 'stoch_d_period': 7, 'leverage': 3},
    {'symbol': 'GMXUSDT', 'ma_period': 88, 'stoch_k_period': 99, 'stoch_k_smooth': 75, 'stoch_d_period': 45, 'leverage': 2},
    {'symbol': 'ZRXUSDT', 'ma_period': 191, 'stoch_k_period': 122, 'stoch_k_smooth': 57, 'stoch_d_period': 40, 'leverage': 4},
    {'symbol': 'ATAUSDT', 'ma_period': 94, 'stoch_k_period': 143, 'stoch_k_smooth': 78, 'stoch_d_period': 33, 'leverage': 4},
    {'symbol': 'XVSUSDT', 'ma_period': 65, 'stoch_k_period': 140, 'stoch_k_smooth': 54, 'stoch_d_period': 22, 'leverage': 2},
    {'symbol': 'LPTUSDT', 'ma_period': 95, 'stoch_k_period': 63, 'stoch_k_smooth': 37, 'stoch_d_period': 21, 'leverage': 2},
    {'symbol': 'EGLDUSDT', 'ma_period': 82, 'stoch_k_period': 139, 'stoch_k_smooth': 22, 'stoch_d_period': 39, 'leverage': 3},
    {'symbol': 'CELOUSDT', 'ma_period': 246, 'stoch_k_period': 121, 'stoch_k_smooth': 79, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'NMRUSDT', 'ma_period': 348, 'stoch_k_period': 137, 'stoch_k_smooth': 78, 'stoch_d_period': 25, 'leverage': 4},
    {'symbol': 'DYDXUSDT', 'ma_period': 77, 'stoch_k_period': 34, 'stoch_k_smooth': 17, 'stoch_d_period': 31, 'leverage': 4},
    {'symbol': 'QNTUSDT', 'ma_period': 288, 'stoch_k_period': 142, 'stoch_k_smooth': 54, 'stoch_d_period': 38, 'leverage': 4},
    {'symbol': 'ARKUSDT', 'ma_period': 341, 'stoch_k_period': 149, 'stoch_k_smooth': 74, 'stoch_d_period': 44, 'leverage': 4},
    {'symbol': 'RUNEUSDT', 'ma_period': 272, 'stoch_k_period': 132, 'stoch_k_smooth': 28, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'APTUSDT', 'ma_period': 350, 'stoch_k_period': 117, 'stoch_k_smooth': 53, 'stoch_d_period': 24, 'leverage': 4},
    {'symbol': 'XTZUSDT', 'ma_period': 350, 'stoch_k_period': 117, 'stoch_k_smooth': 58, 'stoch_d_period': 28, 'leverage': 4},
    {'symbol': 'ARCUSDT', 'ma_period': 69, 'stoch_k_period': 84, 'stoch_k_smooth': 7, 'stoch_d_period': 46, 'leverage': 2},
    {'symbol': 'ZILUSDT', 'ma_period': 319, 'stoch_k_period': 67, 'stoch_k_smooth': 20, 'stoch_d_period': 8, 'leverage': 3},
    {'symbol': 'ARUSDT', 'ma_period': 292, 'stoch_k_period': 121, 'stoch_k_smooth': 7, 'stoch_d_period': 10, 'leverage': 4},
    {'symbol': 'YFIUSDT', 'ma_period': 218, 'stoch_k_period': 140, 'stoch_k_smooth': 29, 'stoch_d_period': 28, 'leverage': 4},
    {'symbol': 'ALGOUSDT', 'ma_period': 347, 'stoch_k_period': 149, 'stoch_k_smooth': 12, 'stoch_d_period': 26, 'leverage': 3},
    {'symbol': 'DODOXUSDT', 'ma_period': 212, 'stoch_k_period': 125, 'stoch_k_smooth': 32, 'stoch_d_period': 36, 'leverage': 4},
    {'symbol': 'ONTUSDT', 'ma_period': 127, 'stoch_k_period': 150, 'stoch_k_smooth': 60, 'stoch_d_period': 42, 'leverage': 3},
    {'symbol': 'TAOUSDT', 'ma_period': 203, 'stoch_k_period': 47, 'stoch_k_smooth': 29, 'stoch_d_period': 3, 'leverage': 2},
    {'symbol': 'ALICEUSDT', 'ma_period': 94, 'stoch_k_period': 54, 'stoch_k_smooth': 7, 'stoch_d_period': 24, 'leverage': 2},
    {'symbol': 'COTIUSDT', 'ma_period': 289, 'stoch_k_period': 116, 'stoch_k_smooth': 32, 'stoch_d_period': 6, 'leverage': 2},
    {'symbol': 'LRCUSDT', 'ma_period': 201, 'stoch_k_period': 144, 'stoch_k_smooth': 35, 'stoch_d_period': 50, 'leverage': 3},
    {'symbol': 'CELRUSDT', 'ma_period': 91, 'stoch_k_period': 150, 'stoch_k_smooth': 32, 'stoch_d_period': 47, 'leverage': 2},
    {'symbol': 'NEOUSDT', 'ma_period': 348, 'stoch_k_period': 30, 'stoch_k_smooth': 46, 'stoch_d_period': 36, 'leverage': 5},
    {'symbol': 'KNCUSDT', 'ma_period': 77, 'stoch_k_period': 136, 'stoch_k_smooth': 32, 'stoch_d_period': 3, 'leverage': 2},
    {'symbol': '1INCHUSDT', 'ma_period': 202, 'stoch_k_period': 106, 'stoch_k_smooth': 59, 'stoch_d_period': 30, 'leverage': 2},
    {'symbol': 'MASKUSDT', 'ma_period': 125, 'stoch_k_period': 143, 'stoch_k_smooth': 68, 'stoch_d_period': 20, 'leverage': 4},
    {'symbol': 'QTUMUSDT', 'ma_period': 222, 'stoch_k_period': 98, 'stoch_k_smooth': 50, 'stoch_d_period': 49, 'leverage': 2},
    {'symbol': 'TRBUSDT', 'ma_period': 160, 'stoch_k_period': 102, 'stoch_k_smooth': 53, 'stoch_d_period': 12, 'leverage': 2},
    {'symbol': 'THETAUSDT', 'ma_period': 330, 'stoch_k_period': 118, 'stoch_k_smooth': 73, 'stoch_d_period': 24, 'leverage': 3},
    {'symbol': 'ETCUSDT', 'ma_period': 335, 'stoch_k_period': 103, 'stoch_k_smooth': 67, 'stoch_d_period': 49, 'leverage': 5},
    {'symbol': 'DOTUSDT', 'ma_period': 209, 'stoch_k_period': 68, 'stoch_k_smooth': 44, 'stoch_d_period': 3, 'leverage': 2},
    {'symbol': 'STORJUSDT', 'ma_period': 79, 'stoch_k_period': 44, 'stoch_k_smooth': 47, 'stoch_d_period': 15, 'leverage': 2},
    {'symbol': 'ICPUSDT', 'ma_period': 147, 'stoch_k_period': 133, 'stoch_k_smooth': 34, 'stoch_d_period': 32, 'leverage': 4},
    {'symbol': 'LQTYUSDT', 'ma_period': 77, 'stoch_k_period': 75, 'stoch_k_smooth': 59, 'stoch_d_period': 30, 'leverage': 2},
    {'symbol': 'DUSKUSDT', 'ma_period': 108, 'stoch_k_period': 109, 'stoch_k_smooth': 64, 'stoch_d_period': 39, 'leverage': 2},
    {'symbol': 'ACHUSDT', 'ma_period': 63, 'stoch_k_period': 129, 'stoch_k_smooth': 80, 'stoch_d_period': 35, 'leverage': 1},
    {'symbol': 'AAVEUSDT', 'ma_period': 131, 'stoch_k_period': 131, 'stoch_k_smooth': 76, 'stoch_d_period': 29, 'leverage': 3},
    {'symbol': 'XLMUSDT', 'ma_period': 335, 'stoch_k_period': 84, 'stoch_k_smooth': 74, 'stoch_d_period': 48, 'leverage': 5},
    {'symbol': 'COMPUSDT', 'ma_period': 313, 'stoch_k_period': 93, 'stoch_k_smooth': 37, 'stoch_d_period': 20, 'leverage': 2},
    {'symbol': 'STGUSDT', 'ma_period': 200, 'stoch_k_period': 94, 'stoch_k_smooth': 33, 'stoch_d_period': 23, 'leverage': 5},
    {'symbol': 'BELUSDT', 'ma_period': 223, 'stoch_k_period': 135, 'stoch_k_smooth': 8, 'stoch_d_period': 11, 'leverage': 1},
    {'symbol': 'JASMYUSDT', 'ma_period': 41, 'stoch_k_period': 54, 'stoch_k_smooth': 24, 'stoch_d_period': 28, 'leverage': 1},
    {'symbol': 'GRTUSDT', 'ma_period': 135, 'stoch_k_period': 32, 'stoch_k_smooth': 75, 'stoch_d_period': 40, 'leverage': 3},
    {'symbol': 'ATOMUSDT', 'ma_period': 72, 'stoch_k_period': 101, 'stoch_k_smooth': 80, 'stoch_d_period': 37, 'leverage': 1},
    {'symbol': 'SCRTUSDT', 'ma_period': 72, 'stoch_k_period': 49, 'stoch_k_smooth': 5, 'stoch_d_period': 39, 'leverage': 1},
    {'symbol': 'DENTUSDT', 'ma_period': 148, 'stoch_k_period': 76, 'stoch_k_smooth': 65, 'stoch_d_period': 17, 'leverage': 2},
    {'symbol': 'TLMUSDT', 'ma_period': 93, 'stoch_k_period': 139, 'stoch_k_smooth': 59, 'stoch_d_period': 36, 'leverage': 1},
    {'symbol': '1000RATSUSDT', 'ma_period': 28, 'stoch_k_period': 93, 'stoch_k_smooth': 61, 'stoch_d_period': 7, 'leverage': 1},
    {'symbol': 'CHZUSDT', 'ma_period': 278, 'stoch_k_period': 132, 'stoch_k_smooth': 11, 'stoch_d_period': 15, 'leverage': 1},
    {'symbol': 'ZENUSDT', 'ma_period': 298, 'stoch_k_period': 126, 'stoch_k_smooth': 11, 'stoch_d_period': 17, 'leverage': 1},
    {'symbol': 'CHRUSDT', 'ma_period': 100, 'stoch_k_period': 115, 'stoch_k_smooth': 68, 'stoch_d_period': 46, 'leverage': 2},
    {'symbol': 'INJUSDT', 'ma_period': 337, 'stoch_k_period': 21, 'stoch_k_smooth': 56, 'stoch_d_period': 12, 'leverage': 1},
    {'symbol': 'SSVUSDT', 'ma_period': 200, 'stoch_k_period': 140, 'stoch_k_smooth': 62, 'stoch_d_period': 30, 'leverage': 1},
    {'symbol': 'ZECUSDT', 'ma_period': 164, 'stoch_k_period': 52, 'stoch_k_smooth': 41, 'stoch_d_period': 15, 'leverage': 3},
    {'symbol': 'CRVUSDT', 'ma_period': 273, 'stoch_k_period': 137, 'stoch_k_smooth': 45, 'stoch_d_period': 27, 'leverage': 1},
]

# Futures 매매 제외 코인 (CSV 필터링 완료 - 별도 제외 불필요)
FUTURES_EXCLUDED_COINS = []

# ============================================================
# USDS-M Futures 롱 포지션 설정 (롱/숏 우선 최적화 반영 2026-03-14)
# ============================================================

LONG_TRADING_CONFIGS = [
    {'symbol': 'SOLVUSDT', 'short_ma': 242, 'short_sk': 148, 'short_sks': 78, 'short_sd': 42, 'long_ma': 143, 'long_sk': 144, 'long_sks': 60, 'long_sd': 31, 'long_lev': 1},
    {'symbol': 'RAYSOLUSDT', 'short_ma': 247, 'short_sk': 37, 'short_sks': 28, 'short_sd': 14, 'long_ma': 194, 'long_sk': 14, 'long_sks': 60, 'long_sd': 14, 'long_lev': 5},
    {'symbol': 'BERAUSDT', 'short_ma': 32, 'short_sk': 117, 'short_sks': 52, 'short_sd': 42, 'long_ma': 139, 'long_sk': 55, 'long_sks': 48, 'long_sd': 18, 'long_lev': 4},
    {'symbol': 'DUSDT', 'short_ma': 325, 'short_sk': 145, 'short_sks': 57, 'short_sd': 46, 'long_ma': 208, 'long_sk': 82, 'long_sks': 56, 'long_sd': 42, 'long_lev': 3},
    {'symbol': 'CGPTUSDT', 'short_ma': 112, 'short_sk': 150, 'short_sks': 60, 'short_sd': 27, 'long_ma': 36, 'long_sk': 134, 'long_sks': 76, 'long_sd': 50, 'long_lev': 3},
    {'symbol': '1000000MOGUSDT', 'short_ma': 144, 'short_sk': 115, 'short_sks': 77, 'short_sd': 46, 'long_ma': 298, 'long_sk': 52, 'long_sks': 15, 'long_sd': 11, 'long_lev': 5},
    {'symbol': 'VELODROMEUSDT', 'short_ma': 333, 'short_sk': 148, 'short_sks': 57, 'short_sd': 50, 'long_ma': 84, 'long_sk': 150, 'long_sks': 80, 'long_sd': 49, 'long_lev': 5},
    {'symbol': 'PENGUUSDT', 'short_ma': 101, 'short_sk': 90, 'short_sks': 77, 'short_sd': 19, 'long_ma': 74, 'long_sk': 138, 'long_sks': 24, 'long_sd': 32, 'long_lev': 5},
    {'symbol': 'AIXBTUSDT', 'short_ma': 154, 'short_sk': 109, 'short_sks': 15, 'short_sd': 9, 'long_ma': 39, 'long_sk': 139, 'long_sks': 80, 'long_sd': 35, 'long_lev': 5},
    {'symbol': 'MEUSDT', 'short_ma': 180, 'short_sk': 30, 'short_sks': 77, 'short_sd': 26, 'long_ma': 333, 'long_sk': 148, 'long_sks': 67, 'long_sd': 49, 'long_lev': 3},
    {'symbol': 'SONICUSDT', 'short_ma': 344, 'short_sk': 21, 'short_sks': 6, 'short_sd': 7, 'long_ma': 329, 'long_sk': 148, 'long_sks': 78, 'long_sd': 38, 'long_lev': 5},
    {'symbol': 'AEROUSDT', 'short_ma': 30, 'short_sk': 148, 'short_sks': 63, 'short_sd': 34, 'long_ma': 272, 'long_sk': 142, 'long_sks': 60, 'long_sd': 42, 'long_lev': 5},
    {'symbol': 'FARTCOINUSDT', 'short_ma': 188, 'short_sk': 123, 'short_sks': 61, 'short_sd': 6, 'long_ma': 74, 'long_sk': 90, 'long_sks': 80, 'long_sd': 22, 'long_lev': 4},
    {'symbol': 'CETUSUSDT', 'short_ma': 200, 'short_sk': 132, 'short_sks': 29, 'short_sd': 8, 'long_ma': 193, 'long_sk': 15, 'long_sks': 12, 'long_sd': 21, 'long_lev': 4},
    {'symbol': 'VTHOUSDT', 'short_ma': 310, 'short_sk': 136, 'short_sks': 59, 'short_sd': 49, 'long_ma': 129, 'long_sk': 52, 'long_sks': 19, 'long_sd': 20, 'long_lev': 5},
    {'symbol': 'PNUTUSDT', 'short_ma': 155, 'short_sk': 122, 'short_sks': 53, 'short_sd': 48, 'long_ma': 247, 'long_sk': 109, 'long_sks': 64, 'long_sd': 37, 'long_lev': 5},
    {'symbol': 'VINEUSDT', 'short_ma': 227, 'short_sk': 88, 'short_sks': 76, 'short_sd': 50, 'long_ma': 186, 'long_sk': 57, 'long_sks': 55, 'long_sd': 36, 'long_lev': 3},
    {'symbol': 'MEWUSDT', 'short_ma': 107, 'short_sk': 124, 'short_sks': 56, 'short_sd': 41, 'long_ma': 43, 'long_sk': 14, 'long_sks': 31, 'long_sd': 41, 'long_lev': 5},
    {'symbol': 'PHAUSDT', 'short_ma': 205, 'short_sk': 148, 'short_sks': 80, 'short_sd': 45, 'long_ma': 44, 'long_sk': 144, 'long_sks': 80, 'long_sd': 27, 'long_lev': 5},
    {'symbol': 'VIRTUALUSDT', 'short_ma': 156, 'short_sk': 108, 'short_sks': 36, 'short_sd': 47, 'long_ma': 127, 'long_sk': 112, 'long_sks': 52, 'long_sd': 50, 'long_lev': 5},
    {'symbol': 'TRUMPUSDT', 'short_ma': 54, 'short_sk': 144, 'short_sks': 79, 'short_sd': 17, 'long_ma': 86, 'long_sk': 122, 'long_sks': 66, 'long_sd': 32, 'long_lev': 5},
    {'symbol': '1000CATUSDT', 'short_ma': 41, 'short_sk': 42, 'short_sks': 13, 'short_sd': 16, 'long_ma': 195, 'long_sk': 67, 'long_sks': 75, 'long_sd': 29, 'long_lev': 5},
    {'symbol': 'ZKUSDT', 'short_ma': 84, 'short_sk': 95, 'short_sks': 57, 'short_sd': 41, 'long_ma': 58, 'long_sk': 80, 'long_sks': 15, 'long_sd': 46, 'long_lev': 5},
    {'symbol': 'DEXEUSDT', 'short_ma': 87, 'short_sk': 146, 'short_sks': 30, 'short_sd': 49, 'long_ma': 86, 'long_sk': 76, 'long_sks': 31, 'long_sd': 49, 'long_lev': 4},
    {'symbol': 'GOATUSDT', 'short_ma': 186, 'short_sk': 54, 'short_sks': 14, 'short_sd': 47, 'long_ma': 26, 'long_sk': 37, 'long_sks': 5, 'long_sd': 38, 'long_lev': 4},
    {'symbol': 'EIGENUSDT', 'short_ma': 35, 'short_sk': 150, 'short_sks': 78, 'short_sd': 47, 'long_ma': 216, 'long_sk': 71, 'long_sks': 57, 'long_sd': 42, 'long_lev': 5},
    {'symbol': 'VANRYUSDT', 'short_ma': 115, 'short_sk': 148, 'short_sks': 60, 'short_sd': 46, 'long_ma': 53, 'long_sk': 111, 'long_sks': 13, 'long_sd': 17, 'long_lev': 4},
    {'symbol': 'COOKIEUSDT', 'short_ma': 149, 'short_sk': 148, 'short_sks': 80, 'short_sd': 49, 'long_ma': 310, 'long_sk': 98, 'long_sks': 70, 'long_sd': 13, 'long_lev': 1},
    {'symbol': 'BOMEUSDT', 'short_ma': 93, 'short_sk': 148, 'short_sks': 54, 'short_sd': 37, 'long_ma': 142, 'long_sk': 30, 'long_sks': 40, 'long_sd': 9, 'long_lev': 3},
    {'symbol': 'SWARMSUSDT', 'short_ma': 239, 'short_sk': 149, 'short_sks': 74, 'short_sd': 22, 'long_ma': 29, 'long_sk': 42, 'long_sks': 70, 'long_sd': 11, 'long_lev': 1},
    {'symbol': 'SYNUSDT', 'short_ma': 129, 'short_sk': 107, 'short_sks': 25, 'short_sd': 25, 'long_ma': 40, 'long_sk': 44, 'long_sks': 25, 'long_sd': 16, 'long_lev': 4},
    {'symbol': 'DEGENUSDT', 'short_ma': 86, 'short_sk': 15, 'short_sks': 12, 'short_sd': 33, 'long_ma': 70, 'long_sk': 64, 'long_sks': 75, 'long_sd': 37, 'long_lev': 4},
    {'symbol': 'HIVEUSDT', 'short_ma': 142, 'short_sk': 150, 'short_sks': 80, 'short_sd': 50, 'long_ma': 77, 'long_sk': 91, 'long_sks': 5, 'long_sd': 9, 'long_lev': 5},
    {'symbol': 'BIOUSDT', 'short_ma': 272, 'short_sk': 39, 'short_sks': 6, 'short_sd': 45, 'long_ma': 20, 'long_sk': 141, 'long_sks': 60, 'long_sd': 17, 'long_lev': 4},
    {'symbol': '1MBABYDOGEUSDT', 'short_ma': 76, 'short_sk': 79, 'short_sks': 9, 'short_sd': 7, 'long_ma': 230, 'long_sk': 146, 'long_sks': 61, 'long_sd': 26, 'long_lev': 5},
    {'symbol': 'ACXUSDT', 'short_ma': 146, 'short_sk': 101, 'short_sks': 35, 'short_sd': 8, 'long_ma': 313, 'long_sk': 78, 'long_sks': 56, 'long_sd': 43, 'long_lev': 1},
    {'symbol': 'SYSUSDT', 'short_ma': 162, 'short_sk': 64, 'short_sks': 5, 'short_sd': 42, 'long_ma': 39, 'long_sk': 138, 'long_sks': 28, 'long_sd': 22, 'long_lev': 5},
    {'symbol': 'VVVUSDT', 'short_ma': 129, 'short_sk': 104, 'short_sks': 73, 'short_sd': 36, 'long_ma': 113, 'long_sk': 68, 'long_sks': 57, 'long_sd': 43, 'long_lev': 5},
    {'symbol': 'HMSTRUSDT', 'short_ma': 222, 'short_sk': 29, 'short_sks': 24, 'short_sd': 22, 'long_ma': 340, 'long_sk': 120, 'long_sks': 76, 'long_sd': 45, 'long_lev': 3},
    {'symbol': 'NOTUSDT', 'short_ma': 40, 'short_sk': 145, 'short_sks': 67, 'short_sd': 34, 'long_ma': 49, 'long_sk': 31, 'long_sks': 79, 'long_sd': 25, 'long_lev': 3},
    {'symbol': 'GRIFFAINUSDT', 'short_ma': 91, 'short_sk': 147, 'short_sks': 5, 'short_sd': 7, 'long_ma': 148, 'long_sk': 146, 'long_sks': 64, 'long_sd': 33, 'long_lev': 5},
    {'symbol': 'KOMAUSDT', 'short_ma': 314, 'short_sk': 150, 'short_sks': 74, 'short_sd': 46, 'long_ma': 88, 'long_sk': 37, 'long_sks': 62, 'long_sd': 42, 'long_lev': 5},
    {'symbol': 'AVAAIUSDT', 'short_ma': 305, 'short_sk': 94, 'short_sks': 77, 'short_sd': 39, 'long_ma': 339, 'long_sk': 51, 'long_sks': 63, 'long_sd': 16, 'long_lev': 1},
    {'symbol': 'SAGAUSDT', 'short_ma': 234, 'short_sk': 124, 'short_sks': 79, 'short_sd': 26, 'long_ma': 42, 'long_sk': 93, 'long_sks': 77, 'long_sd': 27, 'long_lev': 4},
    {'symbol': 'PIXELUSDT', 'short_ma': 82, 'short_sk': 146, 'short_sks': 40, 'short_sd': 48, 'long_ma': 100, 'long_sk': 68, 'long_sks': 79, 'long_sd': 22, 'long_lev': 4},
    {'symbol': 'PROMUSDT', 'short_ma': 22, 'short_sk': 147, 'short_sks': 80, 'short_sd': 47, 'long_ma': 332, 'long_sk': 28, 'long_sks': 53, 'long_sd': 45, 'long_lev': 5},
    {'symbol': 'DRIFTUSDT', 'short_ma': 326, 'short_sk': 150, 'short_sks': 75, 'short_sd': 47, 'long_ma': 23, 'long_sk': 18, 'long_sks': 78, 'long_sd': 36, 'long_lev': 3},
    {'symbol': 'BRETTUSDT', 'short_ma': 250, 'short_sk': 147, 'short_sks': 68, 'short_sd': 32, 'long_ma': 247, 'long_sk': 114, 'long_sks': 64, 'long_sd': 34, 'long_lev': 5},
    {'symbol': 'POLUSDT', 'short_ma': 102, 'short_sk': 15, 'short_sks': 76, 'short_sd': 22, 'long_ma': 275, 'long_sk': 32, 'long_sks': 14, 'long_sd': 16, 'long_lev': 5},
    {'symbol': 'AKTUSDT', 'short_ma': 323, 'short_sk': 100, 'short_sks': 13, 'short_sd': 13, 'long_ma': 61, 'long_sk': 15, 'long_sks': 18, 'long_sd': 35, 'long_lev': 4},
    {'symbol': 'SCRUSDT', 'short_ma': 168, 'short_sk': 133, 'short_sks': 75, 'short_sd': 30, 'long_ma': 56, 'long_sk': 113, 'long_sks': 65, 'long_sd': 25, 'long_lev': 5},
    {'symbol': 'KAIAUSDT', 'short_ma': 23, 'short_sk': 150, 'short_sks': 80, 'short_sd': 33, 'long_ma': 20, 'long_sk': 91, 'long_sks': 79, 'long_sd': 21, 'long_lev': 4},
    {'symbol': 'SPXUSDT', 'short_ma': 62, 'short_sk': 148, 'short_sks': 75, 'short_sd': 17, 'long_ma': 51, 'long_sk': 75, 'long_sks': 74, 'long_sd': 40, 'long_lev': 5},
    {'symbol': 'FIDAUSDT', 'short_ma': 115, 'short_sk': 91, 'short_sks': 51, 'short_sd': 49, 'long_ma': 293, 'long_sk': 18, 'long_sks': 12, 'long_sd': 24, 'long_lev': 4},
    {'symbol': 'RPLUSDT', 'short_ma': 79, 'short_sk': 87, 'short_sks': 18, 'short_sd': 28, 'long_ma': 350, 'long_sk': 33, 'long_sks': 32, 'long_sd': 45, 'long_lev': 3},
    {'symbol': 'ANIMEUSDT', 'short_ma': 240, 'short_sk': 150, 'short_sks': 79, 'short_sd': 30, 'long_ma': 277, 'long_sk': 16, 'long_sks': 19, 'long_sd': 21, 'long_lev': 5},
    {'symbol': 'TURBOUSDT', 'short_ma': 68, 'short_sk': 135, 'short_sks': 79, 'short_sd': 11, 'long_ma': 83, 'long_sk': 146, 'long_sks': 27, 'long_sd': 23, 'long_lev': 4},
    {'symbol': 'KMNOUSDT', 'short_ma': 75, 'short_sk': 149, 'short_sks': 69, 'short_sd': 38, 'long_ma': 289, 'long_sk': 30, 'long_sks': 21, 'long_sd': 24, 'long_lev': 5},
    {'symbol': 'ENAUSDT', 'short_ma': 203, 'short_sk': 144, 'short_sks': 54, 'short_sd': 48, 'long_ma': 232, 'long_sk': 16, 'long_sks': 28, 'long_sd': 25, 'long_lev': 3},
    {'symbol': 'PIPPINUSDT', 'short_ma': 29, 'short_sk': 147, 'short_sks': 76, 'short_sd': 47, 'long_ma': 124, 'long_sk': 78, 'long_sks': 35, 'long_sd': 7, 'long_lev': 3},
    {'symbol': 'POPCATUSDT', 'short_ma': 213, 'short_sk': 91, 'short_sks': 45, 'short_sd': 5, 'long_ma': 246, 'long_sk': 93, 'long_sks': 79, 'long_sd': 50, 'long_lev': 5},
    {'symbol': 'ACTUSDT', 'short_ma': 171, 'short_sk': 128, 'short_sks': 16, 'short_sd': 49, 'long_ma': 42, 'long_sk': 50, 'long_sks': 56, 'long_sd': 37, 'long_lev': 3},
    {'symbol': 'NFPUSDT', 'short_ma': 132, 'short_sk': 115, 'short_sks': 80, 'short_sd': 15, 'long_ma': 52, 'long_sk': 57, 'long_sks': 50, 'long_sd': 50, 'long_lev': 4},
    {'symbol': 'ZETAUSDT', 'short_ma': 163, 'short_sk': 46, 'short_sks': 64, 'short_sd': 10, 'long_ma': 35, 'long_sk': 46, 'long_sks': 45, 'long_sd': 26, 'long_lev': 1},
    {'symbol': 'MOCAUSDT', 'short_ma': 150, 'short_sk': 23, 'short_sks': 16, 'short_sd': 32, 'long_ma': 350, 'long_sk': 146, 'long_sks': 72, 'long_sd': 35, 'long_lev': 5},
    {'symbol': 'AEVOUSDT', 'short_ma': 183, 'short_sk': 121, 'short_sks': 77, 'short_sd': 13, 'long_ma': 20, 'long_sk': 64, 'long_sks': 80, 'long_sd': 24, 'long_lev': 1},
    {'symbol': 'DEGOUSDT', 'short_ma': 287, 'short_sk': 137, 'short_sks': 7, 'short_sd': 26, 'long_ma': 114, 'long_sk': 149, 'long_sks': 79, 'long_sd': 50, 'long_lev': 1},
    {'symbol': 'USUALUSDT', 'short_ma': 322, 'short_sk': 104, 'short_sks': 80, 'short_sd': 19, 'long_ma': 247, 'long_sk': 31, 'long_sks': 6, 'long_sd': 18, 'long_lev': 1},
    {'symbol': 'IOUSDT', 'short_ma': 124, 'short_sk': 132, 'short_sks': 57, 'short_sd': 40, 'long_ma': 213, 'long_sk': 77, 'long_sks': 50, 'long_sd': 34, 'long_lev': 4},
    {'symbol': 'RAREUSDT', 'short_ma': 171, 'short_sk': 75, 'short_sks': 77, 'short_sd': 20, 'long_ma': 36, 'long_sk': 102, 'long_sks': 35, 'long_sd': 10, 'long_lev': 5},
    {'symbol': 'HIPPOUSDT', 'short_ma': 87, 'short_sk': 146, 'short_sks': 73, 'short_sd': 43, 'long_ma': 91, 'long_sk': 92, 'long_sks': 50, 'long_sd': 43, 'long_lev': 3},
    {'symbol': 'ALTUSDT', 'short_ma': 148, 'short_sk': 147, 'short_sks': 51, 'short_sd': 48, 'long_ma': 24, 'long_sk': 109, 'long_sks': 72, 'long_sd': 5, 'long_lev': 1},
    {'symbol': 'PORTALUSDT', 'short_ma': 161, 'short_sk': 142, 'short_sks': 74, 'short_sd': 31, 'long_ma': 60, 'long_sk': 39, 'long_sks': 15, 'long_sd': 18, 'long_lev': 5},
    {'symbol': 'ORCAUSDT', 'short_ma': 198, 'short_sk': 64, 'short_sks': 59, 'short_sd': 5, 'long_ma': 28, 'long_sk': 122, 'long_sks': 68, 'long_sd': 49, 'long_lev': 4},
    {'symbol': 'MBOXUSDT', 'short_ma': 276, 'short_sk': 77, 'short_sks': 78, 'short_sd': 50, 'long_ma': 59, 'long_sk': 77, 'long_sks': 80, 'long_sd': 12, 'long_lev': 4},
    {'symbol': 'BANANAUSDT', 'short_ma': 270, 'short_sk': 133, 'short_sks': 63, 'short_sd': 39, 'long_ma': 46, 'long_sk': 146, 'long_sks': 59, 'long_sd': 43, 'long_lev': 5},
    {'symbol': 'RONINUSDT', 'short_ma': 67, 'short_sk': 143, 'short_sks': 58, 'short_sd': 41, 'long_ma': 143, 'long_sk': 148, 'long_sks': 25, 'long_sd': 21, 'long_lev': 5},
    {'symbol': 'RENDERUSDT', 'short_ma': 157, 'short_sk': 44, 'short_sks': 57, 'short_sd': 13, 'long_ma': 231, 'long_sk': 17, 'long_sks': 12, 'long_sd': 3, 'long_lev': 4},
    {'symbol': 'NTRNUSDT', 'short_ma': 85, 'short_sk': 141, 'short_sks': 50, 'short_sd': 33, 'long_ma': 61, 'long_sk': 62, 'long_sks': 11, 'long_sd': 19, 'long_lev': 5},
    {'symbol': 'AIUSDT', 'short_ma': 126, 'short_sk': 118, 'short_sks': 57, 'short_sd': 28, 'long_ma': 39, 'long_sk': 44, 'long_sks': 42, 'long_sd': 36, 'long_lev': 3},
    {'symbol': 'WUSDT', 'short_ma': 303, 'short_sk': 138, 'short_sks': 76, 'short_sd': 4, 'long_ma': 30, 'long_sk': 39, 'long_sks': 11, 'long_sd': 8, 'long_lev': 4},
    {'symbol': 'BLURUSDT', 'short_ma': 126, 'short_sk': 113, 'short_sks': 52, 'short_sd': 25, 'long_ma': 30, 'long_sk': 83, 'long_sks': 80, 'long_sd': 28, 'long_lev': 2},
    {'symbol': 'LSKUSDT', 'short_ma': 80, 'short_sk': 88, 'short_sks': 80, 'short_sd': 49, 'long_ma': 66, 'long_sk': 70, 'long_sks': 63, 'long_sd': 26, 'long_lev': 1},
    {'symbol': 'BBUSDT', 'short_ma': 327, 'short_sk': 150, 'short_sks': 75, 'short_sd': 25, 'long_ma': 103, 'long_sk': 94, 'long_sks': 40, 'long_sd': 39, 'long_lev': 5},
    {'symbol': 'GUSDT', 'short_ma': 112, 'short_sk': 93, 'short_sks': 54, 'short_sd': 8, 'long_ma': 22, 'long_sk': 25, 'long_sks': 42, 'long_sd': 21, 'long_lev': 1},
    {'symbol': 'WIFUSDT', 'short_ma': 20, 'short_sk': 107, 'short_sks': 64, 'short_sd': 16, 'long_ma': 248, 'long_sk': 129, 'long_sks': 12, 'long_sd': 44, 'long_lev': 3},
    {'symbol': '1000CHEEMSUSDT', 'short_ma': 71, 'short_sk': 150, 'short_sks': 46, 'short_sd': 46, 'long_ma': 112, 'long_sk': 110, 'long_sks': 63, 'long_sd': 40, 'long_lev': 3},
    {'symbol': 'FLUXUSDT', 'short_ma': 75, 'short_sk': 89, 'short_sks': 57, 'short_sd': 37, 'long_ma': 37, 'long_sk': 38, 'long_sks': 14, 'long_sd': 27, 'long_lev': 4},
    {'symbol': 'DIAUSDT', 'short_ma': 92, 'short_sk': 148, 'short_sks': 56, 'short_sd': 48, 'long_ma': 56, 'long_sk': 44, 'long_sks': 22, 'long_sd': 29, 'long_lev': 5},
    {'symbol': 'METISUSDT', 'short_ma': 175, 'short_sk': 137, 'short_sks': 57, 'short_sd': 23, 'long_ma': 60, 'long_sk': 100, 'long_sks': 65, 'long_sd': 28, 'long_lev': 2},
    {'symbol': 'BICOUSDT', 'short_ma': 287, 'short_sk': 120, 'short_sks': 70, 'short_sd': 50, 'long_ma': 217, 'long_sk': 132, 'long_sks': 35, 'long_sd': 17, 'long_lev': 1},
    {'symbol': 'STRKUSDT', 'short_ma': 95, 'short_sk': 119, 'short_sks': 58, 'short_sd': 34, 'long_ma': 71, 'long_sk': 37, 'long_sks': 80, 'long_sd': 30, 'long_lev': 4},
    {'symbol': 'PYTHUSDT', 'short_ma': 171, 'short_sk': 58, 'short_sks': 77, 'short_sd': 19, 'long_ma': 27, 'long_sk': 90, 'long_sks': 31, 'long_sd': 15, 'long_lev': 1},
    {'symbol': 'COSUSDT', 'short_ma': 86, 'short_sk': 102, 'short_sks': 58, 'short_sd': 39, 'long_ma': 218, 'long_sk': 150, 'long_sks': 80, 'long_sd': 50, 'long_lev': 1},
    {'symbol': 'ETHWUSDT', 'short_ma': 96, 'short_sk': 146, 'short_sks': 58, 'short_sd': 42, 'long_ma': 350, 'long_sk': 103, 'long_sks': 78, 'long_sd': 8, 'long_lev': 5},
    {'symbol': 'TNSRUSDT', 'short_ma': 228, 'short_sk': 104, 'short_sks': 64, 'short_sd': 37, 'long_ma': 66, 'long_sk': 34, 'long_sks': 42, 'long_sd': 32, 'long_lev': 5},
    {'symbol': 'MEMEUSDT', 'short_ma': 97, 'short_sk': 93, 'short_sks': 66, 'short_sd': 33, 'long_ma': 49, 'long_sk': 140, 'long_sks': 67, 'long_sd': 13, 'long_lev': 1},
    {'symbol': 'LUMIAUSDT', 'short_ma': 296, 'short_sk': 137, 'short_sks': 52, 'short_sd': 31, 'long_ma': 50, 'long_sk': 150, 'long_sks': 80, 'long_sd': 50, 'long_lev': 1},
    {'symbol': 'SEIUSDT', 'short_ma': 192, 'short_sk': 119, 'short_sks': 66, 'short_sd': 42, 'long_ma': 61, 'long_sk': 82, 'long_sks': 65, 'long_sd': 4, 'long_lev': 5},
    {'symbol': 'REZUSDT', 'short_ma': 221, 'short_sk': 16, 'short_sks': 13, 'short_sd': 34, 'long_ma': 51, 'long_sk': 121, 'long_sks': 77, 'long_sd': 36, 'long_lev': 1},
    {'symbol': 'CATIUSDT', 'short_ma': 330, 'short_sk': 87, 'short_sks': 15, 'short_sd': 17, 'long_ma': 200, 'long_sk': 30, 'long_sks': 45, 'long_sd': 23, 'long_lev': 1},
    {'symbol': 'MOVRUSDT', 'short_ma': 258, 'short_sk': 128, 'short_sks': 53, 'short_sd': 40, 'long_ma': 93, 'long_sk': 31, 'long_sks': 32, 'long_sd': 14, 'long_lev': 5},
    {'symbol': 'BIGTIMEUSDT', 'short_ma': 163, 'short_sk': 80, 'short_sks': 57, 'short_sd': 8, 'long_ma': 49, 'long_sk': 112, 'long_sks': 80, 'long_sd': 45, 'long_lev': 5},
    {'symbol': 'AVAUSDT', 'short_ma': 127, 'short_sk': 150, 'short_sks': 80, 'short_sd': 33, 'long_ma': 20, 'long_sk': 27, 'long_sks': 49, 'long_sd': 17, 'long_lev': 4},
    {'symbol': 'MELANIAUSDT', 'short_ma': 143, 'short_sk': 70, 'short_sks': 12, 'short_sd': 49, 'long_ma': 46, 'long_sk': 150, 'long_sks': 76, 'long_sd': 39, 'long_lev': 5},
    {'symbol': 'MOODENGUSDT', 'short_ma': 245, 'short_sk': 28, 'short_sks': 12, 'short_sd': 4, 'long_ma': 122, 'long_sk': 71, 'long_sks': 72, 'long_sd': 30, 'long_lev': 5},
    {'symbol': 'NEIROUSDT', 'short_ma': 289, 'short_sk': 104, 'short_sks': 58, 'short_sd': 6, 'long_ma': 127, 'long_sk': 77, 'long_sks': 71, 'long_sd': 48, 'long_lev': 5},
    {'symbol': 'POLYXUSDT', 'short_ma': 238, 'short_sk': 137, 'short_sks': 68, 'short_sd': 24, 'long_ma': 88, 'long_sk': 65, 'long_sks': 79, 'long_sd': 28, 'long_lev': 3},
    {'symbol': 'IDUSDT', 'short_ma': 46, 'short_sk': 144, 'short_sks': 75, 'short_sd': 28, 'long_ma': 294, 'long_sk': 17, 'long_sks': 70, 'long_sd': 8, 'long_lev': 2},
    {'symbol': 'TONUSDT', 'short_ma': 130, 'short_sk': 117, 'short_sks': 80, 'short_sd': 19, 'long_ma': 69, 'long_sk': 21, 'long_sks': 24, 'long_sd': 33, 'long_lev': 3},
    {'symbol': 'SAFEUSDT', 'short_ma': 331, 'short_sk': 14, 'short_sks': 18, 'short_sd': 36, 'long_ma': 37, 'long_sk': 142, 'long_sks': 36, 'long_sd': 35, 'long_lev': 1},
    {'symbol': 'WAXPUSDT', 'short_ma': 135, 'short_sk': 142, 'short_sks': 80, 'short_sd': 15, 'long_ma': 62, 'long_sk': 32, 'long_sks': 26, 'long_sd': 29, 'long_lev': 4},
    {'symbol': 'FIOUSDT', 'short_ma': 194, 'short_sk': 123, 'short_sks': 30, 'short_sd': 10, 'long_ma': 278, 'long_sk': 125, 'long_sks': 39, 'long_sd': 30, 'long_lev': 1},
    {'symbol': 'XAIUSDT', 'short_ma': 113, 'short_sk': 52, 'short_sks': 71, 'short_sd': 24, 'long_ma': 20, 'long_sk': 38, 'long_sks': 10, 'long_sd': 18, 'long_lev': 2},
    {'symbol': 'ILVUSDT', 'short_ma': 36, 'short_sk': 112, 'short_sks': 55, 'short_sd': 48, 'long_ma': 85, 'long_sk': 39, 'long_sks': 13, 'long_sd': 31, 'long_lev': 5},
    {'symbol': 'HFTUSDT', 'short_ma': 331, 'short_sk': 148, 'short_sks': 78, 'short_sd': 43, 'long_ma': 21, 'long_sk': 133, 'long_sks': 50, 'long_sd': 27, 'long_lev': 1},
    {'symbol': '1000FLOKIUSDT', 'short_ma': 118, 'short_sk': 93, 'short_sks': 78, 'short_sd': 43, 'long_ma': 311, 'long_sk': 16, 'long_sks': 31, 'long_sd': 26, 'long_lev': 5},
    {'symbol': 'STEEMUSDT', 'short_ma': 49, 'short_sk': 115, 'short_sks': 53, 'short_sd': 36, 'long_ma': 47, 'long_sk': 76, 'long_sks': 26, 'long_sd': 18, 'long_lev': 4},
    {'symbol': 'ACEUSDT', 'short_ma': 191, 'short_sk': 70, 'short_sks': 65, 'short_sd': 46, 'long_ma': 22, 'long_sk': 143, 'long_sks': 80, 'long_sd': 24, 'long_lev': 1},
    {'symbol': 'ARKMUSDT', 'short_ma': 233, 'short_sk': 38, 'short_sks': 25, 'short_sd': 46, 'long_ma': 176, 'long_sk': 120, 'long_sks': 73, 'long_sd': 9, 'long_lev': 5},
    {'symbol': 'CAKEUSDT', 'short_ma': 35, 'short_sk': 109, 'short_sks': 80, 'short_sd': 50, 'long_ma': 35, 'long_sk': 30, 'long_sks': 32, 'long_sd': 43, 'long_lev': 5},
    {'symbol': 'ETHFIUSDT', 'short_ma': 350, 'short_sk': 146, 'short_sks': 45, 'short_sd': 38, 'long_ma': 215, 'long_sk': 62, 'long_sks': 73, 'long_sd': 26, 'long_lev': 5},
    {'symbol': 'ARBUSDT', 'short_ma': 221, 'short_sk': 150, 'short_sks': 74, 'short_sd': 47, 'long_ma': 54, 'long_sk': 143, 'long_sks': 63, 'long_sd': 15, 'long_lev': 5},
    {'symbol': 'BEAMXUSDT', 'short_ma': 126, 'short_sk': 89, 'short_sks': 70, 'short_sd': 44, 'long_ma': 76, 'long_sk': 97, 'long_sks': 79, 'long_sd': 8, 'long_lev': 2},
    {'symbol': 'THEUSDT', 'short_ma': 78, 'short_sk': 112, 'short_sks': 71, 'short_sd': 40, 'long_ma': 167, 'long_sk': 17, 'long_sks': 28, 'long_sd': 44, 'long_lev': 5},
    {'symbol': '1000BONKUSDT', 'short_ma': 24, 'short_sk': 146, 'short_sks': 71, 'short_sd': 48, 'long_ma': 235, 'long_sk': 18, 'long_sks': 15, 'long_sd': 13, 'long_lev': 5},
    {'symbol': 'DOGSUSDT', 'short_ma': 37, 'short_sk': 25, 'short_sks': 24, 'short_sd': 50, 'long_ma': 217, 'long_sk': 88, 'long_sks': 72, 'long_sd': 47, 'long_lev': 5},
    {'symbol': 'CYBERUSDT', 'short_ma': 338, 'short_sk': 135, 'short_sks': 75, 'short_sd': 35, 'long_ma': 299, 'long_sk': 20, 'long_sks': 35, 'long_sd': 4, 'long_lev': 4},
    {'symbol': 'LISTAUSDT', 'short_ma': 67, 'short_sk': 27, 'short_sks': 25, 'short_sd': 50, 'long_ma': 329, 'long_sk': 119, 'long_sks': 63, 'long_sd': 13, 'long_lev': 5},
    {'symbol': 'BNTUSDT', 'short_ma': 292, 'short_sk': 120, 'short_sks': 80, 'short_sd': 50, 'long_ma': 273, 'long_sk': 62, 'long_sks': 12, 'long_sd': 8, 'long_lev': 4},
    {'symbol': 'RIFUSDT', 'short_ma': 129, 'short_sk': 139, 'short_sks': 45, 'short_sd': 49, 'long_ma': 80, 'long_sk': 32, 'long_sks': 32, 'long_sd': 9, 'long_lev': 5},
    {'symbol': 'FLOWUSDT', 'short_ma': 109, 'short_sk': 106, 'short_sks': 45, 'short_sd': 48, 'long_ma': 146, 'long_sk': 139, 'long_sks': 29, 'long_sd': 9, 'long_lev': 4},
    {'symbol': 'POWRUSDT', 'short_ma': 183, 'short_sk': 146, 'short_sks': 69, 'short_sd': 25, 'long_ma': 36, 'long_sk': 139, 'long_sks': 12, 'long_sd': 36, 'long_lev': 4},
    {'symbol': 'SUIUSDT', 'short_ma': 332, 'short_sk': 146, 'short_sks': 77, 'short_sd': 49, 'long_ma': 100, 'long_sk': 108, 'long_sks': 41, 'long_sd': 5, 'long_lev': 5},
    {'symbol': 'ORDIUSDT', 'short_ma': 144, 'short_sk': 85, 'short_sks': 77, 'short_sd': 42, 'long_ma': 24, 'long_sk': 112, 'long_sks': 61, 'long_sd': 3, 'long_lev': 1},
    {'symbol': 'APEUSDT', 'short_ma': 145, 'short_sk': 129, 'short_sks': 79, 'short_sd': 18, 'long_ma': 220, 'long_sk': 105, 'long_sks': 47, 'long_sd': 22, 'long_lev': 4},
    {'symbol': 'COWUSDT', 'short_ma': 338, 'short_sk': 147, 'short_sks': 53, 'short_sd': 43, 'long_ma': 90, 'long_sk': 102, 'long_sks': 73, 'long_sd': 34, 'long_lev': 5},
    {'symbol': 'MANAUSDT', 'short_ma': 168, 'short_sk': 132, 'short_sks': 73, 'short_sd': 42, 'long_ma': 45, 'long_sk': 141, 'long_sks': 24, 'long_sd': 49, 'long_lev': 3},
    {'symbol': 'YGGUSDT', 'short_ma': 207, 'short_sk': 142, 'short_sks': 39, 'short_sd': 22, 'long_ma': 34, 'long_sk': 96, 'long_sks': 18, 'long_sd': 39, 'long_lev': 2},
    {'symbol': 'HIGHUSDT', 'short_ma': 289, 'short_sk': 145, 'short_sks': 63, 'short_sd': 44, 'long_ma': 73, 'long_sk': 78, 'long_sks': 79, 'long_sd': 39, 'long_lev': 5},
    {'symbol': 'ONDOUSDT', 'short_ma': 313, 'short_sk': 89, 'short_sks': 80, 'short_sd': 27, 'long_ma': 85, 'long_sk': 35, 'long_sks': 18, 'long_sd': 21, 'long_lev': 5},
    {'symbol': 'SUNUSDT', 'short_ma': 160, 'short_sk': 137, 'short_sks': 77, 'short_sd': 46, 'long_ma': 155, 'long_sk': 137, 'long_sks': 59, 'long_sd': 38, 'long_lev': 5},
    {'symbol': 'LUNA2USDT', 'short_ma': 237, 'short_sk': 137, 'short_sks': 10, 'short_sd': 25, 'long_ma': 64, 'long_sk': 120, 'long_sks': 56, 'long_sd': 42, 'long_lev': 4},
    {'symbol': 'ZROUSDT', 'short_ma': 116, 'short_sk': 84, 'short_sks': 50, 'short_sd': 29, 'long_ma': 105, 'long_sk': 78, 'long_sks': 34, 'long_sd': 7, 'long_lev': 2},
    {'symbol': 'MAVUSDT', 'short_ma': 25, 'short_sk': 73, 'short_sks': 40, 'short_sd': 17, 'long_ma': 200, 'long_sk': 37, 'long_sks': 61, 'long_sd': 32, 'long_lev': 3},
    {'symbol': 'MAGICUSDT', 'short_ma': 275, 'short_sk': 144, 'short_sks': 72, 'short_sd': 34, 'long_ma': 85, 'long_sk': 25, 'long_sks': 57, 'long_sd': 50, 'long_lev': 4},
    {'symbol': 'AXLUSDT', 'short_ma': 346, 'short_sk': 130, 'short_sks': 59, 'short_sd': 34, 'long_ma': 99, 'long_sk': 150, 'long_sks': 75, 'long_sd': 44, 'long_lev': 5},
    {'symbol': '1000PEPEUSDT', 'short_ma': 48, 'short_sk': 25, 'short_sks': 24, 'short_sd': 31, 'long_ma': 297, 'long_sk': 100, 'long_sks': 10, 'long_sd': 10, 'long_lev': 4},
    {'symbol': 'GALAUSDT', 'short_ma': 57, 'short_sk': 139, 'short_sks': 33, 'short_sd': 10, 'long_ma': 322, 'long_sk': 114, 'long_sks': 26, 'long_sd': 19, 'long_lev': 3},
    {'symbol': 'ONEUSDT', 'short_ma': 123, 'short_sk': 78, 'short_sks': 50, 'short_sd': 44, 'long_ma': 249, 'long_sk': 42, 'long_sks': 27, 'long_sd': 15, 'long_lev': 3},
    {'symbol': 'AUCTIONUSDT', 'short_ma': 53, 'short_sk': 34, 'short_sks': 41, 'short_sd': 8, 'long_ma': 22, 'long_sk': 122, 'long_sks': 31, 'long_sd': 50, 'long_lev': 3},
    {'symbol': 'ALCHUSDT', 'short_ma': 298, 'short_sk': 48, 'short_sks': 7, 'short_sd': 41, 'long_ma': 254, 'long_sk': 147, 'long_sks': 54, 'long_sd': 36, 'long_lev': 5},
    {'symbol': 'ASTRUSDT', 'short_ma': 36, 'short_sk': 60, 'short_sks': 69, 'short_sd': 39, 'long_ma': 197, 'long_sk': 149, 'long_sks': 51, 'long_sd': 18, 'long_lev': 1},
    {'symbol': 'GMTUSDT', 'short_ma': 107, 'short_sk': 140, 'short_sks': 67, 'short_sd': 47, 'long_ma': 265, 'long_sk': 18, 'long_sks': 12, 'long_sd': 11, 'long_lev': 1},
    {'symbol': 'WLDUSDT', 'short_ma': 253, 'short_sk': 125, 'short_sks': 16, 'short_sd': 5, 'long_ma': 287, 'long_sk': 28, 'long_sks': 28, 'long_sd': 7, 'long_lev': 5},
    {'symbol': 'TIAUSDT', 'short_ma': 279, 'short_sk': 95, 'short_sks': 36, 'short_sd': 7, 'long_ma': 20, 'long_sk': 149, 'long_sks': 80, 'long_sd': 26, 'long_lev': 3},
    {'symbol': 'ZEREBROUSDT', 'short_ma': 296, 'short_sk': 70, 'short_sks': 39, 'short_sd': 36, 'long_ma': 61, 'long_sk': 144, 'long_sks': 66, 'long_sd': 40, 'long_lev': 4},
    {'symbol': 'JOEUSDT', 'short_ma': 245, 'short_sk': 144, 'short_sks': 40, 'short_sd': 32, 'long_ma': 64, 'long_sk': 41, 'long_sks': 18, 'long_sd': 36, 'long_lev': 5},
    {'symbol': 'SANDUSDT', 'short_ma': 125, 'short_sk': 140, 'short_sks': 72, 'short_sd': 45, 'long_ma': 211, 'long_sk': 132, 'long_sks': 28, 'long_sd': 9, 'long_lev': 3},
    {'symbol': 'AGLDUSDT', 'short_ma': 116, 'short_sk': 148, 'short_sks': 75, 'short_sd': 48, 'long_ma': 64, 'long_sk': 14, 'long_sks': 28, 'long_sd': 38, 'long_lev': 1},
    {'symbol': 'KASUSDT', 'short_ma': 78, 'short_sk': 98, 'short_sks': 75, 'short_sd': 40, 'long_ma': 191, 'long_sk': 33, 'long_sks': 32, 'long_sd': 36, 'long_lev': 5},
    {'symbol': 'OPUSDT', 'short_ma': 343, 'short_sk': 148, 'short_sks': 76, 'short_sd': 48, 'long_ma': 243, 'long_sk': 22, 'long_sks': 17, 'long_sd': 20, 'long_lev': 3},
    {'symbol': 'MANTAUSDT', 'short_ma': 237, 'short_sk': 81, 'short_sks': 39, 'short_sd': 10, 'long_ma': 58, 'long_sk': 92, 'long_sks': 79, 'long_sd': 43, 'long_lev': 2},
    {'symbol': 'PENDLEUSDT', 'short_ma': 153, 'short_sk': 88, 'short_sks': 56, 'short_sd': 32, 'long_ma': 244, 'long_sk': 128, 'long_sks': 74, 'long_sd': 42, 'long_lev': 5},
    {'symbol': 'STXUSDT', 'short_ma': 93, 'short_sk': 148, 'short_sks': 74, 'short_sd': 48, 'long_ma': 275, 'long_sk': 19, 'long_sks': 17, 'long_sd': 12, 'long_lev': 3},
    {'symbol': 'HBARUSDT', 'short_ma': 129, 'short_sk': 102, 'short_sks': 76, 'short_sd': 46, 'long_ma': 237, 'long_sk': 111, 'long_sks': 25, 'long_sd': 10, 'long_lev': 2},
    {'symbol': 'ICXUSDT', 'short_ma': 120, 'short_sk': 148, 'short_sks': 62, 'short_sd': 30, 'long_ma': 224, 'long_sk': 112, 'long_sks': 45, 'long_sd': 42, 'long_lev': 3},
    {'symbol': 'MINAUSDT', 'short_ma': 346, 'short_sk': 55, 'short_sks': 32, 'short_sd': 9, 'long_ma': 51, 'long_sk': 115, 'long_sks': 76, 'long_sd': 38, 'long_lev': 5},
    {'symbol': 'BSVUSDT', 'short_ma': 38, 'short_sk': 92, 'short_sks': 37, 'short_sd': 39, 'long_ma': 28, 'long_sk': 56, 'long_sks': 61, 'long_sd': 12, 'long_lev': 1},
    {'symbol': '1000LUNCUSDT', 'short_ma': 126, 'short_sk': 121, 'short_sks': 38, 'short_sd': 12, 'long_ma': 31, 'long_sk': 112, 'long_sks': 55, 'long_sd': 48, 'long_lev': 2},
    {'symbol': 'CKBUSDT', 'short_ma': 315, 'short_sk': 127, 'short_sks': 80, 'short_sd': 9, 'long_ma': 72, 'long_sk': 150, 'long_sks': 30, 'long_sd': 10, 'long_lev': 4},
    {'symbol': 'FETUSDT', 'short_ma': 132, 'short_sk': 150, 'short_sks': 70, 'short_sd': 46, 'long_ma': 321, 'long_sk': 72, 'long_sks': 18, 'long_sd': 13, 'long_lev': 3},
    {'symbol': 'PHBUSDT', 'short_ma': 144, 'short_sk': 149, 'short_sks': 76, 'short_sd': 22, 'long_ma': 275, 'long_sk': 94, 'long_sks': 22, 'long_sd': 27, 'long_lev': 3},
    {'symbol': 'SANTOSUSDT', 'short_ma': 179, 'short_sk': 103, 'short_sks': 62, 'short_sd': 39, 'long_ma': 162, 'long_sk': 72, 'long_sks': 79, 'long_sd': 3, 'long_lev': 5},
    {'symbol': 'SUSHIUSDT', 'short_ma': 221, 'short_sk': 108, 'short_sks': 60, 'short_sd': 14, 'long_ma': 193, 'long_sk': 21, 'long_sks': 29, 'long_sd': 7, 'long_lev': 1},
    {'symbol': 'EDUUSDT', 'short_ma': 40, 'short_sk': 150, 'short_sks': 60, 'short_sd': 31, 'long_ma': 88, 'long_sk': 139, 'long_sks': 12, 'long_sd': 15, 'long_lev': 1},
    {'symbol': 'JUPUSDT', 'short_ma': 24, 'short_sk': 104, 'short_sks': 61, 'short_sd': 30, 'long_ma': 56, 'long_sk': 150, 'long_sks': 58, 'long_sd': 32, 'long_lev': 4},
    {'symbol': '1000SATSUSDT', 'short_ma': 344, 'short_sk': 99, 'short_sks': 34, 'short_sd': 7, 'long_ma': 27, 'long_sk': 115, 'long_sks': 69, 'long_sd': 18, 'long_lev': 3},
    {'symbol': 'ONGUSDT', 'short_ma': 312, 'short_sk': 122, 'short_sks': 6, 'short_sd': 11, 'long_ma': 68, 'long_sk': 102, 'long_sks': 80, 'long_sd': 39, 'long_lev': 3},
    {'symbol': 'IMXUSDT', 'short_ma': 131, 'short_sk': 105, 'short_sks': 53, 'short_sd': 37, 'long_ma': 289, 'long_sk': 114, 'long_sks': 78, 'long_sd': 47, 'long_lev': 3},
    {'symbol': 'AXSUSDT', 'short_ma': 198, 'short_sk': 90, 'short_sks': 63, 'short_sd': 12, 'long_ma': 267, 'long_sk': 25, 'long_sks': 20, 'long_sd': 14, 'long_lev': 2},
    {'symbol': 'MORPHOUSDT', 'short_ma': 20, 'short_sk': 125, 'short_sks': 16, 'short_sd': 6, 'long_ma': 82, 'long_sk': 84, 'long_sks': 39, 'long_sd': 12, 'long_lev': 5},
    {'symbol': 'WOOUSDT', 'short_ma': 199, 'short_sk': 150, 'short_sks': 76, 'short_sd': 39, 'long_ma': 65, 'long_sk': 72, 'long_sks': 80, 'long_sd': 33, 'long_lev': 3},
    {'symbol': 'API3USDT', 'short_ma': 226, 'short_sk': 147, 'short_sks': 50, 'short_sd': 43, 'long_ma': 80, 'long_sk': 69, 'long_sks': 14, 'long_sd': 13, 'long_lev': 1},
    {'symbol': '1000XECUSDT', 'short_ma': 232, 'short_sk': 145, 'short_sks': 32, 'short_sd': 4, 'long_ma': 343, 'long_sk': 33, 'long_sks': 14, 'long_sd': 3, 'long_lev': 1},
    {'symbol': 'SKLUSDT', 'short_ma': 210, 'short_sk': 125, 'short_sks': 20, 'short_sd': 35, 'long_ma': 240, 'long_sk': 65, 'long_sks': 58, 'long_sd': 50, 'long_lev': 4},
    {'symbol': 'IOSTUSDT', 'short_ma': 317, 'short_sk': 90, 'short_sks': 77, 'short_sd': 50, 'long_ma': 207, 'long_sk': 150, 'long_sks': 12, 'long_sd': 3, 'long_lev': 2},
    {'symbol': 'CTSIUSDT', 'short_ma': 220, 'short_sk': 146, 'short_sks': 64, 'short_sd': 46, 'long_ma': 295, 'long_sk': 138, 'long_sks': 9, 'long_sd': 22, 'long_lev': 2},
    {'symbol': 'ENJUSDT', 'short_ma': 234, 'short_sk': 83, 'short_sks': 14, 'short_sd': 12, 'long_ma': 255, 'long_sk': 27, 'long_sks': 26, 'long_sd': 11, 'long_lev': 3},
    {'symbol': 'CFXUSDT', 'short_ma': 253, 'short_sk': 149, 'short_sks': 72, 'short_sd': 41, 'long_ma': 222, 'long_sk': 125, 'long_sks': 15, 'long_sd': 34, 'long_lev': 3},
    {'symbol': 'IOTXUSDT', 'short_ma': 336, 'short_sk': 144, 'short_sks': 67, 'short_sd': 45, 'long_ma': 34, 'long_sk': 135, 'long_sks': 59, 'long_sd': 15, 'long_lev': 1},
    {'symbol': 'VETUSDT', 'short_ma': 202, 'short_sk': 116, 'short_sks': 73, 'short_sd': 32, 'long_ma': 223, 'long_sk': 33, 'long_sks': 7, 'long_sd': 50, 'long_lev': 4},
    {'symbol': '1000SHIBUSDT', 'short_ma': 96, 'short_sk': 82, 'short_sks': 60, 'short_sd': 23, 'long_ma': 285, 'long_sk': 57, 'long_sks': 24, 'long_sd': 40, 'long_lev': 3},
    {'symbol': 'XVGUSDT', 'short_ma': 70, 'short_sk': 121, 'short_sks': 43, 'short_sd': 18, 'long_ma': 20, 'long_sk': 124, 'long_sks': 67, 'long_sd': 3, 'long_lev': 3},
    {'symbol': 'GLMUSDT', 'short_ma': 86, 'short_sk': 77, 'short_sks': 29, 'short_sd': 17, 'long_ma': 67, 'long_sk': 124, 'long_sks': 80, 'long_sd': 41, 'long_lev': 5},
    {'symbol': 'ANKRUSDT', 'short_ma': 343, 'short_sk': 148, 'short_sks': 66, 'short_sd': 35, 'long_ma': 124, 'long_sk': 64, 'long_sks': 14, 'long_sd': 22, 'long_lev': 3},
    {'symbol': 'RVNUSDT', 'short_ma': 165, 'short_sk': 146, 'short_sks': 46, 'short_sd': 22, 'long_ma': 57, 'long_sk': 111, 'long_sks': 42, 'long_sd': 15, 'long_lev': 2},
    {'symbol': 'ROSEUSDT', 'short_ma': 55, 'short_sk': 34, 'short_sks': 7, 'short_sd': 40, 'long_ma': 59, 'long_sk': 102, 'long_sks': 24, 'long_sd': 31, 'long_lev': 4},
    {'symbol': 'AVAXUSDT', 'short_ma': 101, 'short_sk': 28, 'short_sks': 23, 'short_sd': 31, 'long_ma': 104, 'long_sk': 15, 'long_sks': 57, 'long_sd': 19, 'long_lev': 2},
    {'symbol': 'KSMUSDT', 'short_ma': 115, 'short_sk': 113, 'short_sks': 39, 'short_sd': 50, 'long_ma': 213, 'long_sk': 140, 'long_sks': 14, 'long_sd': 22, 'long_lev': 1},
    {'symbol': 'HOTUSDT', 'short_ma': 181, 'short_sk': 139, 'short_sks': 57, 'short_sd': 33, 'long_ma': 282, 'long_sk': 19, 'long_sks': 20, 'long_sd': 15, 'long_lev': 2},
    {'symbol': 'ENSUSDT', 'short_ma': 290, 'short_sk': 41, 'short_sks': 57, 'short_sd': 49, 'long_ma': 326, 'long_sk': 26, 'long_sks': 25, 'long_sd': 18, 'long_lev': 4},
    {'symbol': 'TUSDT', 'short_ma': 124, 'short_sk': 146, 'short_sks': 80, 'short_sd': 31, 'long_ma': 66, 'long_sk': 119, 'long_sks': 42, 'long_sd': 19, 'long_lev': 3},
    {'symbol': 'IOTAUSDT', 'short_ma': 147, 'short_sk': 131, 'short_sks': 59, 'short_sd': 47, 'long_ma': 239, 'long_sk': 71, 'long_sks': 29, 'long_sd': 15, 'long_lev': 1},
    {'symbol': 'GTCUSDT', 'short_ma': 219, 'short_sk': 134, 'short_sks': 76, 'short_sd': 35, 'long_ma': 69, 'long_sk': 32, 'long_sks': 51, 'long_sd': 10, 'long_lev': 1},
    {'symbol': 'NEARUSDT', 'short_ma': 174, 'short_sk': 81, 'short_sks': 70, 'short_sd': 26, 'long_ma': 244, 'long_sk': 91, 'long_sks': 28, 'long_sd': 18, 'long_lev': 2},
    {'symbol': 'TWTUSDT', 'short_ma': 34, 'short_sk': 85, 'short_sks': 80, 'short_sd': 19, 'long_ma': 320, 'long_sk': 30, 'long_sks': 13, 'long_sd': 23, 'long_lev': 4},
    {'symbol': 'SPELLUSDT', 'short_ma': 236, 'short_sk': 104, 'short_sks': 57, 'short_sd': 40, 'long_ma': 237, 'long_sk': 17, 'long_sks': 28, 'long_sd': 10, 'long_lev': 2},
    {'symbol': 'RSRUSDT', 'short_ma': 84, 'short_sk': 115, 'short_sks': 62, 'short_sd': 20, 'long_ma': 45, 'long_sk': 50, 'long_sks': 24, 'long_sd': 22, 'long_lev': 3},
    {'symbol': 'FILUSDT', 'short_ma': 308, 'short_sk': 17, 'short_sks': 37, 'short_sd': 7, 'long_ma': 319, 'long_sk': 88, 'long_sks': 18, 'long_sd': 4, 'long_lev': 1},
    {'symbol': 'TRUUSDT', 'short_ma': 273, 'short_sk': 143, 'short_sks': 71, 'short_sd': 34, 'long_ma': 236, 'long_sk': 145, 'long_sks': 35, 'long_sd': 4, 'long_lev': 3},
    {'symbol': 'GASUSDT', 'short_ma': 52, 'short_sk': 134, 'short_sks': 55, 'short_sd': 24, 'long_ma': 309, 'long_sk': 150, 'long_sks': 80, 'long_sd': 34, 'long_lev': 5},
    {'symbol': 'SNXUSDT', 'short_ma': 38, 'short_sk': 130, 'short_sks': 57, 'short_sd': 16, 'long_ma': 124, 'long_sk': 55, 'long_sks': 63, 'long_sd': 35, 'long_lev': 1},
    {'symbol': 'SUPERUSDT', 'short_ma': 26, 'short_sk': 150, 'short_sks': 55, 'short_sd': 44, 'long_ma': 218, 'long_sk': 138, 'long_sks': 36, 'long_sd': 48, 'long_lev': 4},
    {'symbol': 'LDOUSDT', 'short_ma': 54, 'short_sk': 134, 'short_sks': 80, 'short_sd': 7, 'long_ma': 22, 'long_sk': 146, 'long_sks': 46, 'long_sd': 16, 'long_lev': 3},
    {'symbol': 'GMXUSDT', 'short_ma': 88, 'short_sk': 99, 'short_sks': 75, 'short_sd': 45, 'long_ma': 137, 'long_sk': 34, 'long_sks': 61, 'long_sd': 26, 'long_lev': 1},
    {'symbol': 'ZRXUSDT', 'short_ma': 191, 'short_sk': 122, 'short_sks': 57, 'short_sd': 40, 'long_ma': 346, 'long_sk': 95, 'long_sks': 24, 'long_sd': 15, 'long_lev': 2},
    {'symbol': 'ATAUSDT', 'short_ma': 94, 'short_sk': 143, 'short_sks': 78, 'short_sd': 33, 'long_ma': 51, 'long_sk': 101, 'long_sks': 47, 'long_sd': 40, 'long_lev': 1},
    {'symbol': 'XVSUSDT', 'short_ma': 65, 'short_sk': 140, 'short_sks': 54, 'short_sd': 22, 'long_ma': 20, 'long_sk': 104, 'long_sks': 41, 'long_sd': 27, 'long_lev': 2},
    {'symbol': 'LPTUSDT', 'short_ma': 95, 'short_sk': 63, 'short_sks': 37, 'short_sd': 21, 'long_ma': 346, 'long_sk': 59, 'long_sks': 19, 'long_sd': 10, 'long_lev': 1},
    {'symbol': 'EGLDUSDT', 'short_ma': 82, 'short_sk': 139, 'short_sks': 22, 'short_sd': 39, 'long_ma': 170, 'long_sk': 91, 'long_sks': 18, 'long_sd': 13, 'long_lev': 1},
    {'symbol': 'CELOUSDT', 'short_ma': 246, 'short_sk': 121, 'short_sks': 79, 'short_sd': 48, 'long_ma': 127, 'long_sk': 147, 'long_sks': 75, 'long_sd': 16, 'long_lev': 1},
    {'symbol': 'NMRUSDT', 'short_ma': 348, 'short_sk': 137, 'short_sks': 78, 'short_sd': 25, 'long_ma': 244, 'long_sk': 75, 'long_sks': 80, 'long_sd': 49, 'long_lev': 2},
    {'symbol': 'DYDXUSDT', 'short_ma': 77, 'short_sk': 34, 'short_sks': 17, 'short_sd': 31, 'long_ma': 50, 'long_sk': 146, 'long_sks': 15, 'long_sd': 41, 'long_lev': 3},
    {'symbol': 'QNTUSDT', 'short_ma': 288, 'short_sk': 142, 'short_sks': 54, 'short_sd': 38, 'long_ma': 207, 'long_sk': 80, 'long_sks': 21, 'long_sd': 6, 'long_lev': 5},
    {'symbol': 'ARKUSDT', 'short_ma': 341, 'short_sk': 149, 'short_sks': 74, 'short_sd': 44, 'long_ma': 105, 'long_sk': 43, 'long_sks': 15, 'long_sd': 30, 'long_lev': 4},
    {'symbol': 'RUNEUSDT', 'short_ma': 272, 'short_sk': 132, 'short_sks': 28, 'short_sd': 48, 'long_ma': 245, 'long_sk': 17, 'long_sks': 39, 'long_sd': 3, 'long_lev': 2},
    {'symbol': 'APTUSDT', 'short_ma': 350, 'short_sk': 117, 'short_sks': 53, 'short_sd': 24, 'long_ma': 91, 'long_sk': 114, 'long_sks': 28, 'long_sd': 38, 'long_lev': 1},
    {'symbol': 'XTZUSDT', 'short_ma': 350, 'short_sk': 117, 'short_sks': 58, 'short_sd': 28, 'long_ma': 313, 'long_sk': 90, 'long_sks': 33, 'long_sd': 11, 'long_lev': 2},
    {'symbol': 'ARCUSDT', 'short_ma': 69, 'short_sk': 84, 'short_sks': 7, 'short_sd': 46, 'long_ma': 194, 'long_sk': 148, 'long_sks': 80, 'long_sd': 50, 'long_lev': 5},
    {'symbol': 'ZILUSDT', 'short_ma': 319, 'short_sk': 67, 'short_sks': 20, 'short_sd': 8, 'long_ma': 72, 'long_sk': 43, 'long_sks': 6, 'long_sd': 12, 'long_lev': 2},
    {'symbol': 'ARUSDT', 'short_ma': 292, 'short_sk': 121, 'short_sks': 7, 'short_sd': 10, 'long_ma': 156, 'long_sk': 132, 'long_sks': 39, 'long_sd': 8, 'long_lev': 1},
    {'symbol': 'YFIUSDT', 'short_ma': 218, 'short_sk': 140, 'short_sks': 29, 'short_sd': 28, 'long_ma': 287, 'long_sk': 97, 'long_sks': 7, 'long_sd': 4, 'long_lev': 1},
    {'symbol': 'ALGOUSDT', 'short_ma': 347, 'short_sk': 149, 'short_sks': 12, 'short_sd': 26, 'long_ma': 58, 'long_sk': 120, 'long_sks': 22, 'long_sd': 39, 'long_lev': 1},
    {'symbol': 'DODOXUSDT', 'short_ma': 212, 'short_sk': 125, 'short_sks': 32, 'short_sd': 36, 'long_ma': 70, 'long_sk': 133, 'long_sks': 78, 'long_sd': 36, 'long_lev': 1},
    {'symbol': 'TAOUSDT', 'short_ma': 203, 'short_sk': 47, 'short_sks': 29, 'short_sd': 3, 'long_ma': 161, 'long_sk': 44, 'long_sks': 79, 'long_sd': 38, 'long_lev': 5},
    {'symbol': 'ALICEUSDT', 'short_ma': 94, 'short_sk': 54, 'short_sks': 7, 'short_sd': 24, 'long_ma': 306, 'long_sk': 15, 'long_sks': 80, 'long_sd': 35, 'long_lev': 1},
    {'symbol': 'COTIUSDT', 'short_ma': 289, 'short_sk': 116, 'short_sks': 32, 'short_sd': 6, 'long_ma': 52, 'long_sk': 98, 'long_sks': 27, 'long_sd': 14, 'long_lev': 1},
    {'symbol': 'LRCUSDT', 'short_ma': 201, 'short_sk': 144, 'short_sks': 35, 'short_sd': 50, 'long_ma': 223, 'long_sk': 15, 'long_sks': 24, 'long_sd': 18, 'long_lev': 2},
    {'symbol': 'CELRUSDT', 'short_ma': 91, 'short_sk': 150, 'short_sks': 32, 'short_sd': 47, 'long_ma': 60, 'long_sk': 103, 'long_sks': 31, 'long_sd': 30, 'long_lev': 4},
    {'symbol': 'NEOUSDT', 'short_ma': 348, 'short_sk': 30, 'short_sks': 46, 'short_sd': 36, 'long_ma': 350, 'long_sk': 96, 'long_sks': 57, 'long_sd': 13, 'long_lev': 2},
    {'symbol': 'KNCUSDT', 'short_ma': 77, 'short_sk': 136, 'short_sks': 32, 'short_sd': 3, 'long_ma': 282, 'long_sk': 94, 'long_sks': 28, 'long_sd': 14, 'long_lev': 1},
    {'symbol': '1INCHUSDT', 'short_ma': 202, 'short_sk': 106, 'short_sks': 59, 'short_sd': 30, 'long_ma': 76, 'long_sk': 40, 'long_sks': 23, 'long_sd': 4, 'long_lev': 1},
    {'symbol': 'MASKUSDT', 'short_ma': 125, 'short_sk': 143, 'short_sks': 68, 'short_sd': 20, 'long_ma': 260, 'long_sk': 124, 'long_sks': 23, 'long_sd': 21, 'long_lev': 2},
    {'symbol': 'QTUMUSDT', 'short_ma': 222, 'short_sk': 98, 'short_sks': 50, 'short_sd': 49, 'long_ma': 278, 'long_sk': 49, 'long_sks': 14, 'long_sd': 10, 'long_lev': 2},
    {'symbol': 'TRBUSDT', 'short_ma': 160, 'short_sk': 102, 'short_sks': 53, 'short_sd': 12, 'long_ma': 20, 'long_sk': 109, 'long_sks': 61, 'long_sd': 20, 'long_lev': 2},
    {'symbol': 'THETAUSDT', 'short_ma': 330, 'short_sk': 118, 'short_sks': 73, 'short_sd': 24, 'long_ma': 62, 'long_sk': 108, 'long_sks': 21, 'long_sd': 21, 'long_lev': 3},
    {'symbol': 'ETCUSDT', 'short_ma': 335, 'short_sk': 103, 'short_sks': 67, 'short_sd': 49, 'long_ma': 98, 'long_sk': 81, 'long_sks': 38, 'long_sd': 4, 'long_lev': 2},
    {'symbol': 'DOTUSDT', 'short_ma': 209, 'short_sk': 68, 'short_sks': 44, 'short_sd': 3, 'long_ma': 121, 'long_sk': 87, 'long_sks': 34, 'long_sd': 9, 'long_lev': 2},
    {'symbol': 'STORJUSDT', 'short_ma': 79, 'short_sk': 44, 'short_sks': 47, 'short_sd': 15, 'long_ma': 64, 'long_sk': 71, 'long_sks': 23, 'long_sd': 13, 'long_lev': 2},
    {'symbol': 'ICPUSDT', 'short_ma': 147, 'short_sk': 133, 'short_sks': 34, 'short_sd': 32, 'long_ma': 58, 'long_sk': 135, 'long_sks': 56, 'long_sd': 15, 'long_lev': 2},
    {'symbol': 'LQTYUSDT', 'short_ma': 77, 'short_sk': 75, 'short_sks': 59, 'short_sd': 30, 'long_ma': 275, 'long_sk': 82, 'long_sks': 12, 'long_sd': 40, 'long_lev': 2},
    {'symbol': 'DUSKUSDT', 'short_ma': 108, 'short_sk': 109, 'short_sks': 64, 'short_sd': 39, 'long_ma': 39, 'long_sk': 148, 'long_sks': 57, 'long_sd': 25, 'long_lev': 1},
    {'symbol': 'ACHUSDT', 'short_ma': 63, 'short_sk': 129, 'short_sks': 80, 'short_sd': 35, 'long_ma': 72, 'long_sk': 31, 'long_sks': 22, 'long_sd': 25, 'long_lev': 5},
    {'symbol': 'AAVEUSDT', 'short_ma': 131, 'short_sk': 131, 'short_sks': 76, 'short_sd': 29, 'long_ma': 272, 'long_sk': 67, 'long_sks': 19, 'long_sd': 24, 'long_lev': 1},
    {'symbol': 'XLMUSDT', 'short_ma': 335, 'short_sk': 84, 'short_sks': 74, 'short_sd': 48, 'long_ma': 80, 'long_sk': 27, 'long_sks': 20, 'long_sd': 30, 'long_lev': 2},
    {'symbol': 'COMPUSDT', 'short_ma': 313, 'short_sk': 93, 'short_sks': 37, 'short_sd': 20, 'long_ma': 111, 'long_sk': 53, 'long_sks': 30, 'long_sd': 38, 'long_lev': 1},
    {'symbol': 'STGUSDT', 'short_ma': 200, 'short_sk': 94, 'short_sks': 33, 'short_sd': 23, 'long_ma': 79, 'long_sk': 18, 'long_sks': 30, 'long_sd': 10, 'long_lev': 4},
    {'symbol': 'JASMYUSDT', 'short_ma': 41, 'short_sk': 54, 'short_sks': 24, 'short_sd': 28, 'long_ma': 122, 'long_sk': 28, 'long_sks': 24, 'long_sd': 12, 'long_lev': 5},
    {'symbol': 'GRTUSDT', 'short_ma': 135, 'short_sk': 32, 'short_sks': 75, 'short_sd': 40, 'long_ma': 318, 'long_sk': 19, 'long_sks': 28, 'long_sd': 7, 'long_lev': 2},
    {'symbol': 'ATOMUSDT', 'short_ma': 72, 'short_sk': 101, 'short_sks': 80, 'short_sd': 37, 'long_ma': 246, 'long_sk': 41, 'long_sks': 62, 'long_sd': 16, 'long_lev': 2},
    {'symbol': 'SCRTUSDT', 'short_ma': 72, 'short_sk': 49, 'short_sks': 5, 'short_sd': 39, 'long_ma': 223, 'long_sk': 20, 'long_sks': 27, 'long_sd': 3, 'long_lev': 5},
    {'symbol': 'DENTUSDT', 'short_ma': 148, 'short_sk': 76, 'short_sks': 65, 'short_sd': 17, 'long_ma': 55, 'long_sk': 130, 'long_sks': 44, 'long_sd': 17, 'long_lev': 2},
    {'symbol': 'TLMUSDT', 'short_ma': 93, 'short_sk': 139, 'short_sks': 59, 'short_sd': 36, 'long_ma': 69, 'long_sk': 141, 'long_sks': 20, 'long_sd': 26, 'long_lev': 4},
    {'symbol': '1000RATSUSDT', 'short_ma': 28, 'short_sk': 93, 'short_sks': 61, 'short_sd': 7, 'long_ma': 28, 'long_sk': 116, 'long_sks': 31, 'long_sd': 32, 'long_lev': 1},
    {'symbol': 'CHZUSDT', 'short_ma': 278, 'short_sk': 132, 'short_sks': 11, 'short_sd': 15, 'long_ma': 129, 'long_sk': 92, 'long_sks': 78, 'long_sd': 21, 'long_lev': 3},
    {'symbol': 'ZENUSDT', 'short_ma': 298, 'short_sk': 126, 'short_sks': 11, 'short_sd': 17, 'long_ma': 260, 'long_sk': 61, 'long_sks': 28, 'long_sd': 14, 'long_lev': 1},
    {'symbol': 'CHRUSDT', 'short_ma': 100, 'short_sk': 115, 'short_sks': 68, 'short_sd': 46, 'long_ma': 144, 'long_sk': 58, 'long_sks': 45, 'long_sd': 11, 'long_lev': 1},
    {'symbol': 'INJUSDT', 'short_ma': 337, 'short_sk': 21, 'short_sks': 56, 'short_sd': 12, 'long_ma': 139, 'long_sk': 72, 'long_sks': 68, 'long_sd': 23, 'long_lev': 4},
    {'symbol': 'SSVUSDT', 'short_ma': 200, 'short_sk': 140, 'short_sks': 62, 'short_sd': 30, 'long_ma': 75, 'long_sk': 18, 'long_sks': 12, 'long_sd': 42, 'long_lev': 3},
    {'symbol': 'ZECUSDT', 'short_ma': 164, 'short_sk': 52, 'short_sks': 41, 'short_sd': 15, 'long_ma': 136, 'long_sk': 95, 'long_sks': 38, 'long_sd': 6, 'long_lev': 2},
    {'symbol': 'CRVUSDT', 'short_ma': 273, 'short_sk': 137, 'short_sks': 45, 'short_sd': 27, 'long_ma': 131, 'long_sk': 21, 'long_sks': 12, 'long_sd': 23, 'long_lev': 2},

    {'symbol': '1000WHYUSDT', 'short_ma': 114, 'short_sk': 97, 'short_sks': 56, 'short_sd': 15, 'long_ma': 40, 'long_sk': 91, 'long_sks': 57, 'long_sd': 19, 'long_lev': 1},
    {'symbol': 'BELUSDT', 'short_ma': 223, 'short_sk': 135, 'short_sks': 8, 'short_sd': 11, 'long_ma': 23, 'long_sk': 96, 'long_sks': 77, 'long_sd': 12, 'long_lev': 1},
    {'symbol': 'C98USDT', 'short_ma': 210, 'short_sk': 29, 'short_sks': 76, 'short_sd': 47, 'long_ma': 69, 'long_sk': 58, 'long_sks': 77, 'long_sd': 48, 'long_lev': 1},
    {'symbol': 'CHILLGUYUSDT', 'short_ma': 126, 'short_sk': 96, 'short_sks': 76, 'short_sd': 27, 'long_ma': 153, 'long_sk': 93, 'long_sks': 18, 'long_sd': 18, 'long_lev': 4},
    {'symbol': 'DYMUSDT', 'short_ma': 301, 'short_sk': 150, 'short_sks': 70, 'short_sd': 46, 'long_ma': 48, 'long_sk': 80, 'long_sks': 60, 'long_sd': 19, 'long_lev': 2},
    {'symbol': 'GRASSUSDT', 'short_ma': 213, 'short_sk': 108, 'short_sks': 73, 'short_sd': 12, 'long_ma': 151, 'long_sk': 120, 'long_sks': 32, 'long_sd': 36, 'long_lev': 1},
    {'symbol': 'HOOKUSDT', 'short_ma': 321, 'short_sk': 135, 'short_sks': 71, 'short_sd': 44, 'long_ma': 33, 'long_sk': 114, 'long_sks': 17, 'long_sd': 49, 'long_lev': 1},
    {'symbol': 'JTOUSDT', 'short_ma': 126, 'short_sk': 145, 'short_sks': 78, 'short_sd': 25, 'long_ma': 112, 'long_sk': 120, 'long_sks': 34, 'long_sd': 44, 'long_lev': 1},
    {'symbol': 'MOVEUSDT', 'short_ma': 48, 'short_sk': 59, 'short_sks': 62, 'short_sd': 33, 'long_ma': 332, 'long_sk': 131, 'long_sks': 57, 'long_sd': 41, 'long_lev': 3},
    {'symbol': 'OGNUSDT', 'short_ma': 118, 'short_sk': 119, 'short_sks': 73, 'short_sd': 48, 'long_ma': 270, 'long_sk': 42, 'long_sks': 29, 'long_sd': 23, 'long_lev': 1},
    {'symbol': 'ONTUSDT', 'short_ma': 127, 'short_sk': 150, 'short_sks': 60, 'short_sd': 42, 'long_ma': 109, 'long_sk': 130, 'long_sks': 21, 'long_sd': 18, 'long_lev': 1},
    {'symbol': 'OXTUSDT', 'short_ma': 283, 'short_sk': 138, 'short_sks': 60, 'short_sd': 38, 'long_ma': 181, 'long_sk': 46, 'long_sks': 14, 'long_sd': 31, 'long_lev': 1},
    {'symbol': 'RDNTUSDT', 'short_ma': 244, 'short_sk': 63, 'short_sks': 14, 'short_sd': 6, 'long_ma': 204, 'long_sk': 149, 'long_sks': 56, 'long_sd': 49, 'long_lev': 1},
    {'symbol': 'SUSDT', 'short_ma': 141, 'short_sk': 145, 'short_sks': 77, 'short_sd': 47, 'long_ma': 310, 'long_sk': 147, 'long_sks': 51, 'long_sd': 50, 'long_lev': 5},
    {'symbol': 'USTCUSDT', 'short_ma': 141, 'short_sk': 144, 'short_sks': 55, 'short_sd': 10, 'long_ma': 39, 'long_sk': 78, 'long_sks': 75, 'long_sd': 17, 'long_lev': 1},
    {'symbol': 'VANAUSDT', 'short_ma': 101, 'short_sk': 150, 'short_sks': 79, 'short_sd': 47, 'long_ma': 56, 'long_sk': 18, 'long_sks': 80, 'long_sd': 31, 'long_lev': 1},
]

# Futures 롱 매매 제외 코인 (CSV 필터링 완료 - 별도 제외 불필요)
LONG_EXCLUDED_COINS = []

# ============================================================
# 코인별 우선순위 설정 (롱/숏 우선 최적화 결과)
# 'long': 롱 우선 (롱 신호 먼저 확인)
# 'short': 숏 우선 (숏 신호 먼저 확인, 기본값)
# ============================================================

COIN_PRIORITY = {
    'SUSDT': 'short',
    'SOLVUSDT': 'short',
    'RAYSOLUSDT': 'long',
    'BERAUSDT': 'short',
    'DUSDT': 'short',
    'CGPTUSDT': 'short',
    '1000000MOGUSDT': 'short',
    'VELODROMEUSDT': 'short',
    'PENGUUSDT': 'short',
    'AIXBTUSDT': 'short',
    'MEUSDT': 'short',
    'SONICUSDT': 'long',
    'AEROUSDT': 'short',
    'FARTCOINUSDT': 'short',
    'CETUSUSDT': 'short',
    'VTHOUSDT': 'short',
    'PNUTUSDT': 'short',
    'VINEUSDT': 'short',
    'MOVEUSDT': 'short',
    'MEWUSDT': 'short',
    'PHAUSDT': 'short',
    'VIRTUALUSDT': 'short',
    'TRUMPUSDT': 'short',
    '1000CATUSDT': 'short',
    'ZKUSDT': 'short',
    'DEXEUSDT': 'long',
    'GOATUSDT': 'short',
    'EIGENUSDT': 'short',
    'VANRYUSDT': 'short',
    'COOKIEUSDT': 'long',
    'BOMEUSDT': 'short',
    'SWARMSUSDT': 'long',
    'SYNUSDT': 'short',
    'DEGENUSDT': 'short',
    'HIVEUSDT': 'short',
    'BIOUSDT': 'short',
    '1MBABYDOGEUSDT': 'short',
    'ACXUSDT': 'long',
    'SYSUSDT': 'short',
    'VVVUSDT': 'short',
    'HMSTRUSDT': 'long',
    'NOTUSDT': 'short',
    'GRIFFAINUSDT': 'short',
    'KOMAUSDT': 'short',
    'AVAAIUSDT': 'short',
    'VANAUSDT': 'short',
    'SAGAUSDT': 'long',
    'PIXELUSDT': 'long',
    'PROMUSDT': 'short',
    'DRIFTUSDT': 'short',
    'BRETTUSDT': 'long',
    'POLUSDT': 'short',
    'AKTUSDT': 'short',
    'SCRUSDT': 'short',
    'KAIAUSDT': 'short',
    'SPXUSDT': 'short',
    'FIDAUSDT': 'long',
    'RPLUSDT': 'short',
    'ANIMEUSDT': 'short',
    'TURBOUSDT': 'short',
    'KMNOUSDT': 'short',
    'ENAUSDT': 'short',
    'PIPPINUSDT': 'short',
    'POPCATUSDT': 'short',
    'ACTUSDT': 'short',
    'NFPUSDT': 'short',
    'ZETAUSDT': 'short',
    'MOCAUSDT': 'short',
    'AEVOUSDT': 'short',
    'DEGOUSDT': 'short',
    'USUALUSDT': 'short',
    'IOUSDT': 'short',
    'GRASSUSDT': 'short',
    'RAREUSDT': 'short',
    'HIPPOUSDT': 'short',
    'ALTUSDT': 'short',
    'PORTALUSDT': 'short',
    'ORCAUSDT': 'short',
    'MBOXUSDT': 'long',
    'BANANAUSDT': 'short',
    'RONINUSDT': 'short',
    'RENDERUSDT': 'short',
    'NTRNUSDT': 'short',
    'AIUSDT': 'short',
    'WUSDT': 'short',
    'DYMUSDT': 'short',
    '1000WHYUSDT': 'short',
    'BLURUSDT': 'short',
    'LSKUSDT': 'long',
    'CHILLGUYUSDT': 'short',
    'BBUSDT': 'long',
    'GUSDT': 'short',
    'WIFUSDT': 'long',
    '1000CHEEMSUSDT': 'short',
    'FLUXUSDT': 'short',
    'DIAUSDT': 'short',
    'METISUSDT': 'long',
    'BICOUSDT': 'short',
    'STRKUSDT': 'short',
    'PYTHUSDT': 'short',
    'COSUSDT': 'short',
    'ETHWUSDT': 'long',
    'TNSRUSDT': 'short',
    'MEMEUSDT': 'long',
    'LUMIAUSDT': 'short',
    'SEIUSDT': 'short',
    'REZUSDT': 'short',
    'CATIUSDT': 'short',
    'MOVRUSDT': 'short',
    'BIGTIMEUSDT': 'short',
    'AVAUSDT': 'short',
    'MELANIAUSDT': 'short',
    'MOODENGUSDT': 'short',
    'NEIROUSDT': 'short',
    'POLYXUSDT': 'short',
    'IDUSDT': 'short',
    'TONUSDT': 'short',
    'SAFEUSDT': 'long',
    'WAXPUSDT': 'short',
    'FIOUSDT': 'long',
    'XAIUSDT': 'short',
    'ILVUSDT': 'short',
    'HFTUSDT': 'short',
    '1000FLOKIUSDT': 'short',
    'STEEMUSDT': 'short',
    'ACEUSDT': 'short',
    'ARKMUSDT': 'short',
    'CAKEUSDT': 'short',
    'ETHFIUSDT': 'short',
    'ARBUSDT': 'long',
    'BEAMXUSDT': 'short',
    'THEUSDT': 'short',
    '1000BONKUSDT': 'short',
    'DOGSUSDT': 'short',
    'CYBERUSDT': 'short',
    'LISTAUSDT': 'short',
    'BNTUSDT': 'short',
    'OXTUSDT': 'short',
    'RIFUSDT': 'short',
    'FLOWUSDT': 'short',
    'POWRUSDT': 'short',
    'SUIUSDT': 'short',
    'ORDIUSDT': 'short',
    'APEUSDT': 'short',
    'USTCUSDT': 'short',
    'COWUSDT': 'short',
    'MANAUSDT': 'short',
    'YGGUSDT': 'long',
    'HIGHUSDT': 'short',
    'ONDOUSDT': 'short',
    'SUNUSDT': 'long',
    'LUNA2USDT': 'short',
    'ZROUSDT': 'short',
    'MAVUSDT': 'long',
    'MAGICUSDT': 'short',
    'AXLUSDT': 'short',
    '1000PEPEUSDT': 'short',
    'GALAUSDT': 'short',
    'ONEUSDT': 'short',
    'JTOUSDT': 'short',
    'AUCTIONUSDT': 'short',
    'ALCHUSDT': 'short',
    'ASTRUSDT': 'short',
    'GMTUSDT': 'short',
    'OGNUSDT': 'short',
    'WLDUSDT': 'long',
    'RDNTUSDT': 'short',
    'TIAUSDT': 'short',
    'ZEREBROUSDT': 'short',
    'JOEUSDT': 'short',
    'SANDUSDT': 'short',
    'AGLDUSDT': 'short',
    'KASUSDT': 'short',
    'OPUSDT': 'short',
    'MANTAUSDT': 'short',
    'PENDLEUSDT': 'short',
    'STXUSDT': 'short',
    'HBARUSDT': 'short',
    'HOOKUSDT': 'short',
    'ICXUSDT': 'short',
    'MINAUSDT': 'short',
    'BSVUSDT': 'short',
    '1000LUNCUSDT': 'short',
    'CKBUSDT': 'short',
    'FETUSDT': 'long',
    'PHBUSDT': 'short',
    'SANTOSUSDT': 'short',
    'SUSHIUSDT': 'short',
    'EDUUSDT': 'long',
    'JUPUSDT': 'long',
    '1000SATSUSDT': 'short',
    'ONGUSDT': 'long',
    'IMXUSDT': 'short',
    'AXSUSDT': 'short',
    'MORPHOUSDT': 'long',
    'WOOUSDT': 'short',
    'API3USDT': 'short',
    '1000XECUSDT': 'long',
    'SKLUSDT': 'long',
    'C98USDT': 'short',
    'IOSTUSDT': 'short',
    'CTSIUSDT': 'short',
    'ENJUSDT': 'long',
    'CFXUSDT': 'short',
    'IOTXUSDT': 'long',
    'VETUSDT': 'short',
    '1000SHIBUSDT': 'short',
    'XVGUSDT': 'short',
    'GLMUSDT': 'short',
    'ANKRUSDT': 'short',
    'RVNUSDT': 'short',
    'ROSEUSDT': 'long',
    'AVAXUSDT': 'long',
    'KSMUSDT': 'short',
    'HOTUSDT': 'short',
    'ENSUSDT': 'short',
    'TUSDT': 'short',
    'IOTAUSDT': 'short',
    'GTCUSDT': 'long',
    'NEARUSDT': 'short',
    'TWTUSDT': 'short',
    'SPELLUSDT': 'long',
    'RSRUSDT': 'short',
    'FILUSDT': 'long',
    'TRUUSDT': 'long',
    'GASUSDT': 'short',
    'SNXUSDT': 'short',
    'SUPERUSDT': 'long',
    'LDOUSDT': 'short',
    'GMXUSDT': 'long',
    'ZRXUSDT': 'long',
    'ATAUSDT': 'long',
    'XVSUSDT': 'short',
    'LPTUSDT': 'short',
    'EGLDUSDT': 'short',
    'CELOUSDT': 'short',
    'NMRUSDT': 'short',
    'DYDXUSDT': 'long',
    'QNTUSDT': 'short',
    'ARKUSDT': 'short',
    'RUNEUSDT': 'short',
    'APTUSDT': 'short',
    'XTZUSDT': 'short',
    'ARCUSDT': 'short',
    'ZILUSDT': 'short',
    'ARUSDT': 'long',
    'YFIUSDT': 'short',
    'ALGOUSDT': 'short',
    'DODOXUSDT': 'short',
    'ONTUSDT': 'short',
    'TAOUSDT': 'long',
    'ALICEUSDT': 'short',
    'COTIUSDT': 'long',
    'LRCUSDT': 'short',
    'CELRUSDT': 'short',
    'NEOUSDT': 'long',
    'KNCUSDT': 'short',
    '1INCHUSDT': 'short',
    'MASKUSDT': 'short',
    'QTUMUSDT': 'short',
    'TRBUSDT': 'short',
    'THETAUSDT': 'short',
    'ETCUSDT': 'short',
    'DOTUSDT': 'long',
    'STORJUSDT': 'short',
    'ICPUSDT': 'short',
    'LQTYUSDT': 'short',
    'DUSKUSDT': 'short',
    'ACHUSDT': 'short',
    'AAVEUSDT': 'short',
    'XLMUSDT': 'long',
    'COMPUSDT': 'long',
    'STGUSDT': 'long',
    'BELUSDT': 'short',
    'JASMYUSDT': 'short',
    'GRTUSDT': 'long',
    'ATOMUSDT': 'short',
    'SCRTUSDT': 'short',
    'DENTUSDT': 'short',
    'TLMUSDT': 'short',
    '1000RATSUSDT': 'long',
    'CHZUSDT': 'long',
    'ZENUSDT': 'short',
    'CHRUSDT': 'long',
    'INJUSDT': 'long',
    'SSVUSDT': 'short',
    'ZECUSDT': 'long',
    'CRVUSDT': 'short',
}



# Futures 전체 거래 코인 수 (숏 코인 수 기준 - 숏/롱 슬롯 공유)
# 포지션 사이징: 총 자산 / 숏 코인 수(289)로 균등 배분
TOTAL_FUTURES_COINS = len(SHORT_TRADING_CONFIGS)

# ============================================================
# 전역 변수
# ============================================================

futures_stoch_cache = {}
futures_stoch_cache_date = None
long_stoch_cache = {}
long_stoch_cache_date = None
spot_exchange = None  # BNB 충전용 Spot 거래소 연결
futures_exchange = None
runtime_excluded_coins = set()



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


def send_trade_summary(futures_open_list, futures_close_list,
                       futures_long_open_list, futures_long_close_list,
                       futures_total, futures_usdt, errors):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    msg = f"📊 <b>Futures 거래 종합 리포트</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"

    # Futures 자산
    msg += f"<b>📉📈 USDS-M Futures</b>\n"
    msg += f"💰 총 자산: <b>${futures_total:,.2f}</b>\n"
    msg += f"💵 USDT: ${futures_usdt:,.2f}\n"
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

    # Futures 롱 진입
    if futures_long_open_list:
        msg += f"🟢 <b>Futures 롱 진입 {len(futures_long_open_list)}건</b>\n"
        for item in futures_long_open_list[:10]:
            msg += f"  • {item['symbol']}: ${item['notional']:.2f} ({item['leverage']}x)\n"
        if len(futures_long_open_list) > 10:
            msg += f"  ... 외 {len(futures_long_open_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"

    # Futures 롱 청산
    if futures_long_close_list:
        msg += f"🔴 <b>Futures 롱 청산 {len(futures_long_close_list)}건</b>\n"
        for item in futures_long_close_list[:10]:
            pnl_sign = "+" if item['pnl'] >= 0 else ""
            emoji = "💚" if item['pnl'] >= 0 else "❤️"
            msg += f"  • {item['symbol']}: {pnl_sign}{item['pnl']:.2f} {emoji}\n"
        if len(futures_long_close_list) > 10:
            msg += f"  ... 외 {len(futures_long_close_list) - 10}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"

    # 거래 없음
    all_lists = [futures_open_list, futures_close_list,
                 futures_long_open_list, futures_long_close_list]
    if not any(all_lists):
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
    msg = f"🚀 <b>바이낸스 Futures 자동매매 봇 시작 (v5.0.0)</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"📉 Futures 숏: MA + 스토캐스틱 숏\n"
    msg += f"📈 Futures 롱: 숏필터 + 롱신호\n"
    msg += f"💰 수수료: 0.06% (Futures)\n"
    msg += f"🔶 BNB 자동충전 (Spot경유 → Futures)\n"
    msg += f"🔻 Futures 숏: {len(SHORT_TRADING_CONFIGS)}개\n"
    msg += f"🟢 Futures 롱: {len(LONG_TRADING_CONFIGS)}개\n"
    msg += f"📊 Futures 총 슬롯: {get_effective_futures_coins()}개 (제외 {TOTAL_FUTURES_COINS - get_effective_futures_coins()}개)\n"
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


def get_usdt_balance():
    """Spot USDT 잔고 조회"""
    try:
        balance = spot_exchange.fetch_balance()
        return float(balance['USDT']['free'])
    except Exception as e:
        logging.error(f"Spot USDT 잔고 조회 중 오류: {e}")
        return 0



# ============================================================
# Futures 관련 함수들 (새로 추가)
# ============================================================


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



def calculate_stochastic(df, k_period, k_smooth, d_period):
    """스토캐스틱 계산 (Slow Stochastic)
    Returns: (slow_k, slow_d) - 마지막 값, 실패 시 (None, None)
    """
    try:
        if df is None or len(df) < k_period + k_smooth + d_period:
            return None, None
        high = df['high']
        low = df['low']
        close = df['close']
        lowest_low = low.rolling(window=k_period).min()
        highest_high = high.rolling(window=k_period).max()
        fast_k = 100 * (close - lowest_low) / (highest_high - lowest_low)
        slow_k = fast_k.rolling(window=k_smooth).mean()
        slow_d = slow_k.rolling(window=d_period).mean()
        last_k = slow_k.iloc[-1]
        last_d = slow_d.iloc[-1]
        if pd.isna(last_k) or pd.isna(last_d):
            return None, None
        return float(last_k), float(last_d)
    except Exception as e:
        logging.error(f"스토캐스틱 계산 오류: {e}")
        return None, None



def get_futures_stochastic_signal(symbol):
    """Futures 스토캐스틱 신호 조회"""
    # 캐시 사용하지 않고 최신 값 계산
    config = next((c for c in SHORT_TRADING_CONFIGS if c['symbol'] == symbol), None)
    if not config:
        return None
    try:
        k_period = config['stoch_k_period']
        k_smooth = config['stoch_k_smooth']
        d_period = config['stoch_d_period']
        required_count = k_period + k_smooth + d_period + 20
        df = fetch_futures_ohlcv(symbol, '1d', required_count)
        if df is None:
            return None
        slow_k, slow_d = calculate_stochastic(df, k_period, k_smooth, d_period)
        if slow_k is None or slow_d is None:
            return None
        return {
            'short_signal': bool(slow_k < slow_d),
            'slow_k': slow_k,
            'slow_d': slow_d
        }
    except Exception as e:
        logging.error(f"Futures {symbol} 스토캐스틱 계산 중 오류: {e}")
        return None


# ============================================================
# Long 스토캐스틱 캐시 관리
# ============================================================


def get_long_stochastic_signal(symbol):
    """Long 스토캐스틱 신호 조회"""
    # 캐시 사용하지 않고 최신 값 계산
    config = next((c for c in LONG_TRADING_CONFIGS if c['symbol'] == symbol), None)
    if not config:
        return None
    try:
        short_k_period = config['short_sk']
        short_k_smooth = config['short_sks']
        short_d_period = config['short_sd']
        long_k_period = config['long_sk']
        long_k_smooth = config['long_sks']
        long_d_period = config['long_sd']
        short_required = short_k_period + short_k_smooth + short_d_period + 20
        long_required = long_k_period + long_k_smooth + long_d_period + 20
        required_count = max(short_required, long_required)
        df = fetch_futures_ohlcv(symbol, '1d', required_count)
        if df is None:
            return None
        short_slow_k, short_slow_d = calculate_stochastic(df, short_k_period, short_k_smooth, short_d_period)
        long_slow_k, long_slow_d = calculate_stochastic(df, long_k_period, long_k_smooth, long_d_period)
        cache_entry = {}
        if short_slow_k is not None and short_slow_d is not None:
            cache_entry['short_filter_signal'] = bool(short_slow_k < short_slow_d)
            cache_entry['short_slow_k'] = short_slow_k
            cache_entry['short_slow_d'] = short_slow_d
        if long_slow_k is not None and long_slow_d is not None:
            cache_entry['long_signal'] = bool(long_slow_k > long_slow_d)
            cache_entry['long_slow_k'] = long_slow_k
            cache_entry['long_slow_d'] = long_slow_d
        return cache_entry if cache_entry else None
    except Exception as e:
        logging.error(f"Long {symbol} 스토캐스틱 계산 중 오류: {e}")
        return None


def _normalize_symbol(symbol):
    """ccxt 통합 심볼을 거래소 네이티브 포맷으로 변환
    예: 'AERO/USDT:USDT' → 'AEROUSDT', 'AEROUSDT' → 'AEROUSDT'
    """
    if '/' in symbol:
        return symbol.replace('/', '').split(':')[0]
    return symbol


def _safe_float(value, default=0):
    """None-safe float 변환"""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value, default=1):
    """None-safe int 변환"""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def get_futures_position(symbol):
    """Futures 포지션 조회"""
    try:
        positions = futures_exchange.fetch_positions([symbol])
        for pos in positions:
            normalized = _normalize_symbol(pos['symbol'])
            if normalized == symbol and abs(_safe_float(pos.get('contracts'))) > 0:
                return {
                    'symbol': symbol,
                    'side': pos['side'],  # 'short' or 'long'
                    'contracts': abs(_safe_float(pos.get('contracts'))),
                    'notional': abs(_safe_float(pos.get('notional'))),
                    'unrealized_pnl': _safe_float(pos.get('unrealizedPnl')),
                    'entry_price': _safe_float(pos.get('entryPrice')),
                    'leverage': _safe_int(pos.get('leverage')),
                    'liquidation_price': _safe_float(pos.get('liquidationPrice'))
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
            if abs(_safe_float(pos.get('contracts'))) > 0:
                active_positions.append({
                    'symbol': _normalize_symbol(pos['symbol']),
                    'side': pos['side'],
                    'contracts': abs(_safe_float(pos.get('contracts'))),
                    'notional': abs(_safe_float(pos.get('notional'))),
                    'unrealized_pnl': _safe_float(pos.get('unrealizedPnl')),
                    'entry_price': _safe_float(pos.get('entryPrice')),
                    'leverage': _safe_int(pos.get('leverage'))
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


def get_effective_futures_coins():
    """실제 거래 가능한 Futures 코인 수 (정적 제외 + 런타임 제외 반영)"""
    excluded_count = 0
    all_symbols = set(c['symbol'] for c in SHORT_TRADING_CONFIGS)
    for symbol in all_symbols:
        if symbol in FUTURES_EXCLUDED_COINS or symbol in runtime_excluded_coins:
            excluded_count += 1
    effective = TOTAL_FUTURES_COINS - excluded_count
    return max(effective, 1)


def count_futures_empty_slots():
    """Futures 포지션이 없는 슬롯 수 계산 (롱+숏 통합)"""
    active_symbols = set()
    positions = get_all_futures_positions()
    for pos in positions:
        active_symbols.add(pos['symbol'])

    effective_total = get_effective_futures_coins()
    empty_count = effective_total - len(active_symbols)
    return max(empty_count, 0)


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
    특정 심볼에 대한 Futures 투자 금액 계산 (롱/숏 통합)
    - 해당 심볼에 이미 포지션이 있으면 0 반환 (중복 진입 방지)
    - 빈 슬롯 = 거래가능 코인 수 - (롱 포지션 수 + 숏 포지션 수)
    """
    balance = get_futures_balance()
    usdt_free = balance['free']

    # 모든 활성 포지션 조회
    positions = get_all_futures_positions()
    active_symbols = set(pos['symbol'] for pos in positions)

    # 해당 심볼에 이미 포지션이 있으면 0 반환
    if symbol in active_symbols:
        logging.info(f"[{symbol}] 이미 포지션 보유 중 - 추가 진입 불가")
        return 0

    # 실제 거래 가능 코인 수 (제외 코인 반영)
    effective_total = get_effective_futures_coins()

    # 빈 슬롯(포지션 없는 코인) 수 계산 (롱+숏 통합)
    empty_count = effective_total - len(active_symbols)

    if empty_count <= 0:
        logging.info(f"[{symbol}] 모든 슬롯에 포지션 보유 중")
        return 0

    # 가용 잔고의 99.5% 사용 (수수료 여유)
    available = usdt_free * 0.995

    # 빈 슬롯에 균등 배분
    invest_per_slot = available / empty_count

    # 최대 투자금 제한: 총 자산 / 거래가능 코인 수
    total_equity = balance['total']
    max_per_slot = total_equity / effective_total

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

    # 최대 투자금 제한: 총 자산 / 거래가능 코인 수
    total_equity = balance['total']
    effective_total = get_effective_futures_coins()
    max_per_slot = total_equity / effective_total

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
        # 상폐 예정 코인 자동 제외 (-4140: Invalid symbol status)
        if '-4140' in str(e):
            runtime_excluded_coins.add(symbol)
            logging.warning(f"⚠️ [{symbol}] 런타임 제외 목록에 추가 (상폐 예정)")
        return None


def close_short_position(symbol, reason=None):
    """숏 포지션 청산"""
    try:
        pos = get_futures_position(symbol)
        if not pos or pos['side'] != 'short':
            logging.info(f"[{symbol}] 청산할 숏 포지션 없음")
            return None

        quantity = pos['contracts']
        entry_price = pos['entry_price']
        unrealized_pnl = pos['unrealized_pnl']

        # 시장가 매수로 숏 청산 (reduceOnly: $5 미만 포지션도 청산 가능)
        order = futures_exchange.create_market_buy_order(symbol, quantity, params={'reduceOnly': True})

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


def open_long_position(config):
    """롱 포지션 진입"""
    symbol = config['symbol']
    leverage = config['long_lev']

    try:
        # 투자 금액 계산 (포지션 중복 체크 포함)
        invest_amount = calculate_futures_invest_amount_for_symbol(symbol)
        if invest_amount <= 0:
            return None

        if invest_amount < FUTURES_MIN_ORDER_USDT:
            logging.warning(f"[{symbol}] 롱 투자 금액 부족: ${invest_amount:.2f}")
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

        # 수량 계산 - config에 leverage 키가 아닌 long_lev를 사용하므로 임시 config 생성
        temp_config = dict(config)
        temp_config['leverage'] = leverage
        quantity = calculate_futures_position_size(temp_config, invest_amount, current_price)
        if quantity <= 0:
            logging.warning(f"[{symbol}] 롱 수량 계산 실패")
            return None

        # 시장가 롱 주문
        order = futures_exchange.create_market_buy_order(symbol, quantity)

        logging.info(f"🟢 [{symbol}] 롱 진입 완료: {quantity} @ ~${current_price:.2f} ({leverage}x)")

        return {
            'symbol': symbol,
            'side': 'long',
            'quantity': quantity,
            'price': current_price,
            'notional': quantity * current_price,
            'leverage': leverage
        }

    except Exception as e:
        logging.error(f"❌ [{symbol}] 롱 진입 실패: {e}")
        # 상폐 예정 코인 자동 제외 (-4140: Invalid symbol status)
        if '-4140' in str(e):
            runtime_excluded_coins.add(symbol)
            logging.warning(f"⚠️ [{symbol}] 런타임 제외 목록에 추가 (상폐 예정)")
        return None


def close_long_position(symbol, reason=None):
    """롱 포지션 청산"""
    try:
        pos = get_futures_position(symbol)
        if not pos or pos['side'] != 'long':
            logging.info(f"[{symbol}] 청산할 롱 포지션 없음")
            return None

        quantity = pos['contracts']
        entry_price = pos['entry_price']
        unrealized_pnl = pos['unrealized_pnl']

        # 시장가 매도로 롱 청산 (reduceOnly: $5 미만 포지션도 청산 가능)
        order = futures_exchange.create_market_sell_order(symbol, quantity, params={'reduceOnly': True})

        current_price = get_futures_current_price(symbol)

        reason_str = f" ({reason})" if reason else ""
        pnl_str = f"+{unrealized_pnl:.2f}" if unrealized_pnl >= 0 else f"{unrealized_pnl:.2f}"

        logging.info(f"🔴 [{symbol}] 롱 청산 완료{reason_str}: {quantity} @ ~${current_price:.2f} (PnL: {pnl_str})")

        return {
            'symbol': symbol,
            'quantity': quantity,
            'entry_price': entry_price,
            'exit_price': current_price,
            'pnl': unrealized_pnl,
            'reason': reason
        }

    except Exception as e:
        logging.error(f"❌ [{symbol}] 롱 청산 실패: {e}")
        return None


# ============================================================
# 메인 거래 전략
# ============================================================

def futures_trade_strategy():
    """Futures 숏+롱 거래 전략"""
    global futures_exchange
    short_open_list, short_close_list = [], []
    long_open_list, long_close_list = [], []
    errors = []

    try:
        if futures_exchange is None:
            logging.info("📡 Futures 거래소 재초기화 시도...")
            if not init_futures_exchange():
                logging.warning("⚠️ Futures 거래소 초기화 실패")
                return short_open_list, short_close_list, long_open_list, long_close_list, errors

        logging.info("✅ Futures API 연결 정상")

        # Futures BNB 자동 충전
        check_and_recharge_futures_bnb()

        balance = get_futures_balance()
        logging.info("=" * 80)
        logging.info(f"📉📈 Futures 거래 - 총자산: ${balance['total']:,.2f}, 가용: ${balance['free']:,.2f}")
        logging.info("=" * 80)

        # 현재 모든 포지션 조회
        all_positions = get_all_futures_positions()
        active_position_map = {}  # symbol -> side
        for pos in all_positions:
            active_position_map[pos['symbol']] = pos['side']

        # ─── 코인별 통합 설정 맵 생성 ───
        short_config_map = {}  # symbol -> short config
        for cfg in SHORT_TRADING_CONFIGS:
            short_config_map[cfg['symbol']] = cfg

        long_config_map = {}  # symbol -> long config
        for cfg in LONG_TRADING_CONFIGS:
            long_config_map[cfg['symbol']] = cfg

        # 모든 고유 심볼 수집 (숏 코인 기준 순서 유지, 롱 전용은 뒤에 추가)
        all_symbols = []
        seen = set()
        for cfg in SHORT_TRADING_CONFIGS:
            if cfg['symbol'] not in seen:
                all_symbols.append(cfg['symbol'])
                seen.add(cfg['symbol'])
        for cfg in LONG_TRADING_CONFIGS:
            if cfg['symbol'] not in seen:
                all_symbols.append(cfg['symbol'])
                seen.add(cfg['symbol'])

        logging.info("─" * 40)
        logging.info(f"🔄 코인별 통합 전략 시작 (총 {len(all_symbols)}개)")
        if runtime_excluded_coins:
            logging.info(f"⚠️ 런타임 제외 코인: {', '.join(sorted(runtime_excluded_coins))}")
        logging.info("─" * 40)

        # ─── 코인별 통합 루프 ───
        for symbol in all_symbols:
            # 제외 코인 체크
            if symbol in FUTURES_EXCLUDED_COINS or symbol in runtime_excluded_coins:
                continue

            try:
                time.sleep(0.15)

                short_config = short_config_map.get(symbol)
                long_config = long_config_map.get(symbol)

                current_price = get_futures_current_price(symbol)
                if current_price is None:
                    continue

                # 현재 포지션 확인
                pos = get_futures_position(symbol)
                current_side = None  # 'short', 'long', or None
                if pos:
                    current_side = pos['side']

                # ── 숏 신호 계산 ──
                final_short_condition = False
                short_ma_condition = False
                short_stoch_condition = False
                if short_config:
                    ma_period = short_config['ma_period']
                    ma_price = get_futures_ma_price(symbol, ma_period)
                    stoch_data = get_futures_stochastic_signal(symbol)

                    if ma_price is not None:
                        short_ma_condition = current_price < ma_price
                        short_stoch_condition = stoch_data['short_signal'] if stoch_data and stoch_data.get('short_signal') is not None else False
                        final_short_condition = short_ma_condition and short_stoch_condition

                        logging.info(f"[숏][{symbol}] 현재가: ${current_price:.4f}, MA{ma_period}: ${ma_price:.4f}")
                        if stoch_data:
                            logging.info(f"[숏][{symbol}] Stoch K: {stoch_data['slow_k']:.2f}, D: {stoch_data['slow_d']:.2f}")
                        logging.info(f"[숏][{symbol}] 조건: MA({short_ma_condition}) AND Stoch({short_stoch_condition}) = {final_short_condition}")

                # ── 롱 신호 계산 ──
                final_long_condition = False
                short_filter_active = False
                long_signal_active = False
                if long_config:
                    long_ma_period = long_config['long_ma']
                    short_ma_period = long_config['short_ma']

                    short_ma_price = get_futures_ma_price(symbol, short_ma_period)
                    long_ma_price = get_futures_ma_price(symbol, long_ma_period)
                    long_stoch_data = get_long_stochastic_signal(symbol)

                    if short_ma_price is not None and long_ma_price is not None and long_stoch_data is not None:
                        short_ma_cond = current_price < short_ma_price
                        short_stoch_cond = long_stoch_data.get('short_filter_signal', False)
                        short_filter_active = short_ma_cond and short_stoch_cond

                        long_ma_cond = current_price > long_ma_price
                        long_stoch_cond = long_stoch_data.get('long_signal', False)
                        long_signal_active = long_ma_cond and long_stoch_cond

                        final_long_condition = (not short_filter_active) and long_signal_active

                        logging.info(f"[롱][{symbol}] 현재가: ${current_price:.4f}, Short MA{short_ma_period}: ${short_ma_price:.4f}, Long MA{long_ma_period}: ${long_ma_price:.4f}")
                        if long_stoch_data:
                            sk = long_stoch_data.get('short_slow_k', 0)
                            sd = long_stoch_data.get('short_slow_d', 0)
                            lk = long_stoch_data.get('long_slow_k', 0)
                            ld = long_stoch_data.get('long_slow_d', 0)
                            logging.info(f"[롱][{symbol}] ShortFilter K:{sk:.2f}/D:{sd:.2f}, LongSignal K:{lk:.2f}/D:{ld:.2f}")
                        logging.info(f"[롱][{symbol}] ShortFilter:{short_filter_active}, LongSignal:{long_signal_active} → 진입:{final_long_condition}")

                # ── 의사결정 (코인별 우선순위 적용) ──
                coin_priority = COIN_PRIORITY.get(symbol, 'short')

                # 우선순위에 따라 1차/2차 조건 결정
                if coin_priority == 'long':
                    first_condition = final_long_condition
                    second_condition = final_short_condition
                    first_side = 'long'
                    second_side = 'short'
                else:
                    first_condition = final_short_condition
                    second_condition = final_long_condition
                    first_side = 'short'
                    second_side = 'long'

                if first_condition:
                    # 1차 우선 신호 ON
                    if current_side == first_side:
                        logging.info(f"[{symbol}] ➡️ {first_side} 포지션 유지 ({coin_priority}우선)")
                    elif current_side == second_side:
                        # 반대 포지션 청산 → 우선 포지션 진입
                        logging.info(f"[{symbol}] 🔄 {second_side}→{first_side} 전환 ({coin_priority}우선)")
                        if second_side == 'long':
                            close_result = close_long_position(symbol, f"{first_side} 신호 발생 - 포지션 전환")
                            if close_result:
                                long_close_list.append(close_result)
                        else:
                            close_result = close_short_position(symbol, f"{first_side} 신호 발생 - 포지션 전환")
                            if close_result:
                                short_close_list.append(close_result)
                        if first_side == 'short':
                            open_result = open_short_position(short_config)
                            if open_result:
                                short_open_list.append(open_result)
                                active_position_map[symbol] = 'short'
                        else:
                            open_result = open_long_position(long_config)
                            if open_result:
                                long_open_list.append(open_result)
                                active_position_map[symbol] = 'long'
                    else:
                        # 현금 → 우선 포지션 진입
                        if first_side == 'short':
                            result = open_short_position(short_config)
                            if result:
                                short_open_list.append(result)
                                active_position_map[symbol] = 'short'
                        else:
                            result = open_long_position(long_config)
                            if result:
                                long_open_list.append(result)
                                active_position_map[symbol] = 'long'

                elif second_condition:
                    # 2차 신호 ON (1차 신호 OFF)
                    if current_side == second_side:
                        logging.info(f"[{symbol}] ➡️ {second_side} 포지션 유지 ({coin_priority}우선)")
                    elif current_side == first_side:
                        # 우선 포지션 청산 → 2차 포지션 진입
                        logging.info(f"[{symbol}] 🔄 {first_side}→{second_side} 전환 ({coin_priority}우선)")
                        if first_side == 'long':
                            close_result = close_long_position(symbol, f"{second_side} 신호 발생 - 포지션 전환")
                            if close_result:
                                long_close_list.append(close_result)
                        else:
                            close_result = close_short_position(symbol, f"{second_side} 신호 발생 - 포지션 전환")
                            if close_result:
                                short_close_list.append(close_result)
                        if second_side == 'short':
                            open_result = open_short_position(short_config)
                            if open_result:
                                short_open_list.append(open_result)
                                active_position_map[symbol] = 'short'
                        else:
                            open_result = open_long_position(long_config)
                            if open_result:
                                long_open_list.append(open_result)
                                active_position_map[symbol] = 'long'
                    else:
                        # 현금 → 2차 포지션 진입
                        if second_side == 'short':
                            result = open_short_position(short_config)
                            if result:
                                short_open_list.append(result)
                                active_position_map[symbol] = 'short'
                        else:
                            result = open_long_position(long_config)
                            if result:
                                long_open_list.append(result)
                                active_position_map[symbol] = 'long'

                else:
                    # 둘 다 OFF → 기존 포지션 청산
                    if current_side == 'short':
                        reason = "MA 조건 미충족" if not short_ma_condition else "스토캐스틱 조건 미충족"
                        result = close_short_position(symbol, reason)
                        if result:
                            short_close_list.append(result)
                            if symbol in active_position_map:
                                del active_position_map[symbol]
                    elif current_side == 'long':
                        if short_filter_active:
                            reason = "숏 필터 활성 (하락 추세)"
                        else:
                            reason = "롱 신호 미충족"
                        result = close_long_position(symbol, reason)
                        if result:
                            long_close_list.append(result)
                            if symbol in active_position_map:
                                del active_position_map[symbol]
                    else:
                        logging.info(f"[{symbol}] ➡️ 현금 유지")

            except Exception as e:
                errors.append(f"Futures {symbol} 처리 중 오류: {e}")
                logging.error(f"Futures {symbol} 처리 중 오류: {e}")

    except Exception as e:
        logging.error(f"Futures 전략 실행 중 오류: {e}")
        errors.append(f"Futures 전략 오류: {e}")

    return short_open_list, short_close_list, long_open_list, long_close_list, errors


def trade_strategy():
    """Futures 전용 거래 전략"""
    logging.info("\n" + "=" * 80)
    logging.info("📊 Futures 거래 전략 실행 시작")
    logging.info("=" * 80)

    # Futures 전략 실행 (숏 + 롱)
    futures_short_open, futures_short_close, futures_long_open, futures_long_close, futures_errors = futures_trade_strategy()

    # 결과 수집
    all_errors = futures_errors

    # 최종 자산 조회
    futures_balance = get_futures_balance()
    futures_total = futures_balance['total']
    futures_usdt = futures_balance['free']

    # 텔레그램 알림
    send_trade_summary(
        futures_short_open, futures_short_close,
        futures_long_open, futures_long_close,
        futures_total, futures_usdt,
        all_errors
    )

    logging.info("=" * 80)
    logging.info(f"📊 완료 - Futures 숏 진입: {len(futures_short_open)}건 / 청산: {len(futures_short_close)}건")
    logging.info(f"📊 완료 - Futures 롱 진입: {len(futures_long_open)}건 / 청산: {len(futures_long_close)}건")
    logging.info("=" * 80)


def log_strategy_info():
    logging.info("=" * 80)
    logging.info("🤖 바이낸스 Futures 자동매매 봇 v6.0.0 (코인별 롱/숏 우선순위)")
    logging.info("=" * 80)
    logging.info("📉 Futures 숏: 현재가 < MA(4H) AND Slow %K < Slow %D (1D)")
    logging.info("📈 Futures 롱: NOT 숏필터 AND (현재가 > MA AND K > D)")
    logging.info("🔄 코인별 우선순위: 롱우선 코인은 롱→숏 순, 숏우선 코인은 숏→롱 순 확인")
    long_priority_count = sum(1 for v in COIN_PRIORITY.values() if v == 'long')
    short_priority_count = sum(1 for v in COIN_PRIORITY.values() if v == 'short')
    logging.info(f"🔻 Futures 숏 대상: {len(SHORT_TRADING_CONFIGS)}개 코인")
    logging.info(f"🟢 Futures 롱 대상: {len(LONG_TRADING_CONFIGS)}개 코인")
    logging.info(f"🔄 우선순위: 롱우선 {long_priority_count}개, 숏우선 {short_priority_count}개")
    logging.info(f"📊 Futures 총 슬롯: {get_effective_futures_coins()}개 (제외 {TOTAL_FUTURES_COINS - get_effective_futures_coins()}개)")
    logging.info(f"🔶 Futures BNB 자동충전: ${FUTURES_BNB_MIN_BALANCE} 이하시 ${FUTURES_BNB_RECHARGE_AMOUNT} 매수")
    logging.info("=" * 80)


def main():
    global BOT_START_TIME
    BOT_START_TIME = datetime.now()

    setup_shutdown_handlers()

    # 거래소 초기화
    if not init_spot_exchange():
        logging.warning("⚠️ Spot 거래소 초기화 실패 (BNB 충전 불가)")

    if not init_futures_exchange():
        logging.warning("⚠️ Futures 거래소 초기화 실패")

    # 캐시 로드

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
    if futures_exchange is not None:
        logging.info("🚀 시작 시 전략 즉시 실행...")
        trade_strategy()

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
