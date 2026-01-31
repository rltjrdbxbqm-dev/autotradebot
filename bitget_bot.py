"""
================================================================================
Bitget Futures 자동매매 봇 v4.0 (Binance 신호 + Bitget 매매) + 텔레그램 알림
================================================================================
- 신호 데이터: Binance 공개 API (API 키 불필요)
- 매매 실행: Bitget API (헤지 모드)
- 지정가 5회 실패 시 시장가 전환
- 텔레그램 실시간 알림
- [v3.2] 자금 배분 로직 개선: 가용 잔고 기반 동적 계산
- [v3.3] 종료 시 텔레그램 알림 (kill, Ctrl+C 등)
- [v3.4] allocation_pct 정상 반영: 코인별 비율 배분 (BTC/ETH/SOL 30%, SUI 10%)
- [v3.5] 스토캐스틱 iloc[-1] + 일봉 시작 시점(09:00 KST) 캐싱
- [v3.6] 진입 자산 규모 제한: 기존 방식 vs 총자산×allocation_pct 중 작은 값 사용
- [v3.7] 텔레그램 메시지 통합: 거래 시간대별 종합 리포트 전송
- [v3.8] 서버 점검 시 자동 복구: API 실패 시 다음 스케줄에 자동 재시도
- [v4.0] 롱/숏 통합: 롱 조건 우선, 숏 조건 차선 (별도 파라미터)
  - 롱 조건: 가격 > MA AND K > D → leverage_up 배율로 롱 진입
  - 숏 조건: 가격 < MA AND K < D → 숏 레버리지로 숏 진입 (롱 미충족 시)
  - 충돌 시 롱 우선
================================================================================
"""

import requests
import hmac
import hashlib
import base64
import time
import json
import sys
import signal
import atexit
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Tuple
import logging
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 트레이딩 설정
# ═══════════════════════════════════════════════════════════════════════════════

TRADING_CONFIGS = [
    # ═══════════════════════════════════════════════════════════════════════════
    # 메이저 코인 (BTC, ETH, SOL) - 각 30% 배분 (총 90%)
    # ═══════════════════════════════════════════════════════════════════════════
    {
        'enabled': True,
        'symbol': 'BTCUSDT',
        'product_type': 'USDT-FUTURES',
        'margin_coin': 'USDT',
        'ma_period': 216,
        'ma_type': 'SMA',
        'timeframe': '4H',
        'stoch_k_period': 46,
        'stoch_k_smooth': 37,
        'stoch_d_period': 4,
        'leverage_up': 4,
        'leverage_down': 0,
        'tick_size': 0.1,
        'size_decimals': 4,
        'allocation_pct': 30.0,
        'position_size_pct': 99,
        'description': 'BTC MA248 + Stoch(46,37,4) Lev 4x/Cash'
    },
    {
        'enabled': True,
        'symbol': 'ETHUSDT',
        'product_type': 'USDT-FUTURES',
        'margin_coin': 'USDT',
        'ma_period': 152,
        'ma_type': 'SMA',
        'timeframe': '4H',
        'stoch_k_period': 58,
        'stoch_k_smooth': 23,
        'stoch_d_period': 18,
        'leverage_up': 4,
        'leverage_down': 0,
        'tick_size': 0.01,
        'size_decimals': 2,
        'allocation_pct': 30.0,
        'position_size_pct': 99,
        'description': 'ETH MA152 + Stoch(58,23,18) Lev 4x/Cash'
    },
    {
        'enabled': True,
        'symbol': 'SOLUSDT',
        'product_type': 'USDT-FUTURES',
        'margin_coin': 'USDT',
        'ma_period': 67,
        'ma_type': 'SMA',
        'timeframe': '4H',
        'stoch_k_period': 51,
        'stoch_k_smooth': 20,
        'stoch_d_period': 17,
        'leverage_up': 3,
        'leverage_down': 0,
        'tick_size': 0.001,
        'size_decimals': 1,
        'allocation_pct': 30.0,
        'position_size_pct': 99,
        'description': 'SOL MA64 + Stoch(51,20,16) Lev 2x/Cash'
    },
    # ═══════════════════════════════════════════════════════════════════════════
    # 알트코인 - SUI 10% 배분
    # ═══════════════════════════════════════════════════════════════════════════
    {
        'enabled': True,
        'symbol': 'SUIUSDT',
        'product_type': 'USDT-FUTURES',
        'margin_coin': 'USDT',
        'ma_period': 140,
        'ma_type': 'SMA',
        'timeframe': '4H',
        'stoch_k_period': 90,
        'stoch_k_smooth': 40,
        'stoch_d_period': 5,
        'leverage_up': 3,
        'leverage_down': 0,
        'tick_size': 0.0001,
        'size_decimals': 1,
        'allocation_pct': 10.0,
        'position_size_pct': 99,
        'description': 'SUI MA140 + Stoch(90,40,5) Lev 3x/Cash'
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 [v4.0] 숏 포지션 트레이딩 설정 (백테스트 결과 기반)
# ═══════════════════════════════════════════════════════════════════════════════
# 숏 조건: 가격 < MA AND K < D (롱 조건 미충족 시에만 적용)
# 숏 파라미터는 롱과 별도로 최적화된 값 사용

SHORT_TRADING_CONFIGS = [
    {
        'enabled': True,
        'symbol': 'BTCUSDT',
        'ma_period': 248,
        'stoch_k_period': 24,
        'stoch_k_smooth': 20,
        'stoch_d_period': 28,
        'leverage': 1,
        # CAGR: 11.0%, MDD: -36.4%, Sharpe: 0.42
    },
    {
        'enabled': True,
        'symbol': 'ETHUSDT',
        'ma_period': 227,
        'stoch_k_period': 32,
        'stoch_k_smooth': 43,
        'stoch_d_period': 26,
        'leverage': 1,
        # CAGR: 28.1%, MDD: -34.7%, Sharpe: 0.70
    },
    {
        'enabled': True,
        'symbol': 'SOLUSDT',
        'ma_period': 64,
        'stoch_k_period': 132,
        'stoch_k_smooth': 25,
        'stoch_d_period': 34,
        'leverage': 1,
        # CAGR: 33.7%, MDD: -34.2%, Sharpe: 0.69
    },
    {
        'enabled': True,
        'symbol': 'SUIUSDT',
        'ma_period': 308,
        'stoch_k_period': 162,
        'stoch_k_smooth': 68,
        'stoch_d_period': 50,
        'leverage': 1,
        # CAGR: 101.5%, MDD: -19.1%, Sharpe: 1.59
    },
]

# 숏 설정을 심볼로 빠르게 찾기 위한 딕셔너리
SHORT_CONFIG_BY_SYMBOL = {c['symbol']: c for c in SHORT_TRADING_CONFIGS if c['enabled']}

# 주문 설정
LIMIT_ORDER_TICKS = 1
ORDER_WAIT_SECONDS = 5
MAX_LIMIT_RETRY = 5
RETRY_DELAY_SECONDS = 1
SYMBOL_DELAY_SECONDS = 2  # 코인 간 API 호출 딜레이 (Rate Limit 방지)

# API 설정 (환경변수 사용)
API_KEY = os.getenv("BITGET_ACCESS_KEY")
API_SECRET = os.getenv("BITGET_SECRET_KEY")
API_PASSPHRASE = os.getenv("BITGET_PASSPHRASE")

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 텔레그램 설정
# ═══════════════════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 일반 설정
BASE_URL = "https://api.bitget.com"
DRY_RUN = False
LOG_LEVEL = logging.INFO
LOG_FILE = "trading_bot_binance_signal.log"
CANDLE_START_DELAY = 10
RETRY_INTERVAL = 60

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 종료 알림 관련 전역 변수
# ═══════════════════════════════════════════════════════════════════════════════

BOT_START_TIME = None
SHUTDOWN_SENT = False

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 거래 결과 수집용 전역 변수 (종합 메시지용)
# ═══════════════════════════════════════════════════════════════════════════════

trade_results = {
    'entries': [],      # 진입 내역
    'closes': [],       # 청산 내역
    'holds': [],        # 유지 중인 포지션
    'errors': [],       # 에러 내역
    'leverage_changes': []  # 레버리지 변경 내역
}

# 로깅
def setup_logging():
    logger = logging.getLogger('BitgetBot')
    logger.setLevel(LOG_LEVEL)
    if logger.handlers:
        logger.handlers.clear()
    ch = logging.StreamHandler()
    ch.setLevel(LOG_LEVEL)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
    fh.setLevel(LOG_LEVEL)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.propagate = False
    return logger

logger = setup_logging()


# ═══════════════════════════════════════════════════════════════════════════════
# 📌 텔레그램 알림 함수
# ═══════════════════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    """텔레그램 메시지 전송"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("텔레그램 설정이 되어있지 않습니다. .env 파일을 확인하세요.")
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
            logger.debug("텔레그램 알림 전송 성공")
            return True
        else:
            logger.error(f"텔레그램 전송 실패: {response.text}")
            return False
    except Exception as e:
        logger.error(f"텔레그램 전송 중 오류: {e}")
        return False


def send_entry_alert(symbol: str, side: str, size: str, price: float, 
                     leverage: int, order_type: str = "지정가"):
    """포지션 진입 내역 수집 (종합 메시지용)"""
    global trade_results
    
    trade_results['entries'].append({
        'symbol': symbol,
        'side': side,
        'size': size,
        'price': price,
        'leverage': leverage,
        'order_type': order_type
    })


def send_close_alert(symbol: str, size: float, entry_price: float, 
                     exit_price: float, pnl: float, reason: str = ""):
    """포지션 청산 내역 수집 (종합 메시지용)"""
    global trade_results
    
    trade_results['closes'].append({
        'symbol': symbol,
        'size': size,
        'entry_price': entry_price,
        'exit_price': exit_price,
        'pnl': pnl,
        'reason': reason
    })


def send_leverage_change_alert(symbol: str, old_lev: int, new_lev: int):
    """레버리지 변경 내역 수집 (종합 메시지용)"""
    global trade_results
    
    trade_results['leverage_changes'].append({
        'symbol': symbol,
        'old_lev': old_lev,
        'new_lev': new_lev
    })


def send_error_alert(symbol: str, error_message: str):
    """에러 내역 수집 (종합 메시지용)"""
    global trade_results
    
    trade_results['errors'].append({
        'symbol': symbol,
        'error': error_message
    })


def clear_trade_results():
    """거래 결과 초기화"""
    global trade_results
    trade_results = {
        'entries': [],
        'closes': [],
        'holds': [],
        'errors': [],
        'leverage_changes': []
    }


def add_hold_position(symbol: str, size: float, leverage: int, pnl: float, side: str = 'long'):
    """보유 유지 포지션 추가"""
    global trade_results
    trade_results['holds'].append({
        'symbol': symbol,
        'size': size,
        'leverage': leverage,
        'pnl': pnl,
        'side': side
    })


def send_trading_summary(total_equity: float, available: float):
    """
    거래 시간대별 종합 메시지 전송
    모든 거래 내역을 하나의 메시지로 통합하여 전송
    """
    global trade_results
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    msg = f"📊 <b>Bitget 거래 리포트</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"🕐 {now}\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    
    # 자산 현황
    msg += f"💰 총 자산: <b>${total_equity:,.2f}</b>\n"
    msg += f"💵 가용 잔고: ${available:,.2f}\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    
    # 진입 내역
    if trade_results['entries']:
        msg += f"🟢 <b>진입 ({len(trade_results['entries'])}건)</b>\n"
        for entry in trade_results['entries']:
            msg += f"  • {entry['symbol']}: ${entry['price']:,.2f}\n"
            msg += f"    └ {entry['size']} @ {entry['leverage']}x ({entry['order_type']})\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    # 청산 내역
    if trade_results['closes']:
        msg += f"🔴 <b>청산 ({len(trade_results['closes'])}건)</b>\n"
        total_pnl = 0
        for close in trade_results['closes']:
            pnl = close['pnl']
            total_pnl += pnl
            pnl_emoji = "💚" if pnl >= 0 else "❤️"
            pnl_sign = "+" if pnl >= 0 else ""
            msg += f"  • {close['symbol']}: {pnl_sign}{pnl:,.2f} {pnl_emoji}\n"
            if close['reason']:
                msg += f"    └ {close['reason']}\n"
        
        total_emoji = "💚" if total_pnl >= 0 else "❤️"
        total_sign = "+" if total_pnl >= 0 else ""
        msg += f"  📋 합계: {total_sign}{total_pnl:,.2f} USDT {total_emoji}\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    # 레버리지 변경 내역
    if trade_results['leverage_changes']:
        msg += f"🔄 <b>레버리지 변경 ({len(trade_results['leverage_changes'])}건)</b>\n"
        for lev in trade_results['leverage_changes']:
            old_str = f"{lev['old_lev']}x" if lev['old_lev'] > 0 else "현금"
            new_str = f"{lev['new_lev']}x" if lev['new_lev'] > 0 else "현금"
            msg += f"  • {lev['symbol']}: {old_str} → {new_str}\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    # 보유 유지 포지션
    if trade_results['holds']:
        msg += f"📌 <b>보유 유지 ({len(trade_results['holds'])}개)</b>\n"
        for hold in trade_results['holds']:
            pnl = hold['pnl']
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            pnl_sign = "+" if pnl >= 0 else ""
            side_str = "L" if hold.get('side', 'long') == 'long' else "S"
            msg += f"  {pnl_emoji} {hold['symbol']}: {side_str} {hold['leverage']}x ({pnl_sign}{pnl:,.2f})\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    # 에러 내역
    if trade_results['errors']:
        msg += f"⚠️ <b>오류 ({len(trade_results['errors'])}건)</b>\n"
        for err in trade_results['errors'][:5]:  # 최대 5개만 표시
            msg += f"  • {err['symbol']}: {err['error'][:40]}\n"
        if len(trade_results['errors']) > 5:
            msg += f"  ... 외 {len(trade_results['errors']) - 5}건\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    # 거래 없음
    entries = len(trade_results['entries'])
    closes = len(trade_results['closes'])
    lev_changes = len(trade_results['leverage_changes'])
    
    if entries == 0 and closes == 0 and lev_changes == 0 and not trade_results['errors']:
        holds_count = len(trade_results['holds'])
        if holds_count > 0:
            msg += f"ℹ️ 이번 시간대 거래 없음\n"
            msg += f"   {holds_count}개 포지션 유지\n"
        else:
            msg += f"ℹ️ 이번 시간대 거래 없음 (현금 유지)\n"
        msg += f"━━━━━━━━━━━━━━━\n"
    
    # 요약
    msg += f"📋 진입: {entries}건 / 청산: {closes}건"
    
    send_telegram(msg)
    
    # 결과 초기화
    clear_trade_results()


def send_bot_start_alert(configs: List[Dict], total_equity: float):
    """봇 시작 알림"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    msg = f"🚀 <b>Bitget 선물봇 시작 v4.0</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"📡 신호: Binance API\n"
    msg += f"💹 매매: Bitget API\n"
    msg += f"💰 총 자산: <b>${total_equity:,.2f}</b>\n"
    msg += f"📊 활성 전략: {len(configs)}개\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    
    msg += f"<b>📈 롱 전략:</b>\n"
    for c in configs:
        e_desc = "현금" if c['leverage_down'] == 0 else f"{c['leverage_down']}x"
        alloc = c.get('allocation_pct', 0)
        msg += f"• {c['symbol']}: {alloc:.0f}% / {c['leverage_up']}x/{e_desc}\n"
    
    msg += f"\n<b>📉 숏 전략:</b>\n"
    for c in SHORT_TRADING_CONFIGS:
        if c['enabled']:
            msg += f"• {c['symbol']}: {c['leverage']}x\n"
    
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"🕐 {now}"
    
    send_telegram(msg)


def send_shutdown_alert(reason: str = "수동 종료"):
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
    
    msg = f"🛑 <b>Bitget 선물봇 종료</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"📋 종료 사유: {reason}\n"
    msg += f"⏱️ 실행 시간: {uptime_str}\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"🕐 {now}"
    
    send_telegram(msg)
    logger.info(f"종료 알림 전송 완료: {reason}")


def send_portfolio_alert(total_equity: float, available: float, pnl: float, positions: List[Dict]):
    """포트폴리오 현황 알림"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    pnl_emoji = "💚" if pnl >= 0 else "❤️"
    pnl_sign = "+" if pnl >= 0 else ""
    
    msg = f"📊 <b>포트폴리오 현황</b>\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"💰 총 자산: <b>${total_equity:,.2f}</b>\n"
    msg += f"💵 가용 잔고: ${available:,.2f}\n"
    msg += f"{pnl_emoji} 미실현 손익: {pnl_sign}{pnl:,.2f}\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    
    if positions:
        msg += f"<b>보유 포지션</b>\n"
        for p in positions:
            pos_pnl = p.get('pnl', 0)
            pos_emoji = "🟢" if pos_pnl >= 0 else "🔴"
            msg += f"{pos_emoji} {p['symbol']}: {p['size']} @ {p['leverage']}x ({pos_pnl:+,.2f})\n"
    else:
        msg += f"📍 보유 포지션 없음\n"
    
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"🕐 {now}"
    
    send_telegram(msg)


# ═══════════════════════════════════════════════════════════════════════════════
# 📌 종료 핸들러 설정
# ═══════════════════════════════════════════════════════════════════════════════

def signal_handler(signum, frame):
    """시그널 핸들러"""
    signal_names = {
        signal.SIGINT: "SIGINT (Ctrl+C)",
        signal.SIGTERM: "SIGTERM (kill)",
    }
    if hasattr(signal, 'SIGHUP'):
        signal_names[signal.SIGHUP] = "SIGHUP (터미널 종료)"
    
    signal_name = signal_names.get(signum, f"Signal {signum}")
    
    logger.info(f"종료 시그널 수신: {signal_name}")
    send_shutdown_alert(reason=signal_name)
    
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
    logger.info("종료 핸들러 설정 완료")


# ═══════════════════════════════════════════════════════════════════════════════
# 📊 Binance 공개 API 클라이언트 (신호 데이터 전용, API 키 불필요)
# ═══════════════════════════════════════════════════════════════════════════════

class BinancePublicClient:
    """Binance 공개 API 클라이언트 (API 키 불필요)"""
    
    BASE_URL = "https://fapi.binance.com"
    
    TIMEFRAME_MAP = {
        '1m': '1m',
        '5m': '5m',
        '15m': '15m',
        '30m': '30m',
        '1H': '1h',
        '4H': '4h',
        '1D': '1d',
        '1W': '1w',
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json'
        })
    
    def _request(self, endpoint: str, params: Dict = None) -> any:
        url = self.BASE_URL + endpoint
        try:
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                logger.error(f"Binance API 오류: {resp.status_code} - {resp.text}")
                return None
            return resp.json()
        except Exception as e:
            logger.error(f"Binance API 요청 실패: {e}")
            return None
    
    def get_ticker(self, symbol: str) -> Dict:
        data = self._request("/fapi/v1/ticker/price", {'symbol': symbol})
        if data:
            return {
                'symbol': data.get('symbol'),
                'lastPr': data.get('price'),
                'price': float(data.get('price', 0))
            }
        return None
    
    def get_candles(self, symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
        binance_interval = self.TIMEFRAME_MAP.get(interval, interval.lower())
        
        data = self._request("/fapi/v1/klines", {
            'symbol': symbol,
            'interval': binance_interval,
            'limit': limit
        })
        
        if not data:
            return pd.DataFrame()
        
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        
        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
        for c in ['open', 'high', 'low', 'close', 'volume']:
            df[c] = df[c].astype(float)
        
        return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].sort_values('timestamp').reset_index(drop=True)
    
    def get_candles_pagination(self, symbol: str, interval: str, required_count: int = 300) -> pd.DataFrame:
        binance_interval = self.TIMEFRAME_MAP.get(interval, interval.lower())
        
        all_df = pd.DataFrame()
        end_ts = int(time.time() * 1000)
        
        while len(all_df) < required_count:
            data = self._request("/fapi/v1/klines", {
                'symbol': symbol,
                'interval': binance_interval,
                'endTime': end_ts,
                'limit': 1000
            })
            
            if not data:
                break
            
            df = pd.DataFrame(data, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])
            
            df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
            for c in ['open', 'high', 'low', 'close', 'volume']:
                df[c] = df[c].astype(float)
            
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
            
            all_df = pd.concat([df, all_df]).drop_duplicates(subset=['timestamp']).sort_values('timestamp')
            end_ts = int(df['timestamp'].min().timestamp() * 1000) - 1
            
            if len(all_df) >= required_count:
                break
            
            time.sleep(0.1)
        
        return all_df.reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 📈 Bitget API 클라이언트 (매매 전용)
# ═══════════════════════════════════════════════════════════════════════════════

class BitgetClient:
    """Bitget API V2 클라이언트 (헤지 모드 지원) - 매매 전용"""
    
    def __init__(self, api_key: str, api_secret: str, passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = BASE_URL
        self.session = requests.Session()
        self._position_mode = None
    
    def _get_timestamp(self) -> str:
        return str(int(time.time() * 1000))
    
    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(self.api_secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode('utf-8')
    
    def _get_headers(self, method: str, request_path: str, body: str = "") -> Dict:
        ts = self._get_timestamp()
        return {
            'ACCESS-KEY': self.api_key,
            'ACCESS-SIGN': self._sign(ts, method, request_path, body),
            'ACCESS-TIMESTAMP': ts,
            'ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json',
            'locale': 'en-US'
        }
    
    def _request(self, method: str, endpoint: str, params: Dict = None, body: Dict = None, retry_count: int = 3) -> Dict:
        url = self.base_url + endpoint
        request_path = endpoint
        
        if params:
            qs = '&'.join([f"{k}={v}" for k, v in params.items()])
            request_path = endpoint + '?' + qs
            url = url + '?' + qs
        
        body_str = json.dumps(body, separators=(',', ':')) if body else ""
        
        # 심볼 추출 (에러 알림용)
        symbol = params.get('symbol', '') if params else ''
        if body:
            symbol = body.get('symbol', symbol)
        
        for attempt in range(retry_count):
            headers = self._get_headers(method, request_path, body_str)
            
            try:
                if method == 'GET':
                    resp = self.session.get(url, headers=headers, timeout=30)
                else:
                    resp = self.session.post(url, headers=headers, data=body_str, timeout=30)
                data = resp.json()
                
                # Rate Limit 처리 (429)
                if data.get('code') == '429':
                    wait_time = (attempt + 1) * 2  # 2초, 4초, 6초...
                    logger.warning(f"Rate Limit 발생, {wait_time}초 대기 후 재시도 ({attempt + 1}/{retry_count})")
                    if attempt == 0:  # 첫 Rate Limit 시 텔레그램 알림
                        send_error_alert(symbol or 'API', f"Rate Limit (429) 발생 - 재시도 중...")
                    time.sleep(wait_time)
                    continue
                
                if data.get('code') != '00000':
                    error_msg = f"Code: {data.get('code')}, Msg: {data.get('msg', 'Unknown')}"
                    logger.error(f"API 오류: {data}")
                    send_error_alert(symbol or 'API', error_msg)
                    return None
                return data.get('data')
            except Exception as e:
                logger.error(f"API 요청 실패: {e}")
                if attempt < retry_count - 1:
                    time.sleep(1)
                    continue
                send_error_alert(symbol or 'API', f"API 요청 실패: {str(e)}")
                return None
        
        # 모든 재시도 실패
        error_msg = f"API 요청 {retry_count}회 재시도 실패 (Rate Limit)"
        logger.error(error_msg)
        send_error_alert(symbol or 'API', error_msg)
        return None
    
    def get_position_mode(self, product_type: str = 'USDT-FUTURES') -> str:
        if self._position_mode:
            return self._position_mode
        data = self._request('GET', "/api/v2/mix/account/account", {
            'symbol': 'BTCUSDT',
            'productType': product_type,
            'marginCoin': 'USDT'
        })
        if data:
            self._position_mode = data.get('posMode', 'one_way_mode')
        else:
            self._position_mode = 'one_way_mode'
        logger.info(f"📋 포지션 모드: {self._position_mode}")
        return self._position_mode
    
    def is_hedge_mode(self, product_type: str = 'USDT-FUTURES') -> bool:
        return self.get_position_mode(product_type) == 'hedge_mode'
    
    def get_account(self, product_type: str = 'USDT-FUTURES', margin_coin: str = 'USDT') -> Dict:
        data = self._request('GET', "/api/v2/mix/account/accounts", {'productType': product_type})
        if not data:
            return None
        for acc in data:
            if acc.get('marginCoin') == margin_coin:
                return acc
        return data[0] if data else None
    
    def get_position(self, symbol: str, product_type: str = 'USDT-FUTURES', margin_coin: str = 'USDT') -> List[Dict]:
        data = self._request('GET', "/api/v2/mix/position/single-position", {
            'symbol': symbol,
            'productType': product_type,
            'marginCoin': margin_coin
        })
        return data if data else []
    
    def set_leverage(self, symbol: str, leverage: int, product_type: str = 'USDT-FUTURES',
                     margin_coin: str = 'USDT', hold_side: str = 'long') -> bool:
        body = {
            'symbol': symbol,
            'productType': product_type,
            'marginCoin': margin_coin,
            'leverage': str(leverage)
        }
        if self.is_hedge_mode(product_type):
            body['holdSide'] = hold_side
        result = self._request('POST', "/api/v2/mix/account/set-leverage", body=body)
        return result is not None
    
    def get_ticker(self, symbol: str, product_type: str = 'USDT-FUTURES') -> Dict:
        data = self._request('GET', "/api/v2/mix/market/ticker", {
            'symbol': symbol,
            'productType': product_type
        })
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
        return data
    
    def cancel_all_orders(self, symbol: str, product_type: str = 'USDT-FUTURES', margin_coin: str = 'USDT') -> bool:
        """미체결 주문 전체 취소 (취소할 주문이 없어도 정상 처리)"""
        try:
            timestamp = int(time.time() * 1000)
            method = 'POST'
            path = "/api/v2/mix/order/cancel-all-orders"
            body = {
                'symbol': symbol,
                'productType': product_type,
                'marginCoin': margin_coin
            }
            body_str = json.dumps(body)
            sign = self._get_sign(timestamp, method, path, body_str)
            
            headers = {
                'ACCESS-KEY': self.api_key,
                'ACCESS-SIGN': sign,
                'ACCESS-TIMESTAMP': str(timestamp),
                'ACCESS-PASSPHRASE': self.passphrase,
                'Content-Type': 'application/json',
                'locale': 'en-US'
            }
            
            resp = requests.post(f"{BASE_URL}{path}", headers=headers, data=body_str, timeout=10)
            data = resp.json()
            
            # '22001' (No order to cancel)은 정상 케이스로 처리
            if data.get('code') == '00000':
                logger.debug(f"[{symbol}] 미체결 주문 취소 완료")
                return True
            elif data.get('code') == '22001':
                logger.debug(f"[{symbol}] 취소할 주문 없음 (정상)")
                return True
            else:
                logger.error(f"[{symbol}] 주문 취소 실패: {data}")
                return False
        except Exception as e:
            logger.error(f"[{symbol}] 주문 취소 예외: {e}")
            return False
    
    def get_order(self, symbol: str, order_id: str, product_type: str = 'USDT-FUTURES') -> Dict:
        return self._request('GET', "/api/v2/mix/order/detail", {
            'symbol': symbol,
            'productType': product_type,
            'orderId': order_id
        })
    
    def cancel_order(self, symbol: str, order_id: str, product_type: str = 'USDT-FUTURES') -> Dict:
        return self._request('POST', "/api/v2/mix/order/cancel-order", body={
            'symbol': symbol,
            'productType': product_type,
            'orderId': order_id
        })
    
    def place_limit_order(self, symbol: str, side: str, size: str, price: str,
                          trade_side: str, pos_side: str,
                          product_type: str = 'USDT-FUTURES', margin_coin: str = 'USDT') -> Dict:
        body = {
            'symbol': symbol,
            'productType': product_type,
            'marginMode': 'crossed',
            'marginCoin': margin_coin,
            'size': size,
            'price': price,
            'side': side,
            'tradeSide': trade_side,
            'posSide': pos_side,
            'orderType': 'limit',
            'force': 'gtc'
        }
        return self._request('POST', "/api/v2/mix/order/place-order", body=body)
    
    def place_market_order(self, symbol: str, side: str, size: str,
                           trade_side: str, pos_side: str,
                           product_type: str = 'USDT-FUTURES', margin_coin: str = 'USDT') -> Dict:
        body = {
            'symbol': symbol,
            'productType': product_type,
            'marginMode': 'crossed',
            'marginCoin': margin_coin,
            'size': size,
            'side': side,
            'tradeSide': trade_side,
            'posSide': pos_side,
            'orderType': 'market'
        }
        return self._request('POST', "/api/v2/mix/order/place-order", body=body)
    
    def flash_close_position(self, symbol: str, product_type: str = 'USDT-FUTURES', hold_side: str = 'long') -> Dict:
        return self._request('POST', "/api/v2/mix/order/close-positions", body={
            'symbol': symbol,
            'productType': product_type,
            'holdSide': hold_side
        })


# ═══════════════════════════════════════════════════════════════════════════════
# 📊 [v3.4 개선] 포트폴리오 매니저 - allocation_pct 비율 배분 적용
# ═══════════════════════════════════════════════════════════════════════════════

class PortfolioManager:
    def __init__(self, client: BitgetClient, configs: List[Dict]):
        self.client = client
        self.configs = [c for c in configs if c['enabled']]
        # config를 symbol로 빠르게 찾기 위한 딕셔너리
        self.config_by_symbol = {c['symbol']: c for c in self.configs}
    
    def get_account_info(self) -> Dict:
        """계좌 정보 조회 (총 자산, 가용 잔고, 마진 등)"""
        if not self.configs:
            return {'equity': 0, 'available': 0, 'margin': 0, 'pnl': 0}
        
        acc = self.client.get_account(self.configs[0]['product_type'], self.configs[0]['margin_coin'])
        if not acc:
            return {'equity': 0, 'available': 0, 'margin': 0, 'pnl': 0}
        
        return {
            'equity': float(acc.get('usdtEquity', 0)),
            'available': float(acc.get('crossedMaxAvailable', 0)),
            'margin': float(acc.get('crossedMargin', 0)),
            'pnl': float(acc.get('unrealizedPL', 0))
        }
    
    def get_total_equity(self) -> float:
        """총 자산 조회"""
        return self.get_account_info()['equity']
    
    def get_available_balance(self) -> float:
        """가용 잔고 조회"""
        return self.get_account_info()['available']
    
    def get_position_status(self) -> Dict[str, bool]:
        """
        각 심볼별 포지션 보유 여부 확인
        Returns: {'BTCUSDT': True, 'ETHUSDT': False, ...}
        """
        status = {}
        for cfg in self.configs:
            pos_data = self.client.get_position(cfg['symbol'], cfg['product_type'], cfg['margin_coin'])
            has_position = False
            if pos_data:
                for p in pos_data:
                    if float(p.get('total', 0)) > 0:
                        has_position = True
                        break
            status[cfg['symbol']] = has_position
        return status
    
    def count_empty_slots(self) -> int:
        """포지션이 없는 빈 슬롯 수 계산"""
        status = self.get_position_status()
        return sum(1 for has_pos in status.values() if not has_pos)
    
    def calculate_invest_amount_for_symbol(self, symbol: str) -> float:
        """
        [v3.4 핵심 개선] allocation_pct를 반영한 개별 코인 투자금액 계산
        
        로직:
        1. 가용 잔고 확인
        2. 빈 슬롯(포지션 없는 코인)들의 allocation_pct 합계 계산
        3. 해당 심볼의 allocation_pct 비율로 가용 잔고 배분
        
        예시: 가용 잔고 $1000, BTC(30%)/ETH(30%) 빈 슬롯
        - 빈 슬롯 합계: 60%
        - BTC 배분: $1000 * (30% / 60%) = $500
        - ETH 배분: $1000 * (30% / 60%) = $500
        """
        available = self.get_available_balance()
        if available <= 0:
            logger.warning(f"[{symbol}] 가용 잔고가 0입니다")
            return 0
        
        # 해당 심볼의 config 찾기
        target_config = self.config_by_symbol.get(symbol)
        if not target_config:
            logger.error(f"[{symbol}] config를 찾을 수 없습니다")
            return 0
        
        target_allocation = target_config.get('allocation_pct', 0)
        if target_allocation <= 0:
            logger.warning(f"[{symbol}] allocation_pct가 0입니다")
            return 0
        
        # 포지션 상태 확인
        position_status = self.get_position_status()
        
        # 빈 슬롯들의 allocation_pct 합계 계산
        empty_allocation_sum = 0
        for cfg in self.configs:
            if not position_status.get(cfg['symbol'], False):  # 빈 슬롯
                empty_allocation_sum += cfg.get('allocation_pct', 0)
        
        if empty_allocation_sum <= 0:
            logger.info(f"[{symbol}] 모든 슬롯에 포지션 보유 중")
            return 0
        
        # 해당 심볼이 빈 슬롯인지 확인
        if position_status.get(symbol, False):
            logger.info(f"[{symbol}] 이미 포지션 보유 중")
            return 0
        
        # 수수료 0.5% 고려하여 99.5%만 사용
        usable_balance = available * 0.995
        
        # allocation_pct 비율에 따라 배분
        invest_amount = usable_balance * (target_allocation / empty_allocation_sum)
        
        logger.info(f"[{symbol}] 💰 자금 배분 (v3.4): 가용 ${available:,.2f} × "
                   f"({target_allocation:.0f}% / {empty_allocation_sum:.0f}%) = ${invest_amount:,.2f}")
        
        return invest_amount
    
    def get_allocated_capital(self, config: Dict) -> float:
        """
        [v3.4 개선] 개별 코인 배분 자본 계산
        - allocation_pct 비율에 따라 배분
        """
        return self.calculate_invest_amount_for_symbol(config['symbol'])
    
    def get_all_positions(self) -> List[Dict]:
        """모든 활성 포지션 조회"""
        positions = []
        for cfg in self.configs:
            pos_data = self.client.get_position(cfg['symbol'], cfg['product_type'], cfg['margin_coin'])
            if pos_data:
                for p in pos_data:
                    total = float(p.get('total', 0))
                    if total > 0:
                        positions.append({
                            'symbol': cfg['symbol'],
                            'size': total,
                            'leverage': int(p.get('leverage', 0)),
                            'pnl': float(p.get('unrealizedPL', 0)),
                            'avg_price': float(p.get('averageOpenPrice', 0))
                        })
        return positions
    
    def log_portfolio_status(self, send_alert: bool = False):
        """포트폴리오 현황 로깅"""
        acc_info = self.get_account_info()
        equity = acc_info['equity']
        available = acc_info['available']
        margin = acc_info['margin']
        pnl = acc_info['pnl']
        
        position_status = self.get_position_status()
        empty_count = sum(1 for has_pos in position_status.values() if not has_pos)
        total_slots = len(self.configs)
        
        logger.info(f"\n{'='*70}")
        logger.info(f"💰 포트폴리오 현황 (Bitget) [v3.6 - 진입자산 상한선 적용]")
        logger.info(f"{'='*70}")
        logger.info(f"   총 자산: {equity:,.2f} USDT")
        logger.info(f"   가용 잔고: {available:,.2f} USDT")
        logger.info(f"   사용 마진: {margin:,.2f} USDT")
        logger.info(f"   미실현 손익: {pnl:+,.2f} USDT")
        logger.info(f"   슬롯 현황: {total_slots - empty_count}/{total_slots} 사용 중")
        
        if empty_count > 0:
            # 빈 슬롯들의 allocation 합계 계산
            empty_allocation_sum = 0
            empty_symbols = []
            for cfg in self.configs:
                if not position_status.get(cfg['symbol'], False):
                    empty_allocation_sum += cfg.get('allocation_pct', 0)
                    empty_symbols.append(f"{cfg['symbol']}({cfg.get('allocation_pct', 0):.0f}%)")
            
            logger.info(f"   빈 슬롯: {', '.join(empty_symbols)}")
            logger.info(f"   빈 슬롯 배분 합계: {empty_allocation_sum:.0f}%")
            
            usable = available * 0.995
            logger.info(f"   신규 진입 가능 금액: {usable:,.2f} USDT")
            
            # 각 빈 슬롯별 예상 배분 금액
            for cfg in self.configs:
                if not position_status.get(cfg['symbol'], False):
                    alloc_pct = cfg.get('allocation_pct', 0)
                    expected = usable * (alloc_pct / empty_allocation_sum) if empty_allocation_sum > 0 else 0
                    logger.info(f"     → {cfg['symbol']}: ${expected:,.2f} ({alloc_pct:.0f}%/{empty_allocation_sum:.0f}%)")
        
        logger.info(f"{'='*70}\n")
        
        if send_alert:
            positions = self.get_all_positions()
            send_portfolio_alert(equity, available, pnl, positions)


# ═══════════════════════════════════════════════════════════════════════════════
# 📈 트레이딩 봇
# ═══════════════════════════════════════════════════════════════════════════════

class TradingBot:
    def __init__(self, bitget_client: BitgetClient, binance_client: BinancePublicClient, 
                 config: Dict, portfolio: PortfolioManager):
        self.client = bitget_client
        self.signal_client = binance_client
        self.config = config
        self.portfolio = portfolio
        self.symbol = config['symbol']
        self.product_type = config['product_type']
        self.margin_coin = config['margin_coin']
        self.ma_period = config['ma_period']
        self.ma_type = config['ma_type']
        self.timeframe = config['timeframe']
        self.stoch_k_period = config['stoch_k_period']
        self.stoch_k_smooth = config['stoch_k_smooth']
        self.stoch_d_period = config['stoch_d_period']
        self.leverage_up = config['leverage_up']
        self.leverage_down = config['leverage_down']
        self.tick_size = config.get('tick_size', 0.1)
        self.size_decimals = config.get('size_decimals', 3)
        self.position_size_pct = config['position_size_pct']
        self.allocation_pct = config.get('allocation_pct', 25.0)
        self.description = config.get('description', self.symbol)
        
        # ═══════════════════════════════════════════════════════════════════════
        # [v4.0] 숏 포지션 설정 (별도 파라미터)
        # ═══════════════════════════════════════════════════════════════════════
        short_config = SHORT_CONFIG_BY_SYMBOL.get(self.symbol, {})
        self.short_enabled = short_config.get('enabled', False)
        self.short_ma_period = short_config.get('ma_period', self.ma_period)
        self.short_stoch_k_period = short_config.get('stoch_k_period', self.stoch_k_period)
        self.short_stoch_k_smooth = short_config.get('stoch_k_smooth', self.stoch_k_smooth)
        self.short_stoch_d_period = short_config.get('stoch_d_period', self.stoch_d_period)
        self.short_leverage = short_config.get('leverage', 1)
        
        # ═══════════════════════════════════════════════════════════════════════
        # [v3.5] 스토캐스틱 캐시 - 일봉 시작 시점(UTC 00:00 = KST 09:00) 기준
        # ═══════════════════════════════════════════════════════════════════════
        self._stoch_cache = {
            'utc_date': None,      # 캐시된 UTC 날짜 (YYYY-MM-DD)
            'is_bull': False,      # K > D 여부
            'k': 0.0,              # K 값
            'd': 0.0               # D 값
        }
        
        # [v4.0] 숏용 스토캐스틱 캐시
        self._stoch_cache_short = {
            'utc_date': None,
            'is_bear': False,      # K < D 여부
            'k': 0.0,
            'd': 0.0
        }
    
    def round_price(self, price: float) -> float:
        return round(price / self.tick_size) * self.tick_size
    
    def format_price(self, price: float) -> str:
        if self.tick_size >= 1: return f"{price:.0f}"
        elif self.tick_size >= 0.1: return f"{price:.1f}"
        elif self.tick_size >= 0.01: return f"{price:.2f}"
        elif self.tick_size >= 0.001: return f"{price:.3f}"
        else: return f"{price:.4f}"
    
    def format_size(self, size: float) -> str:
        return f"{size:.{self.size_decimals}f}"
    
    def get_current_position(self) -> Dict:
        """[v4.0] 현재 포지션 조회 (롱/숏 모두)"""
        positions = self.client.get_position(self.symbol, self.product_type, self.margin_coin)
        result = {
            'long': None,
            'short': None
        }
        
        if not positions:
            return result
        
        for p in positions:
            total = float(p.get('total', 0))
            if total > 0:
                pos_info = {
                    'side': p.get('holdSide'),
                    'size': total,
                    'avg_price': float(p.get('averageOpenPrice', 0)),
                    'unrealized_pnl': float(p.get('unrealizedPL', 0)),
                    'leverage': int(p.get('leverage', 0))
                }
                if p.get('holdSide') == 'long':
                    result['long'] = pos_info
                elif p.get('holdSide') == 'short':
                    result['short'] = pos_info
        
        return result
    
    def wait_for_fill(self, order_id: str, timeout: int = ORDER_WAIT_SECONDS) -> Tuple[str, float]:
        start = time.time()
        while time.time() - start < timeout:
            order = self.client.get_order(self.symbol, order_id, self.product_type)
            if not order:
                time.sleep(0.5)
                continue
            
            status = order.get('state', '')
            filled_size = float(order.get('baseVolume', 0))
            
            if status == 'filled':
                return 'filled', filled_size
            if status in ['canceled', 'cancelled']:
                return 'canceled', filled_size
            
            time.sleep(0.5)
        
        order = self.client.get_order(self.symbol, order_id, self.product_type)
        if order:
            filled_size = float(order.get('baseVolume', 0))
            if order.get('state') == 'filled':
                return 'filled', filled_size
            elif filled_size > 0:
                return 'partially_filled', filled_size
        
        return 'timeout', 0
    
    # ═══════════════════════════════════════════════════════════════════════════
    # [v3.4 개선] 포지션 크기 계산 - allocation_pct 비율 반영
    # ═══════════════════════════════════════════════════════════════════════════
    
    def calculate_position_size(self, price: float, leverage: int) -> str:
        """
        [v3.6 개선] 진입 자산 규모 제한 로직 추가
        - 기존 방식(가용잔고 기반 동적 배분)과 
        - 총자산 × allocation_pct × position_size_pct 중 작은 값 사용
        """
        if leverage <= 0:
            return "0"
        
        # [v3.4] 기존 방식: 가용잔고 기반 allocation_pct 반영한 투자금액
        allocated_by_available = self.portfolio.calculate_invest_amount_for_symbol(self.symbol)
        
        # [v3.6] 새 방식: 총자산 × allocation_pct (상한선)
        total_equity = self.portfolio.get_total_equity()
        max_by_equity = total_equity * (self.allocation_pct / 100)
        
        # 두 방식 중 작은 값 선택
        allocated = min(allocated_by_available, max_by_equity)
        
        logger.info(f"[{self.symbol}] 📊 자금 배분 비교: "
                   f"가용잔고 기반=${allocated_by_available:.2f}, "
                   f"총자산 기반=${max_by_equity:.2f} → 선택: ${allocated:.2f}")
        
        if allocated <= 0:
            logger.warning(f"[{self.symbol}] 배분 가능한 자금이 없습니다")
            return "0"
        
        # position_size_pct 적용 (99% 사용)
        use = allocated * (self.position_size_pct / 100)
        
        if use < 5:
            logger.warning(f"[{self.symbol}] 주문 금액이 최소 5 USDT 미만: {use:.2f}")
            return "0"
        
        min_sizes = {'BTCUSDT': 0.001, 'ETHUSDT': 0.01, 'SOLUSDT': 0.1, 'SUIUSDT': 0.1}
        min_size = min_sizes.get(self.symbol, 0.001)
        
        # 레버리지 적용하여 수량 계산
        size = (use * leverage) / price
        size = max(min_size, round(size, self.size_decimals))
        
        logger.info(f"[{self.symbol}] 💵 최종배분({self.allocation_pct:.0f}%): ${allocated:.2f}, "
                   f"사용({self.position_size_pct}%): ${use:.2f}, Lev {leverage}x → 수량: {size}")
        return self.format_size(size)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 안전한 진입/청산 (텔레그램 알림 포함)
    # ═══════════════════════════════════════════════════════════════════════════
    
    def safe_limit_entry(self, leverage: int, side: str = 'long') -> bool:
        """[v4.0] 포지션 진입 (롱/숏 지원)"""
        if leverage <= 0:
            return False
        
        self.client.set_leverage(self.symbol, leverage, self.product_type, self.margin_coin, side)
        
        ticker = self.signal_client.get_ticker(self.symbol)
        if not ticker:
            logger.error(f"[{self.symbol}] 현재가 조회 실패 (Binance)")
            send_error_alert(self.symbol, "현재가 조회 실패 (Binance)")
            return False
        
        price = float(ticker.get('price', 0))
        if price <= 0:
            return False
        
        # [v3.4] allocation_pct 반영한 포지션 크기 계산
        target_size = self.calculate_position_size(price, leverage)
        if target_size == "0":
            logger.error(f"[{self.symbol}] 포지션 크기 계산 실패 또는 자금 부족")
            return False
        
        target_size_float = float(target_size)
        remaining_size = target_size_float
        total_filled = 0.0
        
        # 롱: buy, 숏: sell
        order_side = 'buy' if side == 'long' else 'sell'
        side_label = 'Long' if side == 'long' else 'Short'
        
        if DRY_RUN:
            logger.info(f"[{self.symbol}] [DRY RUN] {side_label} 진입: {target_size}")
            return True
        
        entry_price_for_alert = price
        order_type_for_alert = "지정가"
        
        for retry in range(1, MAX_LIMIT_RETRY + 1):
            if remaining_size <= 0:
                break
            
            self.client.cancel_all_orders(self.symbol, self.product_type, self.margin_coin)
            time.sleep(0.2)
            
            ticker = self.client.get_ticker(self.symbol, self.product_type)
            if not ticker:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            
            price = float(ticker.get('lastPr', 0))
            # 롱: 약간 높게, 숏: 약간 낮게
            if side == 'long':
                entry_price = self.round_price(price + self.tick_size * LIMIT_ORDER_TICKS)
            else:
                entry_price = self.round_price(price - self.tick_size * LIMIT_ORDER_TICKS)
            entry_price_for_alert = entry_price
            
            remaining_str = self.format_size(remaining_size)
            logger.info(f"[{self.symbol}] 📤 지정가 {side_label} #{retry}: {remaining_str} @ {self.format_price(entry_price)}")
            
            result = self.client.place_limit_order(
                self.symbol, order_side, remaining_str, self.format_price(entry_price),
                'open', side, self.product_type, self.margin_coin
            )
            
            if not result:
                logger.warning(f"[{self.symbol}] 주문 실패, 재시도...")
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            
            order_id = result.get('orderId')
            if not order_id:
                logger.warning(f"[{self.symbol}] 주문 ID 없음, 재시도...")
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            
            status, filled = self.wait_for_fill(order_id)
            total_filled += filled
            remaining_size = target_size_float - total_filled
            
            if status == 'filled':
                logger.info(f"[{self.symbol}] ✅ {side_label} 진입 완료: {self.format_size(total_filled)}")
                send_entry_alert(self.symbol, side_label, self.format_size(total_filled), 
                               entry_price_for_alert, leverage, order_type_for_alert)
                return True
            elif status == 'partially_filled':
                logger.info(f"[{self.symbol}] ⏳ 부분 체결: {self.format_size(filled)}, 잔여: {self.format_size(remaining_size)}")
                self.client.cancel_order(self.symbol, order_id, self.product_type)
            else:
                logger.info(f"[{self.symbol}] ⏳ 미체결 ({status}), 재시도...")
                self.client.cancel_order(self.symbol, order_id, self.product_type)
            
            time.sleep(RETRY_DELAY_SECONDS)
        
        if remaining_size > 0:
            min_sizes = {'BTCUSDT': 0.001, 'ETHUSDT': 0.01, 'SOLUSDT': 0.1, 'SUIUSDT': 0.1}
            min_size = min_sizes.get(self.symbol, 0.001)
            
            if remaining_size >= min_size:
                logger.warning(f"[{self.symbol}] ⚠️ 지정가 {MAX_LIMIT_RETRY}회 실패, 시장가 전환: {self.format_size(remaining_size)}")
                self.client.cancel_all_orders(self.symbol, self.product_type, self.margin_coin)
                
                result = self.client.place_market_order(
                    self.symbol, order_side, self.format_size(remaining_size),
                    'open', side, self.product_type, self.margin_coin
                )
                
                if result:
                    logger.info(f"[{self.symbol}] ✅ 시장가 진입 완료")
                    order_type_for_alert = "시장가"
                    send_entry_alert(self.symbol, side_label, self.format_size(target_size_float), 
                                   entry_price_for_alert, leverage, order_type_for_alert)
                    return True
                else:
                    logger.error(f"[{self.symbol}] ❌ 시장가 진입 실패")
                    send_error_alert(self.symbol, f"{side_label} 시장가 진입 실패")
                    if total_filled > 0:
                        send_entry_alert(self.symbol, side_label, self.format_size(total_filled), 
                                       entry_price_for_alert, leverage, "부분체결")
                    return total_filled > 0
            else:
                logger.info(f"[{self.symbol}] ✅ 잔여 물량 미달, 진입 완료: {self.format_size(total_filled)}")
                send_entry_alert(self.symbol, side_label, self.format_size(total_filled), 
                               entry_price_for_alert, leverage, order_type_for_alert)
                return True
        
        return True
    
    def safe_limit_close(self, side: str = 'long', reason: str = "") -> bool:
        """[v4.0] 포지션 청산 (롱/숏 지원)"""
        # 포지션 조회 재시도 (Rate Limit 대비)
        pos = None
        for attempt in range(3):
            all_pos = self.get_current_position()
            pos = all_pos.get(side)
            if pos and pos['size'] > 0:
                break
            elif pos is None and attempt < 2:
                logger.warning(f"[{self.symbol}] 포지션 조회 재시도 ({attempt + 1}/3)...")
                time.sleep(2)
            else:
                break
        
        if not pos or pos['size'] <= 0:
            logger.info(f"[{self.symbol}] 청산할 {side} 포지션 없음")
            return True
        
        entry_price = pos.get('avg_price', 0)
        position_size = pos['size']
        unrealized_pnl = pos.get('unrealized_pnl', 0)
        
        # 롱 청산: sell, 숏 청산: buy
        order_side = 'sell' if side == 'long' else 'buy'
        side_label = 'Long' if side == 'long' else 'Short'
        
        reason_str = f" ({reason})" if reason else ""
        
        if DRY_RUN:
            logger.info(f"[{self.symbol}] [DRY RUN] {side_label} 청산{reason_str}")
            return True
        
        for retry in range(1, MAX_LIMIT_RETRY + 1):
            # 현재 포지션 확인 (재시도 포함)
            for attempt in range(3):
                all_pos = self.get_current_position()
                pos = all_pos.get(side)
                if pos is not None:
                    break
                time.sleep(1)
            
            if not pos or pos['size'] <= 0:
                logger.info(f"[{self.symbol}] ✅ {side_label} 청산 완료{reason_str}")
                ticker = self.client.get_ticker(self.symbol, self.product_type)
                exit_price = float(ticker.get('lastPr', 0)) if ticker else entry_price
                send_close_alert(self.symbol, position_size, entry_price, exit_price, unrealized_pnl, reason)
                return True
            
            remaining = pos['size']
            
            self.client.cancel_all_orders(self.symbol, self.product_type, self.margin_coin)
            time.sleep(0.2)
            
            ticker = self.client.get_ticker(self.symbol, self.product_type)
            if not ticker:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            
            price = float(ticker.get('lastPr', 0))
            # 롱 청산: 약간 낮게, 숏 청산: 약간 높게
            if side == 'long':
                exit_price = self.round_price(price - self.tick_size * LIMIT_ORDER_TICKS)
            else:
                exit_price = self.round_price(price + self.tick_size * LIMIT_ORDER_TICKS)
            
            remaining_str = self.format_size(remaining)
            logger.info(f"[{self.symbol}] 📤 지정가 {side_label} 청산 #{retry}{reason_str}: {remaining_str} @ {self.format_price(exit_price)}")
            
            result = self.client.place_limit_order(
                self.symbol, order_side, remaining_str, self.format_price(exit_price),
                'close', side, self.product_type, self.margin_coin
            )
            
            if not result:
                logger.warning(f"[{self.symbol}] 지정가 청산 실패, 재시도...")
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            
            order_id = result.get('orderId')
            if not order_id:
                logger.warning(f"[{self.symbol}] 주문 ID 없음, 재시도...")
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            
            status, filled = self.wait_for_fill(order_id)
            
            if status == 'filled':
                logger.info(f"[{self.symbol}] ✅ {side_label} 청산 완료{reason_str}")
                send_close_alert(self.symbol, position_size, entry_price, exit_price, unrealized_pnl, reason)
                return True
            elif status == 'partially_filled':
                logger.info(f"[{self.symbol}] ⏳ 부분 청산: {self.format_size(filled)}")
                self.client.cancel_order(self.symbol, order_id, self.product_type)
            else:
                logger.info(f"[{self.symbol}] ⏳ 미체결 ({status}), 재시도...")
                self.client.cancel_order(self.symbol, order_id, self.product_type)
            
            time.sleep(RETRY_DELAY_SECONDS)
        
        logger.warning(f"[{self.symbol}] ⚠️ 지정가 {MAX_LIMIT_RETRY}회 실패, 플래시 청산...")
        self.client.cancel_all_orders(self.symbol, self.product_type, self.margin_coin)
        
        result = self.client.flash_close_position(self.symbol, self.product_type, side)
        if result:
            logger.info(f"[{self.symbol}] ✅ 플래시 청산 완료{reason_str}")
            ticker = self.client.get_ticker(self.symbol, self.product_type)
            exit_price = float(ticker.get('lastPr', 0)) if ticker else entry_price
            send_close_alert(self.symbol, position_size, entry_price, exit_price, unrealized_pnl, reason + " (플래시)")
            return True
        
        logger.error(f"[{self.symbol}] ❌ 청산 최종 실패")
        send_error_alert(self.symbol, f"{side_label} 청산 최종 실패")
        return False
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 전략 로직 (Binance 데이터 사용)
    # ═══════════════════════════════════════════════════════════════════════════
    
    def calculate_ma(self, df: pd.DataFrame) -> pd.Series:
        if self.ma_type == 'EMA':
            return df['close'].ewm(span=self.ma_period, adjust=False).mean()
        return df['close'].rolling(window=self.ma_period).mean()
    
    def calculate_stochastic(self, df: pd.DataFrame) -> tuple:
        if df.empty:
            return pd.Series(), pd.Series()
        low_min = df['low'].rolling(window=self.stoch_k_period).min()
        high_max = df['high'].rolling(window=self.stoch_k_period).max()
        fast_k = ((df['close'] - low_min) / (high_max - low_min)) * 100
        fast_k = fast_k.replace([np.inf, -np.inf], np.nan)
        slow_k = fast_k.rolling(window=self.stoch_k_smooth).mean()
        slow_d = slow_k.rolling(window=self.stoch_d_period).mean()
        return slow_k, slow_d
    
    def get_stochastic_signal(self) -> tuple:
        """
        [v3.5] 스토캐스틱 신호 (UTC 날짜 기준 캐싱)
        
        - iloc[-1] 사용: 현재 진행 중인 일봉의 스토캐스틱 값
        - UTC 날짜 기준 캐싱: 같은 UTC 날짜 내에서는 캐시된 값 사용
        - UTC 00:00 = 한국시간 09:00
        
        동작:
        - 09:00 KST에 처음 호출 시 → 현재 데이터로 계산 후 캐싱
        - 이후 다음날 09:00 KST까지 → 캐시된 값 사용 (API 호출 없음)
        - 다음날 09:00 KST 이후 → 새로 계산 후 캐시 갱신
        """
        # 현재 UTC 날짜 확인
        now_utc = datetime.now(timezone.utc)
        current_utc_date = now_utc.strftime('%Y-%m-%d')
        
        # 캐시 확인: 같은 UTC 날짜면 캐시된 값 반환
        if self._stoch_cache['utc_date'] == current_utc_date:
            logger.debug(f"[{self.symbol}] 📦 스토캐스틱 캐시 사용 (UTC {current_utc_date})")
            return (
                self._stoch_cache['is_bull'],
                self._stoch_cache['k'],
                self._stoch_cache['d']
            )
        
        # 새로운 UTC 날짜 → 현재 데이터로 계산
        required = self.stoch_k_period + self.stoch_k_smooth + self.stoch_d_period + 50
        df = self.signal_client.get_candles_pagination(self.symbol, '1D', required)
        
        if df.empty:
            logger.warning(f"[{self.symbol}] Binance 1D 캔들 조회 실패")
            return False, 0, 0
        
        slow_k, slow_d = self.calculate_stochastic(df)
        valid_k = slow_k.dropna()
        valid_d = slow_d.dropna()
        
        if len(valid_k) < 1 or len(valid_d) < 1:
            logger.warning(f"[{self.symbol}] 스토캐스틱 데이터 부족")
            return False, 0, 0
        
        # [v3.5] iloc[-1] 사용: 현재 일봉의 스토캐스틱 값
        k = valid_k.iloc[-1]
        d = valid_d.iloc[-1]
        
        if pd.isna(k) or pd.isna(d):
            return False, 0, 0
        
        is_bull = k > d
        
        # 캐시 업데이트: 이 값이 다음날 09:00 KST까지 유지됨
        self._stoch_cache = {
            'utc_date': current_utc_date,
            'is_bull': is_bull,
            'k': float(k),
            'd': float(d)
        }
        
        kst_time = (now_utc + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M')
        logger.info(f"[{self.symbol}] 📊 스토캐스틱 캐시 갱신 (UTC {current_utc_date}, KST {kst_time})")
        logger.info(f"[{self.symbol}]    K={k:.2f}, D={d:.2f} → {'상승장' if is_bull else '하락장'}")
        
        return is_bull, float(k), float(d)
    
    def get_stochastic_signal_short(self) -> tuple:
        """
        [v4.0] 숏용 스토캐스틱 신호 (별도 파라미터 사용)
        숏 조건: K < D (하락장)
        """
        if not self.short_enabled:
            return False, 0, 0
        
        now_utc = datetime.now(timezone.utc)
        current_utc_date = now_utc.strftime('%Y-%m-%d')
        
        # 캐시 확인
        if self._stoch_cache_short['utc_date'] == current_utc_date:
            return (
                self._stoch_cache_short['is_bear'],
                self._stoch_cache_short['k'],
                self._stoch_cache_short['d']
            )
        
        # 숏 전용 파라미터로 계산
        required = self.short_stoch_k_period + self.short_stoch_k_smooth + self.short_stoch_d_period + 50
        df = self.signal_client.get_candles_pagination(self.symbol, '1D', required)
        
        if df.empty:
            return False, 0, 0
        
        # 숏 전용 스토캐스틱 계산
        low_min = df['low'].rolling(window=self.short_stoch_k_period).min()
        high_max = df['high'].rolling(window=self.short_stoch_k_period).max()
        fast_k = ((df['close'] - low_min) / (high_max - low_min)) * 100
        fast_k = fast_k.replace([np.inf, -np.inf], np.nan)
        slow_k = fast_k.rolling(window=self.short_stoch_k_smooth).mean()
        slow_d = slow_k.rolling(window=self.short_stoch_d_period).mean()
        
        valid_k = slow_k.dropna()
        valid_d = slow_d.dropna()
        
        if len(valid_k) < 1 or len(valid_d) < 1:
            return False, 0, 0
        
        k = valid_k.iloc[-1]
        d = valid_d.iloc[-1]
        
        if pd.isna(k) or pd.isna(d):
            return False, 0, 0
        
        is_bear = k < d  # 숏은 K < D
        
        self._stoch_cache_short = {
            'utc_date': current_utc_date,
            'is_bear': is_bear,
            'k': float(k),
            'd': float(d)
        }
        
        logger.info(f"[{self.symbol}] 📊 숏 스토캐스틱({self.short_stoch_k_period},{self.short_stoch_k_smooth},{self.short_stoch_d_period}): K={k:.2f}, D={d:.2f} → {'하락장' if is_bear else '상승장'}")
        
        return is_bear, float(k), float(d)
    
    def get_target_leverage(self) -> int:
        is_bullish, k, d = self.get_stochastic_signal()
        if is_bullish:
            target = self.leverage_up
            state = "상승장 📈"
        else:
            target = self.leverage_down
            state = "하락장 📉"
        lev_desc = f"{target}x" if target > 0 else "현금"
        logger.info(f"[{self.symbol}] 🎯 Stoch(1D/Binance): K={k:.2f}, D={d:.2f} → {state} ({lev_desc})")
        return target
    
    def get_ma_signal(self) -> Optional[int]:
        df = self.signal_client.get_candles(self.symbol, self.timeframe, self.ma_period + 10)
        if df is None or df.empty or len(df) < self.ma_period:
            logger.warning(f"[{self.symbol}] Binance {self.timeframe} 캔들 조회 실패")
            return None
        df['ma'] = self.calculate_ma(df)
        open_price = df.iloc[-1]['open']
        ma = df.iloc[-1]['ma']
        if pd.isna(ma):
            return None
        signal = 1 if open_price > ma else 0
        logger.info(f"[{self.symbol}] 📊 시가(Binance): {open_price:.2f}, MA{self.ma_period}: {ma:.2f} → {'LONG' if signal else 'CASH'}")
        return signal
    
    def get_final_action(self) -> tuple:
        """
        [v4.0] 최종 액션 결정 (롱 우선, 숏 차선)
        Returns: (action, leverage, side)
            action: 'LONG', 'SHORT', 'CASH', 'HOLD'
            leverage: 목표 레버리지
            side: 'long', 'short', None
        """
        # ═══════════════════════════════════════════════════════════════
        # 1. 롱 조건 확인: 가격 > MA AND K > D
        # ═══════════════════════════════════════════════════════════════
        df = self.signal_client.get_candles(self.symbol, self.timeframe, self.ma_period + 10)
        if df is None or df.empty or len(df) < self.ma_period:
            logger.warning(f"[{self.symbol}] Binance {self.timeframe} 캔들 조회 실패")
            return ('HOLD', 0, None)
        
        df['ma'] = self.calculate_ma(df)
        open_price = df.iloc[-1]['open']
        ma_long = df.iloc[-1]['ma']
        
        if pd.isna(ma_long):
            return ('HOLD', 0, None)
        
        ma_long_signal = open_price > ma_long  # 가격 > MA
        is_bull, k_long, d_long = self.get_stochastic_signal()
        
        logger.info(f"[{self.symbol}] 📊 롱 조건: 시가 {open_price:.2f} {'>' if ma_long_signal else '<='} MA{self.ma_period} {ma_long:.2f}, K={k_long:.2f} {'>' if is_bull else '<='} D={d_long:.2f}")
        
        # 롱 조건 충족: 가격 > MA AND K > D
        if ma_long_signal and is_bull:
            target_lev = self.leverage_up
            if target_lev > 0:
                logger.info(f"[{self.symbol}] ✅ 롱 조건 충족 → Long {target_lev}x")
                return ('LONG', target_lev, 'long')
        
        # ═══════════════════════════════════════════════════════════════
        # 2. 숏 조건 확인: 가격 < MA AND K < D (롱 미충족 시)
        # ═══════════════════════════════════════════════════════════════
        if self.short_enabled:
            # 숏 전용 MA 계산
            df_short = self.signal_client.get_candles(self.symbol, self.timeframe, self.short_ma_period + 10)
            if df_short is not None and not df_short.empty and len(df_short) >= self.short_ma_period:
                if self.ma_type == 'EMA':
                    ma_short = df_short['close'].ewm(span=self.short_ma_period, adjust=False).mean().iloc[-1]
                else:
                    ma_short = df_short['close'].rolling(window=self.short_ma_period).mean().iloc[-1]
                
                open_price_short = df_short.iloc[-1]['open']
                
                if not pd.isna(ma_short):
                    ma_short_signal = open_price_short < ma_short  # 가격 < MA
                    is_bear, k_short, d_short = self.get_stochastic_signal_short()
                    
                    logger.info(f"[{self.symbol}] 📊 숏 조건: 시가 {open_price_short:.2f} {'<' if ma_short_signal else '>='} MA{self.short_ma_period} {ma_short:.2f}, K={k_short:.2f} {'<' if is_bear else '>='} D={d_short:.2f}")
                    
                    # 숏 조건 충족: 가격 < MA AND K < D
                    if ma_short_signal and is_bear:
                        logger.info(f"[{self.symbol}] ✅ 숏 조건 충족 → Short {self.short_leverage}x")
                        return ('SHORT', self.short_leverage, 'short')
        
        # ═══════════════════════════════════════════════════════════════
        # 3. 둘 다 미충족 → 현금
        # ═══════════════════════════════════════════════════════════════
        logger.info(f"[{self.symbol}] ❌ 롱/숏 조건 모두 미충족 → 현금")
        return ('CASH', 0, None)
    
    def adjust_leverage(self, target: int) -> bool:
        if target <= 0:
            return self.safe_limit_close(reason="레버리지→현금")
        pos = self.get_current_position()
        if pos['side'] != 'long' or pos['size'] <= 0:
            return True
        if pos.get('leverage', 0) == target:
            return True
        
        old_lev = pos.get('leverage', 0)
        logger.info(f"[{self.symbol}] 🔄 레버리지 변경: {old_lev}x → {target}x")
        
        send_leverage_change_alert(self.symbol, old_lev, target)
        
        if not self.safe_limit_close(reason="레버리지 변경"):
            return False
        time.sleep(1)
        return self.safe_limit_entry(target)
    
    def show_status(self):
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 [{self.symbol}] 현재 상태 (신호: Binance, 매매: Bitget)")
        logger.info(f"   배분 비율: {self.allocation_pct:.0f}%")
        logger.info(f"{'='*60}")
        
        binance_ticker = self.signal_client.get_ticker(self.symbol)
        bitget_ticker = self.client.get_ticker(self.symbol, self.product_type)
        binance_price = float(binance_ticker.get('price', 0)) if binance_ticker else 0
        bitget_price = float(bitget_ticker.get('lastPr', 0)) if bitget_ticker else 0
        diff_pct = ((bitget_price - binance_price) / binance_price * 100) if binance_price > 0 else 0
        logger.info(f"💵 현재가 - Binance: ${binance_price:,.2f} | Bitget: ${bitget_price:,.2f} (차이: {diff_pct:+.3f}%)")
        
        df = self.signal_client.get_candles(self.symbol, self.timeframe, self.ma_period + 10)
        if df is not None and not df.empty and len(df) >= self.ma_period:
            df['ma'] = self.calculate_ma(df)
            ma = df.iloc[-1]['ma']
            open_p = df.iloc[-1]['open']
            sig = "🟢 LONG" if open_p > ma else "🔴"
            logger.info(f"📈 롱 MA{self.ma_period} (Binance): ${ma:,.2f}, 시가: ${open_p:,.2f} → {sig}")
        
        is_bull, k, d = self.get_stochastic_signal()
        stoch_sig = f"🟢 K>D" if is_bull else f"🔴 K<=D"
        cache_date = self._stoch_cache.get('utc_date', 'N/A')
        logger.info(f"📉 롱 Stoch({self.stoch_k_period},{self.stoch_k_smooth},{self.stoch_d_period}): K={k:.2f}, D={d:.2f} → {stoch_sig}")
        
        # 숏 지표 표시
        if self.short_enabled:
            df_short = self.signal_client.get_candles(self.symbol, self.timeframe, self.short_ma_period + 10)
            if df_short is not None and not df_short.empty and len(df_short) >= self.short_ma_period:
                if self.ma_type == 'EMA':
                    ma_short = df_short['close'].ewm(span=self.short_ma_period, adjust=False).mean().iloc[-1]
                else:
                    ma_short = df_short['close'].rolling(window=self.short_ma_period).mean().iloc[-1]
                open_p_short = df_short.iloc[-1]['open']
                sig_short = "🔴 SHORT" if open_p_short < ma_short else "🟢"
                logger.info(f"📈 숏 MA{self.short_ma_period} (Binance): ${ma_short:,.2f}, 시가: ${open_p_short:,.2f} → {sig_short}")
            
            is_bear, k_short, d_short = self.get_stochastic_signal_short()
            stoch_sig_short = f"🔴 K<D" if is_bear else f"🟢 K>=D"
            logger.info(f"📉 숏 Stoch({self.short_stoch_k_period},{self.short_stoch_k_smooth},{self.short_stoch_d_period}): K={k_short:.2f}, D={d_short:.2f} → {stoch_sig_short}")
        
        pos = self.get_current_position()
        if pos['long']:
            p = pos['long']
            logger.info(f"📍 롱 포지션 (Bitget): {p['size']} @ {p['leverage']}x, ${p.get('avg_price',0):,.2f}, PnL: {p['unrealized_pnl']:+,.2f}")
        if pos['short']:
            p = pos['short']
            logger.info(f"📍 숏 포지션 (Bitget): {p['size']} @ {p['leverage']}x, ${p.get('avg_price',0):,.2f}, PnL: {p['unrealized_pnl']:+,.2f}")
        if not pos['long'] and not pos['short']:
            logger.info(f"📍 포지션 (Bitget): 없음 (현금)")
        logger.info(f"{'='*60}\n")
    
    def execute(self):
        """[v4.0] 롱/숏 통합 실행"""
        logger.info(f"\n{'─'*60}")
        logger.info(f"[{self.symbol}] {self.description} (배분: {self.allocation_pct:.0f}%)")
        logger.info(f"{'─'*60}")
        
        action, target_lev, target_side = self.get_final_action()
        pos = self.get_current_position()
        
        has_long = pos['long'] is not None and pos['long']['size'] > 0
        has_short = pos['short'] is not None and pos['short']['size'] > 0
        
        # 현재 포지션 로깅
        if has_long:
            p = pos['long']
            logger.info(f"[{self.symbol}] 📍 현재: Long {p['size']} @ {p['leverage']}x, PnL: {p['unrealized_pnl']:+.2f}")
        if has_short:
            p = pos['short']
            logger.info(f"[{self.symbol}] 📍 현재: Short {p['size']} @ {p['leverage']}x, PnL: {p['unrealized_pnl']:+.2f}")
        if not has_long and not has_short:
            logger.info(f"[{self.symbol}] 📍 현재: 현금")
        
        # 목표 로깅
        lev_desc = f"{target_lev}x" if target_lev > 0 else "현금"
        side_desc = target_side.upper() if target_side else "N/A"
        logger.info(f"[{self.symbol}] 🎯 목표: {action} ({side_desc} {lev_desc})")
        
        # ═══════════════════════════════════════════════════════════════
        # 액션 실행 (롱 우선)
        # ═══════════════════════════════════════════════════════════════
        
        if action == 'LONG':
            # 숏 포지션 있으면 먼저 청산
            if has_short:
                logger.info(f"[{self.symbol}] 📉 숏 청산 (롱 전환)")
                self.safe_limit_close(side='short', reason="롱 전환")
                time.sleep(1)
            
            if not has_long:
                logger.info(f"[{self.symbol}] 📈 Long 진입 (Lev {target_lev}x)")
                self.safe_limit_entry(target_lev, side='long')
            else:
                curr_lev = pos['long']['leverage']
                if curr_lev != target_lev:
                    # 레버리지 변경
                    logger.info(f"[{self.symbol}] 🔄 롱 레버리지 변경: {curr_lev}x → {target_lev}x")
                    send_leverage_change_alert(self.symbol, curr_lev, target_lev)
                    self.safe_limit_close(side='long', reason="레버리지 변경")
                    time.sleep(1)
                    self.safe_limit_entry(target_lev, side='long')
                else:
                    logger.info(f"[{self.symbol}] ➡️ Long 유지")
                    add_hold_position(self.symbol, pos['long']['size'], curr_lev, 
                                    pos['long'].get('unrealized_pnl', 0), side='long')
        
        elif action == 'SHORT':
            # 롱 포지션 있으면 먼저 청산
            if has_long:
                logger.info(f"[{self.symbol}] 📈 롱 청산 (숏 전환)")
                self.safe_limit_close(side='long', reason="숏 전환")
                time.sleep(1)
            
            if not has_short:
                logger.info(f"[{self.symbol}] 📉 Short 진입 (Lev {target_lev}x)")
                self.safe_limit_entry(target_lev, side='short')
            else:
                curr_lev = pos['short']['leverage']
                if curr_lev != target_lev:
                    # 레버리지 변경
                    logger.info(f"[{self.symbol}] 🔄 숏 레버리지 변경: {curr_lev}x → {target_lev}x")
                    send_leverage_change_alert(self.symbol, curr_lev, target_lev)
                    self.safe_limit_close(side='short', reason="레버리지 변경")
                    time.sleep(1)
                    self.safe_limit_entry(target_lev, side='short')
                else:
                    logger.info(f"[{self.symbol}] ➡️ Short 유지")
                    add_hold_position(self.symbol, pos['short']['size'], curr_lev,
                                    pos['short'].get('unrealized_pnl', 0), side='short')
        
        elif action == 'CASH':
            if has_long:
                logger.info(f"[{self.symbol}] 📉 롱 청산 (현금 전환)")
                self.safe_limit_close(side='long', reason="조건 미충족")
            if has_short:
                logger.info(f"[{self.symbol}] 📈 숏 청산 (현금 전환)")
                self.safe_limit_close(side='short', reason="조건 미충족")
            if not has_long and not has_short:
                logger.info(f"[{self.symbol}] ➡️ 현금 유지")


def get_candle_start_time(dt: datetime, timeframe: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    tf_min = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1H': 60, '4H': 240, '1D': 1440}
    minutes = tf_min.get(timeframe, 240)
    if minutes < 1440:
        total = dt.hour * 60 + dt.minute
        start = (total // minutes) * minutes
        return dt.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def get_next_candle_time(start: datetime, timeframe: str) -> datetime:
    tf_min = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1H': 60, '4H': 240, '1D': 1440}
    return start + timedelta(minutes=tf_min.get(timeframe, 240))


def load_api_credentials() -> tuple:
    # 환경변수에서 로드된 전역 변수 사용
    key = API_KEY
    secret = API_SECRET
    pw = API_PASSPHRASE
    
    if not all([key, secret, pw]):
        logger.error("❌ Bitget API 키 미설정. .env 파일을 확인하세요.")
    
    return key, secret, pw


def print_config():
    print("\n" + "="*70)
    print("📊 Bitget 자동매매 봇 v4.0 (롱/숏 통합) + 텔레그램")
    print("   [v4.0] 롱 조건: 가격>MA AND K>D → 롱 진입")
    print("   [v4.0] 숏 조건: 가격<MA AND K<D → 숏 진입 (롱 미충족 시)")
    print("   [v4.0] 충돌 시 롱 우선")
    print("="*70)
    print(f"🔧 모드: {'🔵 DRY RUN' if DRY_RUN else '🔴 LIVE'}")
    print(f"📡 신호 데이터: Binance Futures 공개 API")
    print(f"💹 매매 실행: Bitget Futures API")
    print(f"📲 알림: 텔레그램")
    print(f"📋 지정가 최대 재시도: {MAX_LIMIT_RETRY}회 (초과 시 시장가)")
    print(f"\n📈 롱 전략 (allocation_pct 적용):")
    total_alloc = 0
    for c in TRADING_CONFIGS:
        if c['enabled']:
            stoch = f"({c['stoch_k_period']},{c['stoch_k_smooth']},{c['stoch_d_period']})"
            e = "현금" if c['leverage_down'] == 0 else f"{c['leverage_down']}x"
            alloc = c.get('allocation_pct', 0)
            total_alloc += alloc
            print(f"   {c['symbol']}: {alloc:.0f}% | MA{c['ma_period']} + Stoch{stoch}, Lev {c['leverage_up']}x/{e}")
    print(f"\n   총 배분: {total_alloc:.0f}%")
    
    print(f"\n📉 숏 전략 (롱 조건 미충족 시):")
    for c in SHORT_TRADING_CONFIGS:
        if c['enabled']:
            stoch = f"({c['stoch_k_period']},{c['stoch_k_smooth']},{c['stoch_d_period']})"
            print(f"   {c['symbol']}: MA{c['ma_period']} + Stoch{stoch}, Lev {c['leverage']}x")
    print("="*70)


def main():
    global BOT_START_TIME
    
    # 봇 시작 시간 기록
    BOT_START_TIME = datetime.now()
    
    # 종료 핸들러 설정
    setup_shutdown_handlers()
    
    print_config()
    
    key, secret, pw = load_api_credentials()
    if not all([key, secret, pw]):
        return
    
    bitget_client = BitgetClient(key, secret, pw)
    binance_client = BinancePublicClient()
    
    # 초기 연결 테스트 (실패해도 계속 진행)
    logger.info(f"📡 Binance 공개 API 연결 테스트...")
    test_ticker = binance_client.get_ticker('BTCUSDT')
    if test_ticker:
        logger.info(f"✅ Binance 연결 성공 - BTC: ${float(test_ticker['price']):,.2f}")
    else:
        logger.warning("⚠️ Binance 연결 실패 - 서버 점검 가능성. 다음 스케줄에 재시도합니다.")
        send_telegram("⚠️ <b>Binance API 연결 실패</b>\n서버 점검 가능성이 있습니다.\n다음 스케줄 시간에 자동 재시도합니다.")
    
    # Bitget 포지션 모드 확인 (실패해도 계속 진행)
    try:
        pos_mode = bitget_client.get_position_mode()
        logger.info(f"🔧 Bitget 계좌 포지션 모드: {pos_mode}")
    except Exception as e:
        logger.warning(f"⚠️ Bitget 포지션 모드 조회 실패: {e}")
        pos_mode = "unknown"
    
    portfolio = PortfolioManager(bitget_client, TRADING_CONFIGS)
    bots = [TradingBot(bitget_client, binance_client, c, portfolio) for c in TRADING_CONFIGS if c['enabled']]
    if not bots:
        logger.error("❌ 활성 전략 없음")
        return
    logger.info(f"\n🚀 봇 시작 ({len(bots)}개 티커)")
    
    # 텔레그램 시작 알림
    enabled_configs = [c for c in TRADING_CONFIGS if c['enabled']]
    try:
        total_equity = portfolio.get_total_equity()
    except:
        total_equity = 0
    send_bot_start_alert(enabled_configs, total_equity)
    
    logger.info(f"\n{'='*70}")
    logger.info(f"🔥 실행 즉시 거래 (1회)")
    logger.info(f"{'='*70}")
    
    # API 연결 확인 후 실행
    try:
        portfolio.log_portfolio_status()
    except Exception as e:
        logger.warning(f"⚠️ 포트폴리오 조회 실패: {e}")
    
    # 거래 결과 초기화
    clear_trade_results()
    
    # API 연결 상태 확인
    test_ticker = binance_client.get_ticker('BTCUSDT')
    if test_ticker is None:
        logger.warning("⚠️ API 조회 실패 (서버 점검 가능성). 이번 사이클을 건너뜁니다.")
        send_telegram("⚠️ <b>API 조회 실패</b>\n서버 점검 가능성이 있습니다.\n다음 스케줄 시간에 자동 재시도합니다.")
    else:
        for i, bot in enumerate(bots):
            try:
                if i > 0:
                    time.sleep(SYMBOL_DELAY_SECONDS)  # Rate Limit 방지
                bot.show_status()
                bot.execute()
            except Exception as e:
                logger.error(f"[{bot.symbol}] 실행 오류: {e}")
                send_error_alert(bot.symbol, str(e))
                import traceback
                traceback.print_exc()
        
        # 시작 시 종합 메시지 전송
        try:
            total_equity = portfolio.get_total_equity()
            available = portfolio.get_available_balance()
            send_trading_summary(total_equity, available)
        except Exception as e:
            logger.warning(f"⚠️ 종합 메시지 전송 실패: {e}")
    
    now = datetime.now(timezone.utc)
    last_executed = {}
    for bot in bots:
        k = f"{bot.symbol}_{bot.timeframe}"
        last_executed[k] = get_candle_start_time(now, bot.timeframe)
    
    candle_start = get_candle_start_time(now, '4H')
    next_candle = get_next_candle_time(candle_start, '4H')
    logger.info(f"\n⏰ 다음 4H 봉: {next_candle.strftime('%Y-%m-%d %H:%M:%S')} UTC ({(next_candle-now).total_seconds()/60:.1f}분)")
    
    try:
        while True:
            now = datetime.now(timezone.utc)
            executed_count = 0
            api_failed = False
            
            # 새 봉 시작 시 거래 결과 초기화
            first_bot_executed = False
            
            for bot in bots:
                try:
                    start = get_candle_start_time(now, bot.timeframe)
                    k = f"{bot.symbol}_{bot.timeframe}"
                    if last_executed.get(k) == start:
                        continue
                    elapsed = (now - start).total_seconds()
                    if 0 <= elapsed <= 300:
                        if elapsed < CANDLE_START_DELAY:
                            time.sleep(CANDLE_START_DELAY - elapsed)
                        if executed_count > 0:
                            time.sleep(SYMBOL_DELAY_SECONDS)  # Rate Limit 방지
                        
                        # 첫 번째 봇 실행 전: 거래 결과 초기화 + API 연결 확인
                        if not first_bot_executed:
                            clear_trade_results()
                            
                            # API 연결 상태 확인
                            test_ticker = binance_client.get_ticker('BTCUSDT')
                            if test_ticker is None:
                                logger.warning("⚠️ API 조회 실패 (서버 점검 가능성). 이번 사이클을 건너뜁니다.")
                                send_telegram("⚠️ <b>API 조회 실패</b>\n서버 점검 가능성이 있습니다.\n다음 스케줄 시간에 자동 재시도합니다.")
                                api_failed = True
                                # last_executed는 업데이트하여 같은 봉에서 재시도 방지
                                for b in bots:
                                    bk = f"{b.symbol}_{b.timeframe}"
                                    last_executed[bk] = start
                                break
                            
                            first_bot_executed = True
                        
                        logger.info(f"\n🕐 {bot.timeframe} 봉: {start}")
                        if bot == bots[0]:
                            try:
                                portfolio.log_portfolio_status()
                            except Exception as e:
                                logger.warning(f"⚠️ 포트폴리오 조회 실패: {e}")
                        bot.execute()
                        last_executed[k] = start
                        executed_count += 1
                except Exception as e:
                    logger.error(f"[{bot.symbol}] 오류: {e}")
                    send_error_alert(bot.symbol, str(e))
                    import traceback
                    traceback.print_exc()
            
            # 모든 봇 실행 후 종합 메시지 전송 (API 실패가 아닌 경우에만)
            if executed_count > 0 and not api_failed:
                try:
                    total_equity = portfolio.get_total_equity()
                    available = portfolio.get_available_balance()
                    send_trading_summary(total_equity, available)
                except Exception as e:
                    logger.warning(f"⚠️ 종합 메시지 전송 실패: {e}")
            
            next_times = [get_next_candle_time(get_candle_start_time(now, b.timeframe), b.timeframe) for b in bots]
            next_run = min(next_times)
            sleep_sec = (next_run - now).total_seconds() + CANDLE_START_DELAY
            if sleep_sec > 0:
                logger.info(f"\n⏰ 다음: {next_run.strftime('%H:%M:%S')} UTC ({sleep_sec/60:.1f}분)")
                while sleep_sec > 0:
                    time.sleep(min(sleep_sec, 300))
                    sleep_sec -= 300
            else:
                time.sleep(RETRY_INTERVAL)
    except KeyboardInterrupt:
        logger.info("\n👋 Ctrl+C로 종료")
        send_shutdown_alert(reason="Ctrl+C")


if __name__ == "__main__":
    main()
