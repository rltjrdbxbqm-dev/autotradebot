"""
================================================================================
업비트 자동매매 봇 v2.2 (베이지안 최적화 파라미터 적용)
================================================================================
개선 사항:
1. 자금 배분 로직 수정 - KRW 잔고 기반 계산으로 잔고 부족 오류 방지
2. 스토캐스틱 캐시 개선 - 일봉 마감(09:00) 기준 하루 1회 갱신
3. 역방향 상태 파일 저장 - 봇 재시작 시 데이터 손실 방지
4. [신규] 종료 시 텔레그램 알림 전송
5. [v2.2] 베이지안 최적화 파라미터 적용 (MA, 스토캐스틱, 역방향 전략)
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
from pyupbit import Upbit
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
            logging.info("텔레그램 알림 전송 성공")
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
    
    msg = f"🚀 <b>자동매매 봇 시작</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"📈 전략: MA + 스토캐스틱 + 역방향\n"
    msg += f"🪙 대상: {len(COINS)}개 코인\n"
    msg += f"⏰ 실행: 4시간 간격\n"
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

# 거래 대상 코인 리스트 (20개)
COINS = [
    'KRW-ADA', 'KRW-ANKR', 'KRW-AVAX', 'KRW-AXS', 'KRW-BCH',
    'KRW-BTC', 'KRW-CRO', 'KRW-DOGE', 'KRW-ETH', 'KRW-HBAR',
    'KRW-IMX', 'KRW-MANA', 'KRW-MVL', 'KRW-SAND', 'KRW-SOL',
    'KRW-THETA', 'KRW-VET', 'KRW-WAXP', 'KRW-XLM', 'KRW-XRP',
]

# 이동평균선 기간 설정 (4H봉 기준) - 베이지안 최적화 결과 적용
MA_PERIODS = {
    'KRW-ADA': 120,    # 기존 83 → 최적화 120 (CAGR 155.3%)
    'KRW-ANKR': 180,   # 기존 253 → 최적화 180 (CAGR 233.7%)
    'KRW-AVAX': 50,    # 기존 99 → 최적화 50 (CAGR 176.7%)
    'KRW-AXS': 276,
    'KRW-BCH': 100,    # 기존 99 → 최적화 100 (CAGR 78.4%)
    'KRW-BTC': 110,    # 기존 276 → 최적화 110 (CAGR 102.2%)
    'KRW-CRO': 90,     # 기존 253 → 최적화 90 (CAGR 175.4%)
    'KRW-DOGE': 70,    # 기존 213 → 최적화 70 (CAGR 141.9%)
    'KRW-ETH': 290,    # 기존 201 → 최적화 290 (CAGR 146.1%)
    'KRW-HBAR': 80,    # 기존 180 → 최적화 80 (CAGR 232.6%)
    'KRW-IMX': 140,    # 기존 137 → 최적화 140 (CAGR 282.1%)
    'KRW-MANA': 220,   # 기존 190 → 최적화 220 (CAGR 173.1%)
    'KRW-MVL': 240,    # 기존 163 → 최적화 240 (CAGR 130.2%)
    'KRW-SAND': 290,   # 기존 52 → 최적화 290 (CAGR 239.2%)
    'KRW-SOL': 230,    # 기존 254 → 최적화 230 (CAGR 156.2%)
    'KRW-THETA': 145,
    'KRW-VET': 50,     # 기존 172 → 최적화 50 (CAGR 223.3%)
    'KRW-WAXP': 271,
    'KRW-XLM': 80,     # 기존 115 → 최적화 80 (CAGR 139.3%)
    'KRW-XRP': 100,    # 기존 64 → 최적화 100 (CAGR 162.3%)
}

# 스토캐스틱 파라미터 (1D봉 기준) - 베이지안 최적화 결과 적용
STOCH_PARAMS = {
    'KRW-ADA': {'k_period': 170, 'k_smooth': 20, 'd_period': 25},   # 기존 (60,25,5)
    'KRW-ANKR': {'k_period': 200, 'k_smooth': 60, 'd_period': 10},  # 기존 (70,25,5)
    'KRW-AVAX': {'k_period': 150, 'k_smooth': 55, 'd_period': 15},  # 기존 (120,20,5)
    'KRW-AXS': {'k_period': 50, 'k_smooth': 20, 'd_period': 5},
    'KRW-BCH': {'k_period': 80, 'k_smooth': 30, 'd_period': 5},     # 기존 (50,30,5)
    'KRW-BTC': {'k_period': 140, 'k_smooth': 30, 'd_period': 5},    # 기존 (80,25,5)
    'KRW-CRO': {'k_period': 70, 'k_smooth': 45, 'd_period': 5},     # 기존 (120,45,5)
    'KRW-DOGE': {'k_period': 190, 'k_smooth': 40, 'd_period': 5},   # 기존 (50,30,5)
    'KRW-ETH': {'k_period': 60, 'k_smooth': 20, 'd_period': 5},     # 기존 (60,20,5) - 동일
    'KRW-HBAR': {'k_period': 160, 'k_smooth': 35, 'd_period': 5},   # 기존 (50,35,5)
    'KRW-IMX': {'k_period': 60, 'k_smooth': 20, 'd_period': 10},    # 기존 (50,20,5)
    'KRW-MANA': {'k_period': 50, 'k_smooth': 30, 'd_period': 5},    # 기존 (150,35,5)
    'KRW-MVL': {'k_period': 50, 'k_smooth': 50, 'd_period': 5},     # 기존 (50,50,5) - 동일
    'KRW-SAND': {'k_period': 120, 'k_smooth': 30, 'd_period': 5},   # 기존 (60,20,5)
    'KRW-SOL': {'k_period': 160, 'k_smooth': 25, 'd_period': 10},   # 기존 (50,30,5)
    'KRW-THETA': {'k_period': 120, 'k_smooth': 30, 'd_period': 5},
    'KRW-VET': {'k_period': 100, 'k_smooth': 45, 'd_period': 5},    # 기존 (50,30,5)
    'KRW-WAXP': {'k_period': 50, 'k_smooth': 30, 'd_period': 5},
    'KRW-XLM': {'k_period': 50, 'k_smooth': 20, 'd_period': 10},    # 기존 (50,25,5)
    'KRW-XRP': {'k_period': 50, 'k_smooth': 20, 'd_period': 5},     # 기존 (70,20,5)
}

# 역방향 전략 설정 - 베이지안 최적화 결과 적용
# hold_hours: 4H 캔들 수 (예: 84 = 84*4 = 336시간)
REVERSE_ERROR_RATE_CONFIG = {
    'KRW-ADA': {'error_rate': -25, 'hold_hours': 84},    # 기존 (-38, 56) → 최적화
    'KRW-ANKR': {'error_rate': -20, 'hold_hours': 44},   # 기존 (-51, 59)
    'KRW-AVAX': {'error_rate': -15, 'hold_hours': 20},   # 기존 (-48, 30)
    'KRW-AXS': {'error_rate': -59, 'hold_hours': 48},
    'KRW-BCH': {'error_rate': -55, 'hold_hours': 12},    # 기존 (-45, 80)
    'KRW-BTC': {'error_rate': -30, 'hold_hours': 40},    # 기존 (-35, 24)
    'KRW-CRO': {'error_rate': -35, 'hold_hours': 48},    # 기존 (-44, 23)
    'KRW-DOGE': {'error_rate': -20, 'hold_hours': 100},  # 기존 (-28, 25)
    'KRW-ETH': {'error_rate': -45, 'hold_hours': 92},    # 기존 (-46, 80)
    'KRW-HBAR': {'error_rate': -20, 'hold_hours': 60},   # 기존 (-73, 47)
    'KRW-IMX': {'error_rate': -25, 'hold_hours': 12},    # 기존 (-25, 55)
    'KRW-MANA': {'error_rate': -55, 'hold_hours': 16},   # 기존 (-21, 34)
    'KRW-MVL': {'error_rate': -50, 'hold_hours': 76},    # 기존 (-47, 61)
    'KRW-SAND': {'error_rate': -45, 'hold_hours': 84},   # 기존 (-22, 66)
    'KRW-SOL': {'error_rate': -55, 'hold_hours': 16},    # 기존 (-57, 15)
    'KRW-THETA': {'error_rate': -25, 'hold_hours': 51},
    'KRW-VET': {'error_rate': -15, 'hold_hours': 28},    # 기존 (-55, 78)
    'KRW-WAXP': {'error_rate': -39, 'hold_hours': 80},
    'KRW-XLM': {'error_rate': -35, 'hold_hours': 80},    # 기존 (-39, 16)
    'KRW-XRP': {'error_rate': -50, 'hold_hours': 48},    # 기존 (-43, 13)
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
    """스토캐스틱 캐시를 파일에 저장"""
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
    """KRW 잔고 조회"""
    try:
        return float(upbit.get_balance("KRW"))
    except Exception as e:
        logging.error(f"KRW 잔고 조회 중 오류: {e}")
        return 0


def get_total_asset():
    """총 자산 계산"""
    try:
        balances = upbit.get_balances()
        total_asset = 0.0
        
        for balance in balances:
            if balance['currency'] != 'KRW':
                ticker = f"KRW-{balance['currency']}"
                current_price = get_current_price(ticker)
                time.sleep(0.1)
                if current_price:
                    coin_value = float(balance['balance']) * current_price
                    total_asset += coin_value
            else:
                total_asset += float(balance['balance'])

        return total_asset
    except Exception as e:
        logging.error(f"총 자산 계산 중 오류 발생: {e}")
        return 0


def get_holdings_info():
    """보유 코인 정보 조회"""
    try:
        balances = upbit.get_balances()
        holdings = {}
        
        for balance in balances:
            if balance['currency'] != 'KRW' and float(balance['balance']) > 0:
                ticker = f"KRW-{balance['currency']}"
                current_price = get_current_price(ticker)
                time.sleep(0.1)
                if current_price:
                    coin_value = float(balance['balance']) * current_price
                    if coin_value >= 1000:
                        holdings[balance['currency']] = {
                            'balance': float(balance['balance']),
                            'price': current_price,
                            'value': coin_value
                        }
        
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
    """KRW 잔고 기반 투자금액 계산"""
    krw_balance = get_krw_balance()
    empty_slots = count_empty_slots()
    
    if empty_slots == 0:
        logging.info("매수 가능한 빈 슬롯이 없습니다.")
        return 0
    
    available_krw = krw_balance * 0.995
    invest_amount = available_krw / empty_slots
    
    if invest_amount < 5000:
        logging.warning(f"투자금액({invest_amount:,.0f}원)이 최소 주문금액(5000원) 미만입니다.")
        return 0
    
    logging.info(f"💰 자금 배분: KRW {krw_balance:,.0f}원 / 빈슬롯 {empty_slots}개 = 코인당 {invest_amount:,.0f}원")
    
    return invest_amount


# ============================================================
# 시세 조회 함수
# ============================================================

def get_current_price(ticker):
    """현재가 조회"""
    try:
        url = f"https://api.upbit.com/v1/ticker?markets={ticker}"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data and 'trade_price' in data[0]:
            return float(data[0]['trade_price'])
        return None
    except Exception as e:
        logging.error(f"{ticker} 현재가 조회 중 오류 발생: {e}")
        return None


def get_opening_price_4h(ticker):
    """4시간봉 현재 캔들의 시가 조회"""
    try:
        url = f"https://api.upbit.com/v1/candles/minutes/240?market={ticker}&count=1"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data:
            return float(data[0]['opening_price'])
        return None
    except Exception as e:
        logging.error(f"{ticker} 4H 시가 조회 중 오류 발생: {e}")
        return None


def get_hourly_ma(ticker, period):
    """4시간봉 이동평균 계산 (종가 기준)"""
    try:
        url = f"https://api.upbit.com/v1/candles/minutes/240?market={ticker}&count={period}"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data:
            closes = [float(candle['trade_price']) for candle in data]
            return np.mean(closes)
        return None
    except Exception as e:
        logging.error(f"{ticker} 이동평균선 계산 중 오류 발생: {e}")
        return None


def get_daily_ohlcv(ticker, count):
    """1일봉 OHLCV 데이터 조회"""
    try:
        url = f"https://api.upbit.com/v1/candles/days?market={ticker}&count={count}"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data:
            df = pd.DataFrame(data)
            df = df.rename(columns={
                'opening_price': 'open',
                'high_price': 'high',
                'low_price': 'low',
                'trade_price': 'close'
            })
            df = df[['open', 'high', 'low', 'close']].iloc[::-1].reset_index(drop=True)
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
    
    today_9am = now.replace(hour=9, minute=5, second=0, microsecond=0)
    
    if now >= today_9am and stoch_cache_date < today:
        logging.info(f"일봉 마감 후 첫 실행. 스토캐스틱 캐시 갱신합니다. (캐시날짜: {stoch_cache_date}, 오늘: {today})")
        return True
    
    return False


def refresh_all_stochastic():
    """모든 코인의 스토캐스틱 데이터 갱신"""
    global stoch_cache, stoch_cache_date
    
    logging.info("📊 스토캐스틱 데이터 전체 갱신 시작...")
    
    for ticker in COINS:
        try:
            params = STOCH_PARAMS.get(ticker)
            if not params:
                params = {'k_period': 200, 'k_smooth': 60, 'd_period': 30}
            
            required_count = params['k_period'] + params['k_smooth'] + params['d_period'] + 10
            df = get_daily_ohlcv(ticker, required_count)
            
            if df is None:
                logging.warning(f"{ticker} 일봉 데이터 조회 실패")
                continue
            
            slow_k, slow_d = calculate_stochastic(df, params['k_period'], params['k_smooth'], params['d_period'])
            
            if slow_k is not None and slow_d is not None:
                stoch_cache[ticker] = {
                    'signal': slow_k > slow_d,
                    'slow_k': slow_k,
                    'slow_d': slow_d
                }
                logging.debug(f"{ticker} 스토캐스틱: K={slow_k:.2f}, D={slow_d:.2f}, Signal={slow_k > slow_d}")
            
            time.sleep(0.1)
            
        except Exception as e:
            logging.error(f"{ticker} 스토캐스틱 계산 중 오류: {e}")
    
    stoch_cache_date = datetime.now().date()
    save_stoch_cache()
    
    logging.info(f"📊 스토캐스틱 데이터 갱신 완료: {len(stoch_cache)}개 코인")


def get_stochastic_signal(ticker):
    """스토캐스틱 시그널 조회"""
    global stoch_cache
    
    if should_refresh_stoch_cache():
        refresh_all_stochastic()
    
    if ticker in stoch_cache:
        return stoch_cache[ticker]
    
    try:
        params = STOCH_PARAMS.get(ticker)
        if not params:
            params = {'k_period': 200, 'k_smooth': 60, 'd_period': 30}
        
        required_count = params['k_period'] + params['k_smooth'] + params['d_period'] + 10
        df = get_daily_ohlcv(ticker, required_count)
        
        if df is None:
            return None
        
        slow_k, slow_d = calculate_stochastic(df, params['k_period'], params['k_smooth'], params['d_period'])
        
        if slow_k is None or slow_d is None:
            return None
        
        result = {
            'signal': slow_k > slow_d,
            'slow_k': slow_k,
            'slow_d': slow_d
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


def check_reverse_strategy(ticker, opening_price_4h, ma_price):
    """역방향 전략 체크"""
    global buy_status
    
    if ticker not in REVERSE_ERROR_RATE_CONFIG:
        return False, False, 0
    
    config = REVERSE_ERROR_RATE_CONFIG[ticker]
    error_rate_threshold = config['error_rate']
    hold_duration_hours = config['hold_hours'] * 4
    
    current_time = datetime.now()
    error_rate = calculate_error_rate(opening_price_4h, ma_price)
    
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
    
    if opening_price_4h < ma_price and error_rate <= error_rate_threshold:
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
    """거래 전략 실행"""
    try:
        krw_balance = get_krw_balance()
        total_asset = get_total_asset()
        
        logging.info("=" * 80)
        logging.info(f"📊 거래 전략 실행 시작 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"💰 총 자산: {total_asset:,.0f} KRW")
        logging.info(f"💵 KRW 잔고: {krw_balance:,.0f} KRW")
        logging.info("=" * 80)
        
        buy_count = 0
        sell_count = 0

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
                continue
            
            coin_currency = ticker.split('-')[1]
            current_balance = upbit.get_balance(coin_currency)
            
            reverse_signal, is_reverse_holding, error_rate = check_reverse_strategy(
                ticker, opening_price_4h, ma_price
            )
            
            ma_condition = opening_price_4h > ma_price
            
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
            
            logging.info(f"{ticker} | 시가:{opening_price_4h:,.0f} | MA:{ma_price:,.0f} | "
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
                        buy_count += 1
                        
                        send_trade_alert(
                            trade_type="BUY",
                            ticker=ticker,
                            amount=invest_amount,
                            strategy=strategy_type,
                            price=current_price,
                            error_rate=error_rate if strategy_type == "역방향" else None
                        )
                        
                        if strategy_type == "역방향":
                            logging.info(f"🔴 {ticker} 역방향 매수 주문 성공: {invest_amount:,.0f} KRW")
                        else:
                            logging.info(f"🟢 {ticker} 상승 매수 주문 성공: {invest_amount:,.0f} KRW")
                            
                    except Exception as e:
                        logging.error(f"{ticker} 매수 주문 실패: {e}")
                        send_error_alert(f"{ticker} 매수 주문 실패: {e}")
                else:
                    if strategy_type == "역방향":
                        logging.info(f"✅ {ticker} 역방향 전략 보유 중")
                    else:
                        logging.info(f"✅ {ticker} 상승 전략 보유 중")
            else:
                if current_balance > 0:
                    try:
                        upbit.sell_market_order(ticker, current_balance)
                        sell_count += 1
                        
                        if not ma_condition:
                            sell_reason = "MA 조건 위반"
                        elif not stoch_condition:
                            sell_reason = "스토캐스틱 조건 위반"
                        else:
                            sell_reason = "조건 미충족"
                        
                        send_trade_alert(
                            trade_type="SELL",
                            ticker=ticker,
                            quantity=current_balance,
                            strategy=sell_reason,
                            price=current_price
                        )
                        
                        logging.info(f"🔵 {ticker} 전량 매도 ({sell_reason})")
                    except Exception as e:
                        logging.error(f"{ticker} 매도 주문 실패: {e}")
                        send_error_alert(f"{ticker} 매도 주문 실패: {e}")
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
        
        if buy_count > 0 or sell_count > 0:
            summary_msg = f"📋 <b>거래 실행 완료</b>\n"
            summary_msg += f"━━━━━━━━━━━━━━━\n"
            summary_msg += f"🟢 매수: {buy_count}건\n"
            summary_msg += f"🔴 매도: {sell_count}건\n"
            summary_msg += f"💰 총 자산: {total_asset:,.0f}원"
            send_telegram(summary_msg)
                
        logging.info("=" * 80)
        logging.info(f"📊 거래 전략 실행 완료 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"   매수: {buy_count}건 / 매도: {sell_count}건")
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
    logging.info("🤖 업비트 자동매매 봇 v2.1 (종료 알림 추가)")
    logging.info("=" * 80)
    logging.info("📦 개선 사항:")
    logging.info("   1. 자금 배분: KRW 잔고 기반 계산 (잔고 부족 방지)")
    logging.info("   2. 스토캐스틱: 일봉 마감(09:00) 후 1회 갱신")
    logging.info("   3. 상태 저장: 봇 재시작 시 역방향 상태 복원")
    logging.info("   4. 종료 알림: Ctrl+C, kill 등 종료 시 텔레그램 알림")
    logging.info("-" * 80)
    logging.info("📈 상승 전략:")
    logging.info("   - 조건1: 4H 시가 > MA (4H봉 기준)")
    logging.info("   - 조건2: Slow %K > Slow %D (1D봉 기준)")
    logging.info("-" * 80)
    logging.info("📉 역방향 전략:")
    logging.info("   - 조건: 4H 시가 < MA AND 오차율 <= 임계값")
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
