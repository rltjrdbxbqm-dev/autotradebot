"""
================================================================================
업비트 자동매매 봇 v2.2.7 (서버 점검 시 자동 복구)
================================================================================
수정 내역:
1. [v2.2.7] 서버 점검 시 자동 복구:
   - 모든 API 호출에 30초 timeout 적용 (ThreadPoolExecutor 사용)
   - API 실패 시 이번 사이클 스킵 후 다음 스케줄에 자동 재시도
   - 서버 점검 중에도 봇이 멈추지 않고 계속 실행
2. [v2.2.6] API 에러 처리 강화 (재시도 로직)
3. [v2.2.5] 진입 자산 규모 제한: min(가용KRW/빈슬롯, 총자산/코인개수)
4. [v2.2.4] 매매 조건을 시가 기준에서 현재가 기준으로 변경
5. 베이지안 최적화 파라미터 유지
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
import pyupbit  # pyupbit 라이브러리 (데이터 조회용)
from pyupbit import Upbit # 주문용 클래스
import requests
import json
from datetime import datetime, timedelta
import logging
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 로그 파일 경로 설정
log_file_path = os.path.join(os.path.expanduser('~'), 'trading_log.txt')

# 로깅 설정
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s: %(message)s',
                    handlers=[
                        logging.FileHandler(log_file_path),
                        logging.StreamHandler()
                    ])

# ============================================================
# API 설정 (환경변수에서 로드)
# ============================================================

ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not all([ACCESS_KEY, SECRET_KEY]):
    logging.error("❌ .env 파일에서 업비트 API 키를 찾을 수 없습니다.")

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    logging.warning("⚠️ .env 파일에서 텔레그램 설정을 찾을 수 없습니다.")

# ============================================================
# 상태 저장 파일 경로
# ============================================================

STATUS_FILE = os.path.join(os.path.expanduser('~'), 'trading_status.json')
STOCH_CACHE_FILE = os.path.join(os.path.expanduser('~'), 'stoch_cache.json')

# ============================================================
# 종료 알림 관련 전역 변수
# ============================================================

BOT_START_TIME = None
SHUTDOWN_SENT = False

# ============================================================
# 텔레그램 알림 함수
# ============================================================

def send_telegram(message):
    """텔레그램 메시지 전송"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("텔레그램 설정이 되어있지 않습니다.")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            return True
        else:
            logging.error(f"텔레그램 전송 실패: {response.text}")
            return False
    except Exception as e:
        logging.error(f"텔레그램 전송 중 오류: {e}")
        return False


def send_trade_alert(trade_type, ticker, amount=None, quantity=None, strategy=None, price=None, error_rate=None):
    """거래 알림 전송"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if trade_type == "BUY":
        emoji = "🟢"
        action = "매수"
    elif trade_type == "SELL":
        emoji = "🔴"
        action = "매도"
    else:
        emoji = "ℹ️"
        action = trade_type
    
    msg = f"{emoji} <b>{action}</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"📌 코인: <b>{ticker}</b>\n"
    
    if price:
        msg += f"💰 현재가: {price:,.0f}원\n"
    if amount:
        msg += f"💵 금액: {amount:,.0f}원\n"
    if quantity:
        msg += f"📊 수량: {quantity:.8f}\n"
    if strategy:
        msg += f"📈 전략: {strategy}\n"
    if error_rate is not None:
        msg += f"📉 오차율: {error_rate:.2f}%\n"
    
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"🕐 {now}"
    
    send_telegram(msg)


def send_daily_summary(total_asset, krw_balance, holdings):
    """일일 자산 현황 요약 전송"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    msg = f"📊 <b>자산 현황 리포트</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"💰 총 자산: <b>{total_asset:,.0f}원</b>\n"
    msg += f"💵 KRW 잔고: {krw_balance:,.0f}원\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    
    if holdings:
        msg += f"📌 <b>보유 코인</b>\n"
        for coin, info in holdings.items():
            msg += f"  • {coin}: {info['value']:,.0f}원\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    msg += f"🕐 {now}"
    
    send_telegram(msg)


def send_trading_summary(total_asset, krw_balance, buy_list, sell_list, hold_list, error_list):
    """
    거래 시간대별 종합 메시지 전송
    모든 거래 내역을 하나의 메시지로 통합하여 전송
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    msg = f"📊 <b>거래 실행 리포트</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"🕐 {now}\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    
    # 자산 현황
    msg += f"💰 총 자산: <b>{total_asset:,.0f}원</b>\n"
    msg += f"💵 KRW 잔고: {krw_balance:,.0f}원\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    
    # 매수 내역
    if buy_list:
        msg += f"🟢 <b>매수 ({len(buy_list)}건)</b>\n"
        for buy in buy_list:
            ticker = buy['ticker']
            amount = buy['amount']
            price = buy['price']
            strategy = buy['strategy']
            error_rate = buy.get('error_rate')
            
            msg += f"  • {ticker}: {amount:,.0f}원\n"
            msg += f"    └ {price:,.0f}원 ({strategy})"
            if error_rate is not None:
                msg += f" 오차:{error_rate:.1f}%"
            msg += "\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    # 매도 내역
    if sell_list:
        msg += f"🔴 <b>매도 ({len(sell_list)}건)</b>\n"
        for sell in sell_list:
            ticker = sell['ticker']
            price = sell['price']
            reason = sell['reason']
            
            msg += f"  • {ticker}: {price:,.0f}원\n"
            msg += f"    └ {reason}\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    # 보유 중인 코인 (간략히)
    if hold_list:
        msg += f"📌 <b>보유 중 ({len(hold_list)}개)</b>\n"
        # 한 줄에 여러 개 표시
        hold_str = ", ".join(hold_list)
        # 너무 길면 줄바꿈
        if len(hold_str) > 40:
            chunks = [hold_list[i:i+5] for i in range(0, len(hold_list), 5)]
            for chunk in chunks:
                msg += f"  {', '.join(chunk)}\n"
        else:
            msg += f"  {hold_str}\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    # 에러 내역
    if error_list:
        msg += f"⚠️ <b>오류 ({len(error_list)}건)</b>\n"
        for err in error_list[:5]:  # 최대 5개만 표시
            msg += f"  • {err}\n"
        if len(error_list) > 5:
            msg += f"  ... 외 {len(error_list) - 5}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    # 거래 없음
    if not buy_list and not sell_list and not error_list:
        msg += f"ℹ️ 이번 시간대 거래 없음\n"
        if hold_list:
            msg += f"   {len(hold_list)}개 코인 보유 유지\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    # 요약
    msg += f"📋 매수: {len(buy_list)}건 / 매도: {len(sell_list)}건"
    
    send_telegram(msg)


def send_error_alert(error_message):
    """에러 알림 전송"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    msg = f"⚠️ <b>오류 발생</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"{error_message}\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"🕐 {now}"
    
    send_telegram(msg)


def send_start_alert(status_loaded=False):
    """봇 시작 알림"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    msg = f"🚀 <b>자동매매 봇 시작 (v2.2.7)</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"📈 전략: MA + 스토캐스틱 + 역방향\n"
    msg += f"🛠️ 수정: 서버 점검 시 자동 복구\n"
    msg += f"🪙 대상: {len(COINS)}개 코인\n"
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
        minutes, seconds = divmod(remainder, 60)
        
        if days > 0:
            uptime_str = f"{days}일 {hours}시간 {minutes}분"
        elif hours > 0:
            uptime_str = f"{hours}시간 {minutes}분"
        else:
            uptime_str = f"{minutes}분 {seconds}초"
    else:
        uptime_str = "알 수 없음"
    
    msg = f"🛑 <b>자동매매 봇 종료</b>\n"
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
# Upbit 클라이언트 초기화
# ============================================================

upbit = Upbit(ACCESS_KEY, SECRET_KEY)


# ============================================================
# API 호출 재시도 래퍼 (Timeout 지원 - signal.alarm 방식)
# ============================================================

class APITimeoutError(Exception):
    """API 호출 타임아웃 에러"""
    pass


def _timeout_handler(signum, frame):
    """타임아웃 시그널 핸들러"""
    raise APITimeoutError("API 호출 타임아웃")


def call_with_timeout(func, timeout=30):
    """
    함수를 timeout과 함께 실행 (signal.alarm 방식 - Linux 전용)
    
    Args:
        func: 실행할 함수 (lambda로 전달)
        timeout: 타임아웃 시간 (초)
    
    Returns:
        함수 실행 결과 또는 None (타임아웃/에러 시)
    """
    # 기존 핸들러 저장
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    
    try:
        result = func()
        signal.alarm(0)  # 타이머 취소
        return result
    except APITimeoutError:
        logging.warning(f"API 호출 타임아웃 ({timeout}초)")
        return None
    except Exception as e:
        signal.alarm(0)  # 타이머 취소
        logging.warning(f"API 호출 중 오류: {e}")
        return None
    finally:
        signal.alarm(0)  # 타이머 확실히 취소
        signal.signal(signal.SIGALRM, old_handler)  # 핸들러 복원


def retry_api_call(func, max_retries=3, delay=2.0, default=None, timeout=30):
    """
    API 호출 재시도 래퍼 (Timeout 지원)
    
    Args:
        func: 실행할 함수 (lambda로 전달)
        max_retries: 최대 재시도 횟수
        delay: 재시도 간 대기 시간 (초)
        default: 모든 재시도 실패 시 반환할 기본값
        timeout: 각 시도당 타임아웃 (초)
    
    Returns:
        API 호출 결과 또는 default 값
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            result = call_with_timeout(func, timeout=timeout)
            if result is not None:
                return result
            # None이 반환된 경우도 재시도
            logging.warning(f"API 호출 결과가 None입니다. 재시도 {attempt + 1}/{max_retries}")
        except Exception as e:
            last_error = e
            logging.warning(f"API 호출 실패 (시도 {attempt + 1}/{max_retries}): {e}")
        
        if attempt < max_retries - 1:
            time.sleep(delay)
    
    if last_error:
        logging.error(f"API 호출 최종 실패: {last_error}")
    else:
        logging.error(f"API 호출 최종 실패: 모든 재시도에서 None 반환")
    return default


# 거래 대상 코인 리스트 (20개)
# ============================================================
# 1. 투자 대상 코인 설정 (변수명 COINS 유지 필수)
# ============================================================
# ============================================================
# 1. 투자 대상 코인 설정 (총 32개, CAGR 순 정렬)
# ============================================================
COINS = [
    'KRW-BONK',
    'KRW-UNI',
    'KRW-SUI',
    'KRW-MNT',
    'KRW-MOVE',
    'KRW-AKT',
    'KRW-IMX',
    'KRW-ARB',
    'KRW-VET',
    'KRW-SAND',
    'KRW-HBAR',
    'KRW-GRT',
    'KRW-AVAX',
    'KRW-NEAR',
    'KRW-SOL',
    'KRW-THETA',
    'KRW-MANA',
    'KRW-XRP',
    'KRW-ANKR',
    'KRW-ADA',
    'KRW-POL',
    'KRW-CRO',
    'KRW-DOT',
    'KRW-MVL',
    'KRW-ETH',
    'KRW-WAXP',
    'KRW-DOGE',
    'KRW-XLM',
    'KRW-LINK',
    'KRW-AXS',
    'KRW-BTC',
    'KRW-BCH',
]

# ============================================================
# 2. 전략 파라미터 설정
# ============================================================

# 이동평균선 기간 (4시간봉 기준)
MA_PERIODS = {
    'KRW-BONK': 122,  # Modified (Binance Result)
    'KRW-UNI': 250,
    'KRW-SUI': 298,  # Modified (Binance Result)
    'KRW-MNT': 216,
    'KRW-MOVE': 200,
    'KRW-AKT': 68,
    'KRW-IMX': 116,
    'KRW-ARB': 52,
    'KRW-VET': 46,
    'KRW-SAND': 218,  # Modified (Binance Result)
    'KRW-HBAR': 104,  # Modified (Binance Result)
    'KRW-GRT': 239,
    'KRW-AVAX': 55,
    'KRW-NEAR': 200,
    'KRW-SOL': 258,  # Modified (Binance Result)
    'KRW-THETA': 221,
    'KRW-MANA': 84,   # Modified (Binance Result)
    'KRW-XRP': 100,
    'KRW-ANKR': 163,
    'KRW-ADA': 128,   # Modified (Binance Result)
    'KRW-POL': 50,
    'KRW-CRO': 112,
    'KRW-DOT': 52,
    'KRW-MVL': 298,
    'KRW-ETH': 110,
    'KRW-WAXP': 56,
    'KRW-DOGE': 70,
    'KRW-XLM': 66,
    'KRW-LINK': 61,
    'KRW-AXS': 283,
    'KRW-BTC': 117,
    'KRW-BCH': 97,
}

# STOCH_PARAMS
STOCH_PARAMS = {
    'KRW-BONK': {'k_period': 155, 'k_smooth': 54, 'd_period': 14}, # Modified
    'KRW-UNI': {'k_period': 145, 'k_smooth': 30, 'd_period': 7},
    'KRW-SUI': {'k_period': 160, 'k_smooth': 34, 'd_period': 7},   # Modified
    'KRW-MNT': {'k_period': 177, 'k_smooth': 23, 'd_period': 26},
    'KRW-MOVE': {'k_period': 70, 'k_smooth': 50, 'd_period': 30},
    'KRW-AKT': {'k_period': 142, 'k_smooth': 46, 'd_period': 13},
    'KRW-IMX': {'k_period': 58, 'k_smooth': 19, 'd_period': 14},
    'KRW-ARB': {'k_period': 118, 'k_smooth': 46, 'd_period': 23},
    'KRW-VET': {'k_period': 101, 'k_smooth': 45, 'd_period': 8},
    'KRW-SAND': {'k_period': 125, 'k_smooth': 30, 'd_period': 6},  # Modified
    'KRW-HBAR': {'k_period': 50, 'k_smooth': 34, 'd_period': 19},  # Modified
    'KRW-GRT': {'k_period': 107, 'k_smooth': 25, 'd_period': 4},
    'KRW-AVAX': {'k_period': 133, 'k_smooth': 35, 'd_period': 10},
    'KRW-NEAR': {'k_period': 160, 'k_smooth': 30, 'd_period': 25},
    'KRW-SOL': {'k_period': 80, 'k_smooth': 26, 'd_period': 6},    # Modified
    'KRW-THETA': {'k_period': 166, 'k_smooth': 57, 'd_period': 7},
    'KRW-MANA': {'k_period': 130, 'k_smooth': 38, 'd_period': 14}, # Modified
    'KRW-XRP': {'k_period': 40, 'k_smooth': 22, 'd_period': 6},
    'KRW-ANKR': {'k_period': 227, 'k_smooth': 60, 'd_period': 7},
    'KRW-ADA': {'k_period': 200, 'k_smooth': 34, 'd_period': 9},   # Modified
    'KRW-POL': {'k_period': 216, 'k_smooth': 28, 'd_period': 5},
    'KRW-CRO': {'k_period': 69, 'k_smooth': 46, 'd_period': 3},
    'KRW-DOT': {'k_period': 160, 'k_smooth': 33, 'd_period': 6},
    'KRW-MVL': {'k_period': 40, 'k_smooth': 58, 'd_period': 8},
    'KRW-ETH': {'k_period': 211, 'k_smooth': 28, 'd_period': 11},
    'KRW-WAXP': {'k_period': 103, 'k_smooth': 30, 'd_period': 6},
    'KRW-DOGE': {'k_period': 144, 'k_smooth': 39, 'd_period': 9},
    'KRW-XLM': {'k_period': 39, 'k_smooth': 25, 'd_period': 12},
    'KRW-LINK': {'k_period': 113, 'k_smooth': 35, 'd_period': 3},
    'KRW-AXS': {'k_period': 40, 'k_smooth': 32, 'd_period': 6},
    'KRW-BTC': {'k_period': 171, 'k_smooth': 24, 'd_period': 5},
    'KRW-BCH': {'k_period': 66, 'k_smooth': 29, 'd_period': 3},
}

# REVERSE_ERROR_RATE_CONFIG
REVERSE_ERROR_RATE_CONFIG = {
    'KRW-BONK': {'error_rate': -20, 'hold_hours': 16},  # Modified
    'KRW-UNI': {'error_rate': -38, 'hold_hours': 54},
    'KRW-SUI': {'error_rate': -30, 'hold_hours': 18},   # Modified
    'KRW-MNT': {'error_rate': -33, 'hold_hours': 19},
    'KRW-MOVE': {'error_rate': -25, 'hold_hours': 12},
    'KRW-AKT': {'error_rate': -19, 'hold_hours': 13},
    'KRW-IMX': {'error_rate': -26, 'hold_hours': 44},
    'KRW-ARB': {'error_rate': -17, 'hold_hours': 37},
    'KRW-VET': {'error_rate': -19, 'hold_hours': 80},
    'KRW-SAND': {'error_rate': -36, 'hold_hours': 70}, # Modified
    'KRW-HBAR': {'error_rate': -18, 'hold_hours': 16},  # Modified
    'KRW-GRT': {'error_rate': -34, 'hold_hours': 94},
    'KRW-AVAX': {'error_rate': -12, 'hold_hours': 19},
    'KRW-NEAR': {'error_rate': -25, 'hold_hours': 32},
    'KRW-SOL': {'error_rate': -54, 'hold_hours': 12},   # Modified
    'KRW-THETA': {'error_rate': -42, 'hold_hours': 138},
    'KRW-MANA': {'error_rate': -26, 'hold_hours': 52}, # Modified
    'KRW-XRP': {'error_rate': -56, 'hold_hours': 217},
    'KRW-ANKR': {'error_rate': -34, 'hold_hours': 120},
    'KRW-ADA': {'error_rate': -16, 'hold_hours': 14},   # Modified
    'KRW-POL': {'error_rate': -14, 'hold_hours': 35},
    'KRW-CRO': {'error_rate': -48, 'hold_hours': 207},
    'KRW-DOT': {'error_rate': -15, 'hold_hours': 39},
    'KRW-MVL': {'error_rate': -60, 'hold_hours': 305},
    'KRW-ETH': {'error_rate': -41, 'hold_hours': 90},
    'KRW-WAXP': {'error_rate': -67, 'hold_hours': 148},
    'KRW-DOGE': {'error_rate': -19, 'hold_hours': 98},
    'KRW-XLM': {'error_rate': -49, 'hold_hours': 202},
    'KRW-LINK': {'error_rate': -25, 'hold_hours': 51},
    'KRW-AXS': {'error_rate': -63, 'hold_hours': 217},
    'KRW-BTC': {'error_rate': -31, 'hold_hours': 145},
    'KRW-BCH': {'error_rate': -61, 'hold_hours': 43},
}
# 매수 상태 추적을 위한 글로벌 변수
buy_status = {}

# 스토캐스틱 캐시
stoch_cache = {}
stoch_cache_date = None


# ============================================================
# 상태 저장/로드 함수
# ============================================================

def save_status():
    """매수 상태를 파일에 저장"""
    global buy_status
    try:
        save_data = {}
        for ticker, status in buy_status.items():
            save_data[ticker] = {
                'is_reverse_holding': status['is_reverse_holding'],
                'reverse_start_time': status['reverse_start_time'].isoformat() if status['reverse_start_time'] else None,
                'reverse_hold_hours': status['reverse_hold_hours']
            }
        
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        logging.debug(f"상태 저장 완료: {STATUS_FILE}")
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
        for ticker, status in data.items():
            if ticker in buy_status:
                buy_status[ticker]['is_reverse_holding'] = status.get('is_reverse_holding', False)
                
                start_time_str = status.get('reverse_start_time')
                if start_time_str:
                    buy_status[ticker]['reverse_start_time'] = datetime.fromisoformat(start_time_str)
                else:
                    buy_status[ticker]['reverse_start_time'] = None
                
                buy_status[ticker]['reverse_hold_hours'] = status.get('reverse_hold_hours', 0)
                
                if buy_status[ticker]['is_reverse_holding']:
                    loaded_count += 1
                    logging.info(f"📂 {ticker} 역방향 상태 복원: 시작={start_time_str}, 보유시간={status.get('reverse_hold_hours')}h")
        
        logging.info(f"상태 로드 완료: {loaded_count}개 역방향 보유 중")
        return loaded_count > 0
    except Exception as e:
        logging.error(f"상태 로드 중 오류: {e}")
        return False


def save_stoch_cache():
    """스토캐스틱 캐시를 파일에 저장 (JSON 직렬화 오류 수정)"""
    global stoch_cache, stoch_cache_date
    try:
        save_data = {
            'cache_date': stoch_cache_date.isoformat() if stoch_cache_date else None,
            'data': stoch_cache
        }
        
        with open(STOCH_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        logging.debug(f"스토캐스틱 캐시 저장 완료")
        return True
    except Exception as e:
        logging.error(f"스토캐스틱 캐시 저장 중 오류: {e}")
        return False


def load_stoch_cache():
    """저장된 스토캐스틱 캐시 불러오기"""
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
    for ticker in COINS:
        buy_status[ticker] = {
            'is_reverse_holding': False,
            'reverse_start_time': None,
            'reverse_hold_hours': 0
        }


# ============================================================
# 자금 배분 로직
# ============================================================

def get_krw_balance():
    """KRW 잔고 조회 (재시도 및 None 처리 포함)"""
    try:
        result = retry_api_call(
            lambda: upbit.get_balance("KRW"),
            max_retries=3,
            delay=2.0,
            default=None
        )
        
        if result is None:
            logging.error("KRW 잔고 조회 실패: API가 None을 반환했습니다.")
            return 0
        
        return float(result)
    except Exception as e:
        logging.error(f"KRW 잔고 조회 중 오류: {e}")
        return 0


def get_total_asset():
    """총 자산 계산 (재시도 및 개별 에러 처리 포함)"""
    try:
        # 잔고 조회 (재시도 포함)
        logging.debug("총 자산 계산: 잔고 조회 시작...")
        balances = retry_api_call(
            lambda: upbit.get_balances(),
            max_retries=3,
            delay=3.0,
            default=None
        )
        
        if balances is None:
            logging.error("잔고 조회 실패: API가 None을 반환했습니다.")
            return 0
        
        logging.debug(f"총 자산 계산: 잔고 {len(balances)}개 항목 조회됨")
        total_asset = 0.0
        
        for idx, balance in enumerate(balances):
            try:
                if balance['currency'] != 'KRW':
                    ticker = f"KRW-{balance['currency']}"
                    current_price = get_current_price(ticker)
                    time.sleep(0.1)
                    if current_price:
                        balance_amount = balance.get('balance')
                        if balance_amount is not None:
                            coin_value = float(balance_amount) * current_price
                            total_asset += coin_value
                else:
                    balance_amount = balance.get('balance')
                    if balance_amount is not None:
                        total_asset += float(balance_amount)
            except Exception as e:
                logging.warning(f"개별 잔고 처리 중 오류: {balance.get('currency', 'unknown')} - {e}")
                continue

        logging.debug(f"총 자산 계산 완료: {total_asset:,.0f}")
        return total_asset
    except Exception as e:
        logging.error(f"총 자산 계산 중 오류 발생: {e}")
        return 0


def get_holdings_info():
    """보유 코인 정보 조회 (재시도 및 에러 처리 포함)"""
    try:
        balances = retry_api_call(
            lambda: upbit.get_balances(),
            max_retries=3,
            delay=2.0,
            default=None
        )
        
        if balances is None:
            logging.error("보유 코인 조회 실패: API가 None을 반환했습니다.")
            return {}
        
        holdings = {}
        
        for balance in balances:
            try:
                balance_amount = balance.get('balance')
                if balance['currency'] != 'KRW' and balance_amount and float(balance_amount) > 0:
                    ticker = f"KRW-{balance['currency']}"
                    current_price = get_current_price(ticker)
                    time.sleep(0.1)
                    if current_price:
                        coin_value = float(balance_amount) * current_price
                        if coin_value >= 1000:
                            holdings[balance['currency']] = {
                                'balance': float(balance_amount),
                                'price': current_price,
                                'value': coin_value
                            }
            except Exception as e:
                logging.warning(f"보유 코인 처리 중 오류: {balance.get('currency', 'unknown')} - {e}")
                continue
        
        return holdings
    except Exception as e:
        logging.error(f"보유 코인 조회 중 오류 발생: {e}")
        return {}


def count_empty_slots():
    """매수 가능한 빈 슬롯 수 계산"""
    empty_count = 0
    for ticker in COINS:
        coin_currency = ticker.split('-')[1]
        try:
            balance = upbit.get_balance(coin_currency)
            if balance == 0 or balance is None:
                empty_count += 1
        except:
            empty_count += 1
        time.sleep(0.05)
    return empty_count


def calculate_invest_amount():
    """
    [v2.2.5] 진입 자산 규모 제한 로직 추가
    - 기존 방식(가용 KRW / 빈 슬롯)과
    - 총자산 / 가상화폐 개수 중 작은 값 사용
    """
    krw_balance = get_krw_balance()
    empty_slots = count_empty_slots()
    
    if empty_slots == 0:
        logging.info("매수 가능한 빈 슬롯이 없습니다.")
        return 0
    
    # [기존 방식] 가용 KRW / 빈 슬롯 수
    available_krw = krw_balance * 0.995
    amount_by_available = available_krw / empty_slots
    
    # [v2.2.5 추가] 총자산 / 코인 개수 (상한선)
    total_asset = get_total_asset()
    num_coins = len(COINS)
    max_by_equity = total_asset / num_coins
    
    # 두 방식 중 작은 값 선택
    invest_amount = min(amount_by_available, max_by_equity)
    
    logging.info(f"💰 자금 배분 비교: 가용잔고 기반={amount_by_available:,.0f}원, "
                f"총자산 기반={max_by_equity:,.0f}원 → 선택: {invest_amount:,.0f}원")
    
    if invest_amount < 5000:
        logging.warning(f"투자금액({invest_amount:,.0f}원)이 최소 주문금액(5000원) 미만입니다.")
        return 0
    
    logging.info(f"💰 최종 배분: 총자산 {total_asset:,.0f}원 / {num_coins}개 코인, "
                f"KRW {krw_balance:,.0f}원 / 빈슬롯 {empty_slots}개 = 코인당 {invest_amount:,.0f}원")
    
    return invest_amount


# ============================================================
# 시세 조회 함수
# ============================================================

def get_current_price(ticker):
    """현재가 조회 (timeout 포함)"""
    try:
        url = f"https://api.upbit.com/v1/ticker?markets={ticker}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and 'trade_price' in data[0]:
            return float(data[0]['trade_price'])
        return None
    except requests.exceptions.Timeout:
        logging.warning(f"{ticker} 현재가 조회 타임아웃")
        return None
    except Exception as e:
        logging.error(f"{ticker} 현재가 조회 중 오류 발생: {e}")
        return None


def get_opening_price_4h(ticker):
    """4시간봉 현재 캔들의 시가 조회 (timeout 포함)"""
    try:
        url = f"https://api.upbit.com/v1/candles/minutes/240?market={ticker}&count=1"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data:
            return float(data[0]['opening_price'])
        return None
    except requests.exceptions.Timeout:
        logging.warning(f"{ticker} 4H 시가 조회 타임아웃")
        return None
    except Exception as e:
        logging.error(f"{ticker} 4H 시가 조회 중 오류 발생: {e}")
        return None


def get_hourly_ma(ticker, period):
    """4시간봉 이동평균 계산 (pyupbit 사용으로 200개 제한 해결, timeout 포함)"""
    try:
        # pyupbit는 count가 200을 넘으면 자동으로 분할 요청하여 합쳐줍니다.
        # interval="minute240"은 4시간봉을 의미합니다.
        logging.debug(f"{ticker} MA{period} 조회 중...")
        
        # timeout wrapper 적용
        df = call_with_timeout(
            lambda: pyupbit.get_ohlcv(ticker, interval="minute240", count=period),
            timeout=30
        )
        
        if df is not None and not df.empty:
            # trade_price는 종가(close)를 의미합니다.
            return float(df['close'].mean())
        return None
    except Exception as e:
        logging.error(f"{ticker} 이동평균선 계산 중 오류 발생: {e}")
        return None


def get_daily_ohlcv(ticker, count):
    """1일봉 OHLCV 데이터 조회 (pyupbit 사용으로 200개 제한 해결, timeout 포함)"""
    try:
        # pyupbit.get_ohlcv는 count가 200을 넘으면 자동으로 반복 요청을 처리해줍니다.
        # timeout wrapper 적용
        df = call_with_timeout(
            lambda: pyupbit.get_ohlcv(ticker, interval="day", count=count),
            timeout=30
        )
        
        if df is not None and not df.empty:
            return df
        return None
    except Exception as e:
        logging.error(f"{ticker} 일봉 데이터 조회 중 오류 발생: {e}")
        return None


# ============================================================
# 스토캐스틱 캐시
# ============================================================

def calculate_stochastic(df, k_period, k_smooth, d_period):
    """스토캐스틱 슬로우 계산"""
    if df is None or len(df) < k_period:
        return None, None
    
    low_min = df['low'].rolling(window=k_period).min()
    high_max = df['high'].rolling(window=k_period).max()
    
    fast_k = ((df['close'] - low_min) / (high_max - low_min)) * 100
    slow_k = fast_k.rolling(window=k_smooth).mean()
    slow_d = slow_k.rolling(window=d_period).mean()
    
    if pd.isna(slow_k.iloc[-1]) or pd.isna(slow_d.iloc[-1]):
        return None, None
    
    return slow_k.iloc[-1], slow_d.iloc[-1]


def should_refresh_stoch_cache():
    """스토캐스틱 캐시 갱신 필요 여부 확인"""
    global stoch_cache_date
    
    now = datetime.now()
    today = now.date()
    
    if stoch_cache_date is None:
        logging.info("스토캐스틱 캐시가 없습니다. 새로 생성합니다.")
        return True
    
    # 일봉 마감 시간 = 09:00 (한국시간 기준)
    today_9am = now.replace(hour=9, minute=0, second=0, microsecond=0)
    
    if now >= today_9am and stoch_cache_date < today:
        logging.info(f"일봉 마감 후 첫 실행. 스토캐스틱 캐시 갱신합니다. (캐시날짜: {stoch_cache_date}, 오늘: {today})")
        return True
    
    return False


def refresh_all_stochastic():
    """모든 코인의 스토캐스틱 데이터 갱신 (형변환 추가)"""
    global stoch_cache, stoch_cache_date
    
    logging.info("📊 스토캐스틱 데이터 전체 갱신 시작...")
    
    for ticker in COINS:
        try:
            params = STOCH_PARAMS.get(ticker)
            if not params:
                params = {'k_period': 200, 'k_smooth': 60, 'd_period': 30}
            
            # 여유분을 20으로 늘림 (안전성 확보)
            required_count = params['k_period'] + params['k_smooth'] + params['d_period'] + 20
            df = get_daily_ohlcv(ticker, required_count)
            
            if df is None:
                logging.warning(f"{ticker} 일봉 데이터 조회 실패")
                continue
            
            slow_k, slow_d = calculate_stochastic(df, params['k_period'], params['k_smooth'], params['d_period'])
            
            if slow_k is not None and slow_d is not None:
                stoch_cache[ticker] = {
                    'signal': bool(slow_k > slow_d),  # numpy.bool_ -> bool
                    'slow_k': float(slow_k),          # numpy.float -> float
                    'slow_d': float(slow_d)           # numpy.float -> float
                }
                logging.debug(f"{ticker} 스토캐스틱: K={slow_k:.2f}, D={slow_d:.2f}, Signal={slow_k > slow_d}")
            
            time.sleep(0.1)
            
        except Exception as e:
            logging.error(f"{ticker} 스토캐스틱 계산 중 오류: {e}")
    
    stoch_cache_date = datetime.now().date()
    save_stoch_cache()
    
    logging.info(f"📊 스토캐스틱 데이터 갱신 완료: {len(stoch_cache)}개 코인")


def get_stochastic_signal(ticker):
    """스토캐스틱 시그널 조회 (형변환 추가)"""
    global stoch_cache
    
    if should_refresh_stoch_cache():
        refresh_all_stochastic()
    
    if ticker in stoch_cache:
        return stoch_cache[ticker]
    
    try:
        params = STOCH_PARAMS.get(ticker)
        if not params:
            params = {'k_period': 200, 'k_smooth': 60, 'd_period': 30}
        
        required_count = params['k_period'] + params['k_smooth'] + params['d_period'] + 20
        df = get_daily_ohlcv(ticker, required_count)
        
        if df is None:
            return None
        
        slow_k, slow_d = calculate_stochastic(df, params['k_period'], params['k_smooth'], params['d_period'])
        
        if slow_k is None or slow_d is None:
            return None
        
        result = {
            'signal': bool(slow_k > slow_d),  # numpy.bool_ -> bool
            'slow_k': float(slow_k),          # numpy.float -> float
            'slow_d': float(slow_d)           # numpy.float -> float
        }
        
        stoch_cache[ticker] = result
        return result
        
    except Exception as e:
        logging.error(f"{ticker} 스토캐스틱 시그널 조회 중 오류: {e}")
        return None


# ============================================================
# 전략 함수
# ============================================================

def calculate_error_rate(price, ma_price):
    """오차율 계산"""
    if ma_price is None or ma_price <= 0:
        return 0
    return ((price - ma_price) / ma_price) * 100


def check_reverse_strategy(ticker, current_price, ma_price):
    """역방향 전략 체크"""
    global buy_status
    
    if ticker not in REVERSE_ERROR_RATE_CONFIG:
        return False, False, 0
    
    config = REVERSE_ERROR_RATE_CONFIG[ticker]
    error_rate_threshold = config['error_rate']
    hold_duration_hours = config['hold_hours'] * 4
    
    current_time = datetime.now()
    error_rate = calculate_error_rate(current_price, ma_price)
    
    if buy_status[ticker]['is_reverse_holding']:
        start_time = buy_status[ticker]['reverse_start_time']
        if start_time:
            elapsed_hours = (current_time - start_time).total_seconds() / 3600
            
            if elapsed_hours >= hold_duration_hours:
                buy_status[ticker]['is_reverse_holding'] = False
                buy_status[ticker]['reverse_start_time'] = None
                buy_status[ticker]['reverse_hold_hours'] = 0
                save_status()
                
                logging.info(f"🔚 {ticker} 역방향 보유 기간 종료 (경과: {elapsed_hours:.1f}시간 / 설정: {hold_duration_hours}시간)")
                return False, False, error_rate
            else:
                remaining = hold_duration_hours - elapsed_hours
                logging.info(f"⏳ {ticker} 역방향 보유 중 - 남은시간: {remaining:.1f}시간")
                return True, True, error_rate
    
    if current_price < ma_price and error_rate <= error_rate_threshold:
        buy_status[ticker]['is_reverse_holding'] = True
        buy_status[ticker]['reverse_start_time'] = current_time
        buy_status[ticker]['reverse_hold_hours'] = hold_duration_hours
        save_status()
        
        logging.info(f"🔴 {ticker} 역방향 매수 신호 발생!")
        logging.info(f"   오차율: {error_rate:.2f}% (임계값: {error_rate_threshold}%)")
        logging.info(f"   보유 예정: {hold_duration_hours}시간")
        
        return True, True, error_rate
    
    return False, False, error_rate


# ============================================================
# 메인 거래 전략
# ============================================================

def trade_strategy():
    """거래 전략 실행 (종합 메시지 전송)"""
    try:
        logging.info("📡 KRW 잔고 조회 중...")
        krw_balance = get_krw_balance()
        logging.info(f"📡 KRW 잔고 조회 완료: {krw_balance:,.0f} KRW")
        
        logging.info("📡 총 자산 계산 중...")
        total_asset = get_total_asset()
        logging.info(f"📡 총 자산 계산 완료: {total_asset:,.0f} KRW")
        
        # API 실패 시 (서버 점검 등) 이번 사이클 스킵
        if krw_balance == 0 and total_asset == 0:
            logging.warning("⚠️ API 조회 실패 (서버 점검 가능성). 이번 사이클을 건너뜁니다.")
            logging.warning("⏰ 다음 스케줄 시간에 자동으로 재시도합니다.")
            send_telegram("⚠️ <b>업비트 API 조회 실패</b>\n서버 점검 가능성이 있습니다.\n다음 스케줄 시간에 자동 재시도합니다.")
            return
        
        logging.info("=" * 80)
        logging.info(f"📊 거래 전략 실행 시작 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"💰 총 자산: {total_asset:,.0f} KRW")
        logging.info(f"💵 KRW 잔고: {krw_balance:,.0f} KRW")
        logging.info("=" * 80)
        
        # 거래 내역 수집용 리스트
        buy_list = []      # 매수 내역
        sell_list = []     # 매도 내역
        hold_list = []     # 보유 중인 코인
        error_list = []    # 에러 내역

        for ticker in COINS:
            time.sleep(0.2)
            
            opening_price_4h = get_opening_price_4h(ticker)
            time.sleep(0.1)
            
            ma_price = get_hourly_ma(ticker, MA_PERIODS[ticker])
            time.sleep(0.1)
            
            current_price = get_current_price(ticker)
            time.sleep(0.1)
            
            stoch_data = get_stochastic_signal(ticker)
            time.sleep(0.1)
            
            if opening_price_4h is None or ma_price is None or current_price is None:
                logging.error(f"{ticker} 데이터가 유효하지 않아 매매 건너뜀")
                error_list.append(f"{ticker.split('-')[1]}: 데이터 조회 실패")
                continue
            
            coin_currency = ticker.split('-')[1]
            current_balance = call_with_timeout(
                lambda c=coin_currency: upbit.get_balance(c),
                timeout=10
            )
            if current_balance is None:
                current_balance = 0
            
            reverse_signal, is_reverse_holding, error_rate = check_reverse_strategy(
                ticker, current_price, ma_price
            )
            
            ma_condition = current_price > ma_price
            
            if stoch_data and stoch_data.get('signal') is not None:
                stoch_condition = stoch_data['signal']
                slow_k = stoch_data['slow_k']
                slow_d = stoch_data['slow_d']
            else:
                stoch_condition = True
                slow_k = None
                slow_d = None
            
            if is_reverse_holding:
                final_buy_condition = True
                strategy_type = "역방향"
            elif ma_condition and stoch_condition:
                final_buy_condition = True
                strategy_type = "상승"
            else:
                final_buy_condition = False
                strategy_type = "없음"
            
            stoch_str = f"K:{slow_k:.1f}/D:{slow_d:.1f}" if slow_k is not None else "N/A"
            reverse_str = "보유중" if is_reverse_holding else ("신호" if reverse_signal else "X")
            
            logging.info(f"{ticker} | 현재가:{current_price:,.0f} | MA:{ma_price:,.0f} | "
                        f"오차율:{error_rate:.1f}% | 스토캐스틱:{stoch_str} | "
                        f"MA:{ma_condition} | Stoch:{stoch_condition} | 역방향:{reverse_str} | "
                        f"최종:{final_buy_condition} ({strategy_type})")
            
            if final_buy_condition:
                if current_balance == 0:
                    invest_amount = calculate_invest_amount()
                    
                    if invest_amount < 5000:
                        logging.warning(f"{ticker} 투자금액 부족으로 매수 건너뜀")
                        continue
                    
                    try:
                        upbit.buy_market_order(ticker, invest_amount)
                        
                        # 매수 내역 수집
                        buy_info = {
                            'ticker': coin_currency,
                            'amount': invest_amount,
                            'price': current_price,
                            'strategy': strategy_type,
                            'error_rate': error_rate if strategy_type == "역방향" else None
                        }
                        buy_list.append(buy_info)
                        
                        if strategy_type == "역방향":
                            logging.info(f"🔴 {ticker} 역방향 매수 주문 성공: {invest_amount:,.0f} KRW")
                        else:
                            logging.info(f"🟢 {ticker} 상승 매수 주문 성공: {invest_amount:,.0f} KRW")
                            
                    except Exception as e:
                        logging.error(f"{ticker} 매수 주문 실패: {e}")
                        error_list.append(f"{coin_currency}: 매수 실패 - {str(e)[:30]}")
                else:
                    # 보유 중인 코인 수집
                    hold_list.append(f"{coin_currency}({strategy_type})")
                    
                    if strategy_type == "역방향":
                        logging.info(f"✅ {ticker} 역방향 전략 보유 중")
                    else:
                        logging.info(f"✅ {ticker} 상승 전략 보유 중")
            else:
                if current_balance > 0:
                    try:
                        upbit.sell_market_order(ticker, current_balance)
                        
                        if not ma_condition:
                            sell_reason = "MA 조건 위반"
                        elif not stoch_condition:
                            sell_reason = "스토캐스틱 조건 위반"
                        else:
                            sell_reason = "조건 미충족"
                        
                        # 매도 내역 수집
                        sell_info = {
                            'ticker': coin_currency,
                            'quantity': current_balance,
                            'price': current_price,
                            'reason': sell_reason
                        }
                        sell_list.append(sell_info)
                        
                        logging.info(f"🔵 {ticker} 전량 매도 ({sell_reason})")
                    except Exception as e:
                        logging.error(f"{ticker} 매도 주문 실패: {e}")
                        error_list.append(f"{coin_currency}: 매도 실패 - {str(e)[:30]}")
                else:
                    reasons = []
                    if not ma_condition:
                        reasons.append("MA미충족")
                    if not stoch_condition:
                        reasons.append("Stoch미충족")
                    
                    reverse_config = REVERSE_ERROR_RATE_CONFIG.get(ticker, {})
                    reverse_threshold = reverse_config.get('error_rate', -999)
                    if error_rate > reverse_threshold:
                        reasons.append(f"역방향미충족({error_rate:.1f}%>{reverse_threshold}%)")
                    
                    logging.info(f"⬜ {ticker} 대기 중 ({', '.join(reasons)})")
        
        # 종합 메시지 전송
        send_trading_summary(
            total_asset=total_asset,
            krw_balance=krw_balance,
            buy_list=buy_list,
            sell_list=sell_list,
            hold_list=hold_list,
            error_list=error_list
        )
                
        logging.info("=" * 80)
        logging.info(f"📊 거래 전략 실행 완료 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"   매수: {len(buy_list)}건 / 매도: {len(sell_list)}건")
        logging.info("=" * 80)
        logging.info("=" * 80)
        
        save_status()
                
    except Exception as e:
        logging.error(f"자동매매 전략 실행 중 오류 발생: {e}")
        send_error_alert(f"전략 실행 중 오류: {e}")
        import traceback
        traceback.print_exc()


def send_daily_report():
    """일일 리포트 전송"""
    try:
        total_asset = get_total_asset()
        krw_balance = get_krw_balance()
        holdings = get_holdings_info()
        send_daily_summary(total_asset, krw_balance, holdings)
    except Exception as e:
        logging.error(f"일일 리포트 전송 중 오류: {e}")


def log_strategy_info():
    """전략 정보 로깅"""
    logging.info("=" * 80)
    logging.info("🤖 업비트 자동매매 봇 v2.2.7 (서버 점검 시 자동 복구)")
    logging.info("=" * 80)
    logging.info("📦 개선 사항:")
    logging.info("   1. [NEW] 진입 자산: min(가용KRW/빈슬롯, 총자산/코인개수)")
    logging.info("   2. [FIX] 매매 조건을 시가 → 현재가 기준으로 변경")
    logging.info("   3. [FIX] 4시간봉 MA 계산 오류 수정 (200개 데이터 제한 해결)")
    logging.info("   4. 베이지안 최적화 파라미터 적용 (MA, Stoch, Reverse)")
    logging.info("-" * 80)
    logging.info("📈 상승 전략:")
    logging.info("   - 조건1: 현재가 > MA (4H봉 기준)")
    logging.info("   - 조건2: Slow %K > Slow %D (1D봉 기준)")
    logging.info("-" * 80)
    logging.info("📉 역방향 전략:")
    logging.info("   - 조건: 현재가 < MA AND 오차율 <= 임계값")
    logging.info("   - 조건 충족 시 지정된 시간 동안 무조건 보유")
    logging.info("-" * 80)
    
    for ticker in COINS:
        ma_period = MA_PERIODS[ticker]
        stoch = STOCH_PARAMS.get(ticker, {})
        reverse = REVERSE_ERROR_RATE_CONFIG.get(ticker, {})
        
        logging.info(f"  {ticker}: MA{ma_period} | "
                    f"Stoch({stoch.get('k_period')},{stoch.get('k_smooth')},{stoch.get('d_period')}) | "
                    f"역방향: {reverse.get('error_rate')}% → {reverse.get('hold_hours')*4}h")
    
    logging.info("=" * 80)


def main():
    """메인 함수"""
    global BOT_START_TIME
    
    BOT_START_TIME = datetime.now()
    
    setup_shutdown_handlers()
    
    initialize_status()
    
    status_loaded = load_status()
    load_stoch_cache()
    
    log_strategy_info()
    
    send_start_alert(status_loaded)
    
    schedule.every().day.at("01:00").do(trade_strategy)
    schedule.every().day.at("05:00").do(trade_strategy)
    schedule.every().day.at("09:00").do(trade_strategy)
    schedule.every().day.at("13:00").do(trade_strategy)
    schedule.every().day.at("17:00").do(trade_strategy)
    schedule.every().day.at("21:00").do(trade_strategy)
    
    schedule.every().day.at("09:05").do(send_daily_report)
    
    logging.info("자동매매 스크립트 시작")
    logging.info("실행 시간: 매일 01:00, 05:00, 09:00, 13:00, 17:00, 21:00")
    logging.info("일일 리포트: 매일 09:05")
    logging.info(f"상태 저장 파일: {STATUS_FILE}")
    logging.info(f"캐시 저장 파일: {STOCH_CACHE_FILE}")
    
    logging.info("🚀 시작 시 전략 즉시 실행...")
    trade_strategy()
    
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
