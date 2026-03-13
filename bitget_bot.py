"""
================================================================================
Bitget Futures 메이저 코인 자동매매 봇 v2.0
(Binance 신호 + Bitget 매매) + 텔레그램 알림
================================================================================
- 대상: BTC, ETH, XRP, SOL, DOGE, ADA (6개)
- 신호 데이터: Binance Futures 공개 API (API 키 불필요)
- 매매 실행: Bitget Futures API (헤지 모드)
- 전략: 코인별 우선순위 (롱우선 or 숏우선, 각각 독립 파라미터)
  - 롱 조건: 시가 > MA_long(4H) AND K_long > D_long(1D) → 롱 진입
  - 숏 조건: 시가 < MA_short(4H) AND K_short < D_short(1D) → 숏 진입
  - 충돌 시 코인별 priority에 따라 우선방향 선택
- 자금 배분: 6코인 균등배분 (각 16.7%)
- 지정가 5회 실패 시 시장가 전환
- 텔레그램 실시간 알림
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
# 📌 트레이딩 설정 (백테스트 최적화 결과 v2 - 코인별 롱우선/숏우선)
# ═══════════════════════════════════════════════════════════════════════════════
# 롱/숏 파라미터 독립 최적화 + 코인별 priority 결과
# (optimize_all_coins_long_vs_short.py → compare_old_vs_new_params.py)
# priority='long': 롱 조건 우선 확인, priority='short': 숏 조건 우선 확인

TRADING_CONFIGS = [
    {
        'enabled': True,
        'symbol': 'BTCUSDT',
        'product_type': 'USDT-FUTURES',
        'margin_coin': 'USDT',
        'timeframe': '4H',
        'tick_size': 0.1,
        'size_decimals': 3,
        'priority': 'short',  # 숏우선 (Winner: SF)
        # 롱 파라미터 (보조)
        'long_ma': 350, 'long_sk': 36, 'long_sks': 32, 'long_sd': 10, 'long_lev': 5,
        # 숏 파라미터 (우선)
        'short_ma': 254, 'short_sk': 27, 'short_sks': 23, 'short_sd': 19, 'short_lev': 1,
    },
    {
        'enabled': True,
        'symbol': 'ETHUSDT',
        'product_type': 'USDT-FUTURES',
        'margin_coin': 'USDT',
        'timeframe': '4H',
        'tick_size': 0.01,
        'size_decimals': 2,
        'priority': 'long',  # 롱우선 (Winner: LF)
        # 롱 파라미터 (우선)
        'long_ma': 322, 'long_sk': 54, 'long_sks': 10, 'long_sd': 36, 'long_lev': 5,
        # 숏 파라미터 (보조)
        'short_ma': 220, 'short_sk': 31, 'short_sks': 44, 'short_sd': 26, 'short_lev': 2,
    },
    {
        'enabled': True,
        'symbol': 'XRPUSDT',
        'product_type': 'USDT-FUTURES',
        'margin_coin': 'USDT',
        'timeframe': '4H',
        'tick_size': 0.0001,
        'size_decimals': 1,
        'priority': 'short',  # 숏우선 (Winner: SF)
        # 롱 파라미터 (보조)
        'long_ma': 107, 'long_sk': 14, 'long_sks': 13, 'long_sd': 23, 'long_lev': 5,
        # 숏 파라미터 (우선)
        'short_ma': 269, 'short_sk': 121, 'short_sks': 35, 'short_sd': 47, 'short_lev': 1,
    },
    {
        'enabled': True,
        'symbol': 'SOLUSDT',
        'product_type': 'USDT-FUTURES',
        'margin_coin': 'USDT',
        'timeframe': '4H',
        'tick_size': 0.001,
        'size_decimals': 1,
        'priority': 'long',  # 롱우선 (Winner: LF)
        # 롱 파라미터 (우선)
        'long_ma': 73, 'long_sk': 33, 'long_sks': 16, 'long_sd': 38, 'long_lev': 4,
        # 숏 파라미터 (보조)
        'short_ma': 314, 'short_sk': 37, 'short_sks': 34, 'short_sd': 44, 'short_lev': 1,
    },
    {
        'enabled': True,
        'symbol': 'DOGEUSDT',
        'product_type': 'USDT-FUTURES',
        'margin_coin': 'USDT',
        'timeframe': '4H',
        'tick_size': 0.00001,
        'size_decimals': 0,
        'priority': 'short',  # 숏우선 (Winner: SF)
        # 롱 파라미터 (보조)
        'long_ma': 31, 'long_sk': 48, 'long_sks': 50, 'long_sd': 17, 'long_lev': 2,
        # 숏 파라미터 (우선)
        'short_ma': 250, 'short_sk': 36, 'short_sks': 15, 'short_sd': 40, 'short_lev': 1,
    },
    {
        'enabled': True,
        'symbol': 'ADAUSDT',
        'product_type': 'USDT-FUTURES',
        'margin_coin': 'USDT',
        'timeframe': '4H',
        'tick_size': 0.0001,
        'size_decimals': 0,
        'priority': 'short',  # 숏우선 (Winner: SF)
        # 롱 파라미터 (보조)
        'long_ma': 296, 'long_sk': 19, 'long_sks': 53, 'long_sd': 15, 'long_lev': 3,
        # 숏 파라미터 (우선)
        'short_ma': 80, 'short_sk': 31, 'short_sks': 77, 'short_sd': 46, 'short_lev': 1,
    },
]

# 총 코인 수 (균등배분용)
TOTAL_COINS = len([c for c in TRADING_CONFIGS if c['enabled']])

# 주문 설정
LIMIT_ORDER_TICKS = 1
ORDER_WAIT_SECONDS = 5
MAX_LIMIT_RETRY = 5
RETRY_DELAY_SECONDS = 1
SYMBOL_DELAY_SECONDS = 2

# API 설정 (환경변수 사용)
API_KEY = os.getenv("BITGET_ACCESS_KEY")
API_SECRET = os.getenv("BITGET_SECRET_KEY")
API_PASSPHRASE = os.getenv("BITGET_PASSPHRASE")

# 텔레그램 설정
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 일반 설정
BASE_URL = "https://api.bitget.com"
DRY_RUN = False
LOG_LEVEL = logging.INFO
LOG_FILE = "bitget_major_bot.log"
CANDLE_START_DELAY = 10
RETRY_INTERVAL = 60

# 종료 알림 관련
BOT_START_TIME = None
SHUTDOWN_SENT = False

# 거래 결과 수집용
trade_results = {
    'entries': [],
    'closes': [],
    'holds': [],
    'errors': [],
}


# ═══════════════════════════════════════════════════════════════════════════════
# 로깅
# ═══════════════════════════════════════════════════════════════════════════════

def setup_logging():
    logger = logging.getLogger('BitgetMajorBot')
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
# 텔레그램 알림
# ═══════════════════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'HTML'}
        response = requests.post(url, data=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"텔레그램 전송 오류: {e}")
        return False


def send_entry_alert(symbol, side, size, price, leverage, order_type="지정가"):
    global trade_results
    trade_results['entries'].append({
        'symbol': symbol, 'side': side, 'size': size,
        'price': price, 'leverage': leverage, 'order_type': order_type
    })


def send_close_alert(symbol, size, entry_price, exit_price, pnl, reason=""):
    global trade_results
    trade_results['closes'].append({
        'symbol': symbol, 'size': size, 'entry_price': entry_price,
        'exit_price': exit_price, 'pnl': pnl, 'reason': reason
    })


def send_error_alert(symbol, error_message):
    global trade_results
    trade_results['errors'].append({'symbol': symbol, 'error': error_message})


def clear_trade_results():
    global trade_results
    trade_results = {'entries': [], 'closes': [], 'holds': [], 'errors': []}


def add_hold_position(symbol, size, leverage, pnl, side='long'):
    global trade_results
    trade_results['holds'].append({
        'symbol': symbol, 'size': size, 'leverage': leverage, 'pnl': pnl, 'side': side
    })


def send_trading_summary(total_equity: float, available: float):
    """거래 종합 리포트 텔레그램 전송"""
    global trade_results

    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    msg = f"📊 <b>Bitget Major Bot 거래 리포트</b>\n"
    msg += f"⏰ {kst.strftime('%Y-%m-%d %H:%M')} KST\n"
    msg += f"💰 총자산: ${total_equity:,.2f} | 가용: ${available:,.2f}\n"
    msg += f"{'─' * 30}\n"

    # 진입 내역
    if trade_results['entries']:
        msg += f"\n📈 <b>진입 ({len(trade_results['entries'])}건)</b>\n"
        for e in trade_results['entries'][:10]:
            emoji = "🟢" if e['side'] == 'Long' else "🔴"
            msg += f"  {emoji} {e['symbol']} {e['side']} {e['leverage']}x @ ${e['price']:,.2f} ({e['order_type']})\n"

    # 청산 내역
    if trade_results['closes']:
        msg += f"\n📉 <b>청산 ({len(trade_results['closes'])}건)</b>\n"
        for c in trade_results['closes'][:10]:
            pnl_emoji = "💚" if c['pnl'] >= 0 else "❤️"
            msg += f"  {pnl_emoji} {c['symbol']} PnL: ${c['pnl']:+,.2f} ({c['reason']})\n"

    # 유지 내역
    if trade_results['holds']:
        msg += f"\n📍 <b>유지 ({len(trade_results['holds'])}건)</b>\n"
        for h in trade_results['holds'][:10]:
            emoji = "🟢" if h['side'] == 'long' else "🔴"
            pnl_emoji = "💚" if h['pnl'] >= 0 else "❤️"
            msg += f"  {emoji} {h['symbol']} {h['side'].upper()} {h['leverage']}x {pnl_emoji}${h['pnl']:+,.2f}\n"

    # 에러
    if trade_results['errors']:
        msg += f"\n⚠️ <b>에러 ({len(trade_results['errors'])}건)</b>\n"
        for err in trade_results['errors'][:5]:
            msg += f"  ❌ {err['symbol']}: {err['error'][:50]}\n"

    if not any([trade_results['entries'], trade_results['closes'], trade_results['holds']]):
        msg += "\n✅ 변동 없음 (모든 포지션 유지)\n"

    send_telegram(msg)
    clear_trade_results()


def send_bot_start_alert(configs, total_equity):
    msg = f"🚀 <b>Bitget Major Bot v2.0 시작</b>\n"
    msg += f"{'─' * 30}\n"
    msg += f"💰 총자산: ${total_equity:,.2f}\n"
    msg += f"📊 전략: 코인별 롱우선/숏우선 (독립 파라미터)\n"
    msg += f"🪙 코인: {len(configs)}개 균등배분 (각 {100/max(len(configs),1):.1f}%)\n\n"

    for c in configs:
        pri = '롱우선' if c.get('priority', 'long') == 'long' else '숏우선'
        msg += f"  {c['symbol']}: [{pri}] L{c['long_lev']}x/S{c['short_lev']}x "
        msg += f"LMA{c['long_ma']} SMA{c['short_ma']}\n"

    msg += f"\n📡 신호: Binance | 매매: Bitget"
    send_telegram(msg)


def send_shutdown_alert(reason=""):
    global SHUTDOWN_SENT
    if SHUTDOWN_SENT:
        return
    SHUTDOWN_SENT = True

    uptime = ""
    if BOT_START_TIME:
        delta = datetime.now() - BOT_START_TIME
        hours = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        uptime = f"\n⏱ 가동시간: {hours}시간 {mins}분"

    msg = f"🛑 <b>Bitget Major Bot 종료</b>\n"
    msg += f"원인: {reason}{uptime}\n"
    msg += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    send_telegram(msg)


# 종료 핸들러
def signal_handler(signum, frame):
    sig_name = {2: 'SIGINT (Ctrl+C)', 15: 'SIGTERM', 1: 'SIGHUP'}.get(signum, f'Signal {signum}')
    logger.info(f"\n⚠️ 종료 신호: {sig_name}")
    send_shutdown_alert(reason=sig_name)
    sys.exit(0)


def exit_handler():
    send_shutdown_alert(reason="프로그램 종료")


def setup_shutdown_handlers():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try:
        signal.signal(signal.SIGHUP, signal_handler)
    except (AttributeError, OSError):
        pass
    atexit.register(exit_handler)


# ═══════════════════════════════════════════════════════════════════════════════
# Binance 공개 API (신호 데이터)
# ═══════════════════════════════════════════════════════════════════════════════

class BinancePublicClient:
    BASE_URL = "https://fapi.binance.com"
    TIMEFRAME_MAP = {
        '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
        '1H': '1h', '4H': '4h', '1D': '1d', '1W': '1w',
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def _request(self, endpoint, params=None):
        try:
            resp = self.session.get(self.BASE_URL + endpoint, params=params, timeout=30)
            if resp.status_code != 200:
                logger.error(f"Binance API 오류: {resp.status_code}")
                return None
            return resp.json()
        except Exception as e:
            logger.error(f"Binance API 요청 실패: {e}")
            return None

    def get_ticker(self, symbol):
        data = self._request("/fapi/v1/ticker/price", {'symbol': symbol})
        if data:
            return {'symbol': data.get('symbol'), 'lastPr': data.get('price'),
                    'price': float(data.get('price', 0))}
        return None

    def get_candles(self, symbol, interval, limit=300):
        binance_interval = self.TIMEFRAME_MAP.get(interval, interval.lower())
        data = self._request("/fapi/v1/klines", {
            'symbol': symbol, 'interval': binance_interval, 'limit': limit
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

    def get_candles_pagination(self, symbol, interval, required_count=300):
        binance_interval = self.TIMEFRAME_MAP.get(interval, interval.lower())
        all_df = pd.DataFrame()
        end_ts = int(time.time() * 1000)
        while len(all_df) < required_count:
            data = self._request("/fapi/v1/klines", {
                'symbol': symbol, 'interval': binance_interval, 'endTime': end_ts, 'limit': 1000
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
# Bitget API 클라이언트 (매매 전용)
# ═══════════════════════════════════════════════════════════════════════════════

class BitgetClient:
    def __init__(self, api_key, api_secret, passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = BASE_URL
        self.session = requests.Session()
        self._position_mode = None

    def _get_timestamp(self):
        return str(int(time.time() * 1000))

    def _sign(self, timestamp, method, request_path, body=""):
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(self.api_secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode('utf-8')

    def _get_headers(self, method, request_path, body=""):
        ts = self._get_timestamp()
        return {
            'ACCESS-KEY': self.api_key,
            'ACCESS-SIGN': self._sign(ts, method, request_path, body),
            'ACCESS-TIMESTAMP': ts,
            'ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json',
            'locale': 'en-US'
        }

    def _request(self, method, endpoint, params=None, body=None, retry_count=3):
        url = self.base_url + endpoint
        request_path = endpoint
        if params:
            qs = '&'.join([f"{k}={v}" for k, v in params.items()])
            request_path = endpoint + '?' + qs
            url = url + '?' + qs
        body_str = json.dumps(body, separators=(',', ':')) if body else ""
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
                if data.get('code') == '429':
                    wait_time = (attempt + 1) * 2
                    logger.warning(f"Rate Limit, {wait_time}초 대기 ({attempt + 1}/{retry_count})")
                    if attempt == 0:
                        send_error_alert(symbol or 'API', "Rate Limit (429)")
                    time.sleep(wait_time)
                    continue
                if data.get('code') != '00000':
                    logger.error(f"API 오류: {data}")
                    send_error_alert(symbol or 'API', f"Code: {data.get('code')}, Msg: {data.get('msg')}")
                    return None
                return data.get('data')
            except Exception as e:
                logger.error(f"API 요청 실패: {e}")
                if attempt < retry_count - 1:
                    time.sleep(1)
                    continue
                send_error_alert(symbol or 'API', f"요청 실패: {e}")
                return None
        return None

    def get_position_mode(self, product_type='USDT-FUTURES'):
        if self._position_mode:
            return self._position_mode
        data = self._request('GET', "/api/v2/mix/account/account", {
            'symbol': 'BTCUSDT', 'productType': product_type, 'marginCoin': 'USDT'
        })
        self._position_mode = data.get('posMode', 'one_way_mode') if data else 'one_way_mode'
        logger.info(f"📋 포지션 모드: {self._position_mode}")
        return self._position_mode

    def is_hedge_mode(self, product_type='USDT-FUTURES'):
        return self.get_position_mode(product_type) == 'hedge_mode'

    def get_account(self, product_type='USDT-FUTURES', margin_coin='USDT'):
        data = self._request('GET', "/api/v2/mix/account/accounts", {'productType': product_type})
        if not data:
            return None
        for acc in data:
            if acc.get('marginCoin') == margin_coin:
                return acc
        return data[0] if data else None

    def get_position(self, symbol, product_type='USDT-FUTURES', margin_coin='USDT'):
        data = self._request('GET', "/api/v2/mix/position/single-position", {
            'symbol': symbol, 'productType': product_type, 'marginCoin': margin_coin
        })
        return data if data else []

    def set_leverage(self, symbol, leverage, product_type='USDT-FUTURES',
                     margin_coin='USDT', hold_side='long'):
        body = {
            'symbol': symbol, 'productType': product_type,
            'marginCoin': margin_coin, 'leverage': str(leverage)
        }
        if self.is_hedge_mode(product_type):
            body['holdSide'] = hold_side
        result = self._request('POST', "/api/v2/mix/account/set-leverage", body=body)
        return result is not None

    def get_ticker(self, symbol, product_type='USDT-FUTURES'):
        data = self._request('GET', "/api/v2/mix/market/ticker", {
            'symbol': symbol, 'productType': product_type
        })
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
        return data

    def cancel_all_orders(self, symbol, product_type='USDT-FUTURES', margin_coin='USDT'):
        try:
            ts = self._get_timestamp()
            path = "/api/v2/mix/order/cancel-all-orders"
            body = {'symbol': symbol, 'productType': product_type, 'marginCoin': margin_coin}
            body_str = json.dumps(body)
            headers = self._get_headers('POST', path, body_str)
            resp = requests.post(f"{BASE_URL}{path}", headers=headers, data=body_str, timeout=10)
            data = resp.json()
            return data.get('code') in ('00000', '22001')
        except Exception as e:
            logger.error(f"[{symbol}] 주문 취소 예외: {e}")
            return False

    def get_order(self, symbol, order_id, product_type='USDT-FUTURES'):
        return self._request('GET', "/api/v2/mix/order/detail", {
            'symbol': symbol, 'productType': product_type, 'orderId': order_id
        })

    def cancel_order(self, symbol, order_id, product_type='USDT-FUTURES'):
        return self._request('POST', "/api/v2/mix/order/cancel-order", body={
            'symbol': symbol, 'productType': product_type, 'orderId': order_id
        })

    def place_limit_order(self, symbol, side, size, price,
                          trade_side, pos_side,
                          product_type='USDT-FUTURES', margin_coin='USDT'):
        return self._request('POST', "/api/v2/mix/order/place-order", body={
            'symbol': symbol, 'productType': product_type, 'marginMode': 'crossed',
            'marginCoin': margin_coin, 'size': size, 'price': price,
            'side': side, 'tradeSide': trade_side, 'posSide': pos_side,
            'orderType': 'limit', 'force': 'gtc'
        })

    def place_market_order(self, symbol, side, size,
                           trade_side, pos_side,
                           product_type='USDT-FUTURES', margin_coin='USDT'):
        return self._request('POST', "/api/v2/mix/order/place-order", body={
            'symbol': symbol, 'productType': product_type, 'marginMode': 'crossed',
            'marginCoin': margin_coin, 'size': size,
            'side': side, 'tradeSide': trade_side, 'posSide': pos_side,
            'orderType': 'market'
        })

    def flash_close_position(self, symbol, product_type='USDT-FUTURES', hold_side='long'):
        return self._request('POST', "/api/v2/mix/order/close-positions", body={
            'symbol': symbol, 'productType': product_type, 'holdSide': hold_side
        })


# ═══════════════════════════════════════════════════════════════════════════════
# 포트폴리오 매니저 (균등배분)
# ═══════════════════════════════════════════════════════════════════════════════

class PortfolioManager:
    def __init__(self, client, configs):
        self.client = client
        self.configs = [c for c in configs if c['enabled']]

    def get_account_info(self):
        acc = self.client.get_account()
        if not acc:
            return {'equity': 0, 'available': 0, 'margin': 0, 'pnl': 0}
        return {
            'equity': float(acc.get('usdtEquity', 0)),
            'available': float(acc.get('crossedMaxAvailable', 0)),
            'margin': float(acc.get('crossedMargin', 0)),
            'pnl': float(acc.get('unrealizedPL', 0))
        }

    def get_total_equity(self):
        return self.get_account_info()['equity']

    def get_available_balance(self):
        return self.get_account_info()['available']

    def get_position_status(self):
        """각 심볼별 포지션 보유 여부 {'BTCUSDT': True, ...}"""
        status = {}
        for cfg in self.configs:
            pos_data = self.client.get_position(cfg['symbol'])
            has_position = False
            if pos_data:
                for p in pos_data:
                    if float(p.get('total', 0)) > 0:
                        has_position = True
                        break
            status[cfg['symbol']] = has_position
        return status

    def calculate_invest_amount(self, symbol):
        """
        균등배분: 가용잔고 / 빈슬롯 수 vs 총자산 / 총코인수 중 작은 값
        """
        available = self.get_available_balance()
        if available <= 0:
            return 0

        position_status = self.get_position_status()

        # 이미 포지션 보유 중이면 0
        if position_status.get(symbol, False):
            logger.info(f"[{symbol}] 이미 포지션 보유 중")
            return 0

        # 빈 슬롯 수
        empty_slots = sum(1 for has_pos in position_status.values() if not has_pos)
        if empty_slots <= 0:
            return 0

        usable = available * 0.995  # 수수료 0.5% 고려

        # 방법1: 가용잔고 기반 균등배분
        invest_by_available = usable / empty_slots

        # 방법2: 총자산 기반 상한 (총자산 / 총코인수)
        total_equity = self.get_total_equity()
        max_per_coin = total_equity / TOTAL_COINS

        invest = min(invest_by_available, max_per_coin)

        logger.info(f"[{symbol}] 💰 균등배분: 가용 ${available:,.2f}, 빈슬롯 {empty_slots}개, "
                     f"투자=${invest:,.2f} (가용기반 ${invest_by_available:,.2f}, 상한 ${max_per_coin:,.2f})")

        return invest

    def log_portfolio_status(self):
        acc = self.get_account_info()
        position_status = self.get_position_status()
        used = sum(1 for v in position_status.values() if v)
        total = len(self.configs)

        logger.info(f"\n{'='*60}")
        logger.info(f"💰 포트폴리오 현황 (Bitget Major)")
        logger.info(f"{'='*60}")
        logger.info(f"   총자산: {acc['equity']:,.2f} USDT | 가용: {acc['available']:,.2f} USDT")
        logger.info(f"   마진: {acc['margin']:,.2f} USDT | PnL: {acc['pnl']:+,.2f} USDT")
        logger.info(f"   슬롯: {used}/{total} 사용 중 | 균등배분: {100/max(total,1):.1f}%/코인")
        logger.info(f"{'='*60}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# 트레이딩 봇
# ═══════════════════════════════════════════════════════════════════════════════

class TradingBot:
    def __init__(self, bitget_client, binance_client, config, portfolio):
        self.client = bitget_client
        self.signal_client = binance_client
        self.config = config
        self.portfolio = portfolio
        self.symbol = config['symbol']
        self.product_type = config['product_type']
        self.margin_coin = config['margin_coin']
        self.timeframe = config['timeframe']
        self.tick_size = config.get('tick_size', 0.1)
        self.size_decimals = config.get('size_decimals', 3)

        # 롱 파라미터
        self.long_ma = config['long_ma']
        self.long_sk = config['long_sk']
        self.long_sks = config['long_sks']
        self.long_sd = config['long_sd']
        self.long_lev = config['long_lev']

        # 숏 파라미터
        self.short_ma = config['short_ma']
        self.short_sk = config['short_sk']
        self.short_sks = config['short_sks']
        self.short_sd = config['short_sd']
        self.short_lev = config['short_lev']

        # 우선순위 (코인별 롱우선/숏우선)
        self.priority = config.get('priority', 'long')  # 'long' or 'short'

        # 스토캐스틱 캐시 (롱/숏 각각)
        self._stoch_cache_long = {'utc_date': None, 'is_bull': False, 'k': 0.0, 'd': 0.0}
        self._stoch_cache_short = {'utc_date': None, 'is_bear': False, 'k': 0.0, 'd': 0.0}

    # ── 가격/수량 포맷 ──

    def round_price(self, price):
        return round(price / self.tick_size) * self.tick_size

    def format_price(self, price):
        if self.tick_size >= 1: return f"{price:.0f}"
        elif self.tick_size >= 0.1: return f"{price:.1f}"
        elif self.tick_size >= 0.01: return f"{price:.2f}"
        elif self.tick_size >= 0.001: return f"{price:.3f}"
        elif self.tick_size >= 0.0001: return f"{price:.4f}"
        else: return f"{price:.5f}"

    def format_size(self, size):
        return f"{size:.{self.size_decimals}f}"

    # ── 포지션 조회 ──

    def get_current_position(self):
        positions = self.client.get_position(self.symbol, self.product_type, self.margin_coin)
        result = {'long': None, 'short': None}
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

    def wait_for_fill(self, order_id, timeout=ORDER_WAIT_SECONDS):
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
            if status in ('canceled', 'cancelled'):
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

    # ── 포지션 사이즈 계산 (균등배분) ──

    def calculate_position_size(self, price, leverage):
        if leverage <= 0:
            return "0"

        allocated = self.portfolio.calculate_invest_amount(self.symbol)
        if allocated <= 0:
            return "0"

        use = allocated * 0.99  # 99% 사용

        if use < 5:
            logger.warning(f"[{self.symbol}] 주문금액 < 5 USDT: {use:.2f}")
            return "0"

        # 최소 수량 테이블
        min_sizes = {
            'BTCUSDT': 0.001, 'ETHUSDT': 0.01, 'SOLUSDT': 0.1,
            'XRPUSDT': 1, 'DOGEUSDT': 1, 'ADAUSDT': 1
        }
        min_size = min_sizes.get(self.symbol, 0.001)

        size = (use * leverage) / price
        size = max(min_size, round(size, self.size_decimals))

        logger.info(f"[{self.symbol}] 💵 배분: ${allocated:,.2f}, 사용: ${use:,.2f}, "
                     f"Lev {leverage}x → 수량: {size}")
        return self.format_size(size)

    # ── 안전한 진입/청산 ──

    def safe_limit_entry(self, leverage, side='long'):
        if leverage <= 0:
            return False

        self.client.set_leverage(self.symbol, leverage, self.product_type, self.margin_coin, side)

        ticker = self.signal_client.get_ticker(self.symbol)
        if not ticker:
            send_error_alert(self.symbol, "현재가 조회 실패")
            return False

        price = float(ticker.get('price', 0))
        if price <= 0:
            return False

        target_size = self.calculate_position_size(price, leverage)
        if target_size == "0":
            return False

        target_size_float = float(target_size)
        remaining_size = target_size_float
        total_filled = 0.0

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
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            order_id = result.get('orderId')
            if not order_id:
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
                self.client.cancel_order(self.symbol, order_id, self.product_type)
            else:
                self.client.cancel_order(self.symbol, order_id, self.product_type)
            time.sleep(RETRY_DELAY_SECONDS)

        # 지정가 실패 → 시장가
        if remaining_size > 0:
            min_sizes = {
                'BTCUSDT': 0.001, 'ETHUSDT': 0.01, 'SOLUSDT': 0.1, 'XRPUSDT': 1,
                'TRXUSDT': 1, 'DOGEUSDT': 1, 'BCHUSDT': 0.01, 'ADAUSDT': 1
            }
            min_size = min_sizes.get(self.symbol, 0.001)
            if remaining_size >= min_size:
                logger.warning(f"[{self.symbol}] ⚠️ 시장가 전환: {self.format_size(remaining_size)}")
                self.client.cancel_all_orders(self.symbol, self.product_type, self.margin_coin)
                result = self.client.place_market_order(
                    self.symbol, order_side, self.format_size(remaining_size),
                    'open', side, self.product_type, self.margin_coin
                )
                if result:
                    send_entry_alert(self.symbol, side_label, self.format_size(target_size_float),
                                     entry_price_for_alert, leverage, "시장가")
                    return True
                else:
                    send_error_alert(self.symbol, f"{side_label} 시장가 진입 실패")
                    return total_filled > 0
            else:
                send_entry_alert(self.symbol, side_label, self.format_size(total_filled),
                                 entry_price_for_alert, leverage, order_type_for_alert)
                return True
        return True

    def safe_limit_close(self, side='long', reason=""):
        pos = None
        for attempt in range(3):
            all_pos = self.get_current_position()
            pos = all_pos.get(side)
            if pos and pos['size'] > 0:
                break
            elif pos is None and attempt < 2:
                time.sleep(2)
            else:
                break

        if not pos or pos['size'] <= 0:
            logger.info(f"[{self.symbol}] 청산할 {side} 포지션 없음")
            return True

        entry_price = pos.get('avg_price', 0)
        position_size = pos['size']
        unrealized_pnl = pos.get('unrealized_pnl', 0)

        order_side = 'sell' if side == 'long' else 'buy'
        side_label = 'Long' if side == 'long' else 'Short'
        reason_str = f" ({reason})" if reason else ""

        if DRY_RUN:
            logger.info(f"[{self.symbol}] [DRY RUN] {side_label} 청산{reason_str}")
            return True

        for retry in range(1, MAX_LIMIT_RETRY + 1):
            for attempt in range(3):
                all_pos = self.get_current_position()
                pos = all_pos.get(side)
                if pos is not None:
                    break
                time.sleep(1)

            if not pos or pos['size'] <= 0:
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
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            order_id = result.get('orderId')
            if not order_id:
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            status, filled = self.wait_for_fill(order_id)
            if status == 'filled':
                send_close_alert(self.symbol, position_size, entry_price, exit_price, unrealized_pnl, reason)
                return True
            elif status == 'partially_filled':
                self.client.cancel_order(self.symbol, order_id, self.product_type)
            else:
                self.client.cancel_order(self.symbol, order_id, self.product_type)
            time.sleep(RETRY_DELAY_SECONDS)

        # 플래시 청산
        logger.warning(f"[{self.symbol}] ⚠️ 플래시 청산...")
        self.client.cancel_all_orders(self.symbol, self.product_type, self.margin_coin)
        result = self.client.flash_close_position(self.symbol, self.product_type, side)
        if result:
            ticker = self.client.get_ticker(self.symbol, self.product_type)
            exit_price = float(ticker.get('lastPr', 0)) if ticker else entry_price
            send_close_alert(self.symbol, position_size, entry_price, exit_price, unrealized_pnl, reason + " (플래시)")
            return True

        send_error_alert(self.symbol, f"{side_label} 청산 최종 실패")
        return False

    # ── 지표 계산 ──

    def _calc_stochastic(self, df, k_period, k_smooth, d_period):
        low_min = df['low'].rolling(window=k_period).min()
        high_max = df['high'].rolling(window=k_period).max()
        fast_k = ((df['close'] - low_min) / (high_max - low_min)) * 100
        fast_k = fast_k.replace([np.inf, -np.inf], np.nan)
        slow_k = fast_k.rolling(window=k_smooth).mean()
        slow_d = slow_k.rolling(window=d_period).mean()
        return slow_k, slow_d

    def get_long_stochastic(self):
        """롱용 스토캐스틱 (UTC 날짜 기준 캐싱)"""
        required = self.long_sk + self.long_sks + self.long_sd + 50
        df = self.signal_client.get_candles_pagination(self.symbol, '1D', required)
        if df.empty:
            return False, 0, 0

        slow_k, slow_d = self._calc_stochastic(df, self.long_sk, self.long_sks, self.long_sd)
        vk = slow_k.dropna()
        vd = slow_d.dropna()
        if len(vk) < 1 or len(vd) < 1:
            return False, 0, 0

        k, d = float(vk.iloc[-1]), float(vd.iloc[-1])
        if pd.isna(k) or pd.isna(d):
            return False, 0, 0

        is_bull = k > d
        logger.info(f"[{self.symbol}] 📊 롱 Stoch({self.long_sk},{self.long_sks},{self.long_sd}): K={k:.2f}, D={d:.2f} → {'상승' if is_bull else '하락'}")
        return is_bull, k, d

    def get_short_stochastic(self):
        """숏용 스토캐스틱 (UTC 날짜 기준 캐싱)"""
        required = self.short_sk + self.short_sks + self.short_sd + 50
        df = self.signal_client.get_candles_pagination(self.symbol, '1D', required)
        if df.empty:
            return False, 0, 0

        slow_k, slow_d = self._calc_stochastic(df, self.short_sk, self.short_sks, self.short_sd)
        vk = slow_k.dropna()
        vd = slow_d.dropna()
        if len(vk) < 1 or len(vd) < 1:
            return False, 0, 0

        k, d = float(vk.iloc[-1]), float(vd.iloc[-1])
        if pd.isna(k) or pd.isna(d):
            return False, 0, 0

        is_bear = k < d
        logger.info(f"[{self.symbol}] 📊 숏 Stoch({self.short_sk},{self.short_sks},{self.short_sd}): K={k:.2f}, D={d:.2f} → {'하락' if is_bear else '상승'}")
        return is_bear, k, d

    # ── 최종 액션 결정 (코인별 우선순위) ──

    def _check_long_condition(self):
        """롱 조건 확인. Returns: (충족여부, leverage) or (False, 0)"""
        df_long = self.signal_client.get_candles(self.symbol, self.timeframe, self.long_ma + 10)
        if df_long is None or df_long.empty or len(df_long) < self.long_ma:
            logger.warning(f"[{self.symbol}] 롱 캔들 조회 실패")
            return False, 0

        ma_long = df_long['close'].rolling(window=self.long_ma).mean().iloc[-1]
        open_price = df_long.iloc[-1]['open']

        if pd.isna(ma_long):
            return False, 0

        ma_long_signal = open_price > ma_long
        is_bull, k_long, d_long = self.get_long_stochastic()

        logger.info(f"[{self.symbol}] 📊 롱: 시가 {open_price:.4f} {'>' if ma_long_signal else '<='} MA{self.long_ma} {ma_long:.4f}, "
                     f"K={k_long:.2f} {'>' if is_bull else '<='} D={d_long:.2f}")

        if ma_long_signal and is_bull:
            return True, self.long_lev
        return False, 0

    def _check_short_condition(self):
        """숏 조건 확인. Returns: (충족여부, leverage) or (False, 0)"""
        df_short = self.signal_client.get_candles(self.symbol, self.timeframe, self.short_ma + 10)
        if df_short is None or df_short.empty or len(df_short) < self.short_ma:
            logger.warning(f"[{self.symbol}] 숏 캔들 조회 실패")
            return False, 0

        ma_short = df_short['close'].rolling(window=self.short_ma).mean().iloc[-1]
        open_price_short = df_short.iloc[-1]['open']

        if pd.isna(ma_short):
            return False, 0

        ma_short_signal = open_price_short < ma_short
        is_bear, k_short, d_short = self.get_short_stochastic()

        logger.info(f"[{self.symbol}] 📊 숏: 시가 {open_price_short:.4f} {'<' if ma_short_signal else '>='} MA{self.short_ma} {ma_short:.4f}, "
                     f"K={k_short:.2f} {'<' if is_bear else '>='} D={d_short:.2f}")

        if ma_short_signal and is_bear:
            return True, self.short_lev
        return False, 0

    def get_final_action(self):
        """
        코인별 priority에 따라 우선 방향을 먼저 확인.
        - priority='long': 롱 조건 먼저 → 미충족 시 숏 조건 확인
        - priority='short': 숏 조건 먼저 → 미충족 시 롱 조건 확인

        Returns: (action, leverage, side)
          action: 'LONG', 'SHORT', 'CASH'
          leverage: 목표 레버리지
          side: 'long', 'short', None
        """
        priority_label = '롱우선' if self.priority == 'long' else '숏우선'
        logger.info(f"[{self.symbol}] 🔀 전략: {priority_label}")

        if self.priority == 'long':
            # 1차: 롱 조건 확인
            long_met, long_lev = self._check_long_condition()
            if long_met:
                logger.info(f"[{self.symbol}] ✅ 롱 조건 충족 → Long {long_lev}x")
                return ('LONG', long_lev, 'long')

            # 2차: 숏 조건 확인 (롱 미충족 시)
            short_met, short_lev = self._check_short_condition()
            if short_met:
                logger.info(f"[{self.symbol}] ✅ 숏 조건 충족 → Short {short_lev}x")
                return ('SHORT', short_lev, 'short')

        else:  # priority == 'short'
            # 1차: 숏 조건 확인
            short_met, short_lev = self._check_short_condition()
            if short_met:
                logger.info(f"[{self.symbol}] ✅ 숏 조건 충족 → Short {short_lev}x")
                return ('SHORT', short_lev, 'short')

            # 2차: 롱 조건 확인 (숏 미충족 시)
            long_met, long_lev = self._check_long_condition()
            if long_met:
                logger.info(f"[{self.symbol}] ✅ 롱 조건 충족 → Long {long_lev}x")
                return ('LONG', long_lev, 'long')

        # 둘 다 미충족
        logger.info(f"[{self.symbol}] ❌ 롱/숏 미충족 → 현금")
        return ('CASH', 0, None)

    # ── 상태 표시 ──

    def show_status(self):
        priority_label = '롱우선' if self.priority == 'long' else '숏우선'
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 [{self.symbol}] (Binance 신호 → Bitget 매매) [{priority_label}]")
        logger.info(f"   L{self.long_lev}x MA{self.long_ma} Stoch({self.long_sk},{self.long_sks},{self.long_sd})")
        logger.info(f"   S{self.short_lev}x MA{self.short_ma} Stoch({self.short_sk},{self.short_sks},{self.short_sd})")
        logger.info(f"{'='*60}")

        pos = self.get_current_position()
        if pos['long']:
            p = pos['long']
            logger.info(f"📍 Long: {p['size']} @ {p['leverage']}x, PnL: {p['unrealized_pnl']:+,.2f}")
        if pos['short']:
            p = pos['short']
            logger.info(f"📍 Short: {p['size']} @ {p['leverage']}x, PnL: {p['unrealized_pnl']:+,.2f}")
        if not pos['long'] and not pos['short']:
            logger.info(f"📍 포지션: 없음 (현금)")

    # ── 메인 실행 ──

    def execute(self):
        logger.info(f"\n{'─'*60}")
        logger.info(f"[{self.symbol}] 실행")
        logger.info(f"{'─'*60}")

        action, target_lev, target_side = self.get_final_action()
        pos = self.get_current_position()

        has_long = pos['long'] is not None and pos['long']['size'] > 0
        has_short = pos['short'] is not None and pos['short']['size'] > 0

        if has_long:
            p = pos['long']
            logger.info(f"[{self.symbol}] 📍 현재: Long {p['size']} @ {p['leverage']}x, PnL: {p['unrealized_pnl']:+.2f}")
        if has_short:
            p = pos['short']
            logger.info(f"[{self.symbol}] 📍 현재: Short {p['size']} @ {p['leverage']}x, PnL: {p['unrealized_pnl']:+.2f}")
        if not has_long and not has_short:
            logger.info(f"[{self.symbol}] 📍 현재: 현금")

        lev_desc = f"{target_lev}x" if target_lev > 0 else "현금"
        logger.info(f"[{self.symbol}] 🎯 목표: {action} ({lev_desc})")

        # 액션 실행
        priority_label = '롱우선' if self.priority == 'long' else '숏우선'
        if action == 'LONG':
            if has_short:
                logger.info(f"[{self.symbol}] 📉 숏 청산 (롱 전환) [{priority_label}]")
                self.safe_limit_close(side='short', reason="롱 전환")
                time.sleep(1)

            if not has_long:
                logger.info(f"[{self.symbol}] 📈 Long 진입 ({target_lev}x)")
                self.safe_limit_entry(target_lev, side='long')
            else:
                curr_lev = pos['long']['leverage']
                if curr_lev != target_lev:
                    logger.info(f"[{self.symbol}] 🔄 롱 레버리지 변경: {curr_lev}x → {target_lev}x")
                    self.safe_limit_close(side='long', reason="레버리지 변경")
                    time.sleep(1)
                    self.safe_limit_entry(target_lev, side='long')
                else:
                    logger.info(f"[{self.symbol}] ➡️ Long 유지")
                    add_hold_position(self.symbol, pos['long']['size'], curr_lev,
                                      pos['long'].get('unrealized_pnl', 0), side='long')

        elif action == 'SHORT':
            if has_long:
                logger.info(f"[{self.symbol}] 📈 롱 청산 (숏 전환)")
                self.safe_limit_close(side='long', reason="숏 전환")
                time.sleep(1)

            if not has_short:
                logger.info(f"[{self.symbol}] 📉 Short 진입 ({target_lev}x)")
                self.safe_limit_entry(target_lev, side='short')
            else:
                curr_lev = pos['short']['leverage']
                if curr_lev != target_lev:
                    logger.info(f"[{self.symbol}] 🔄 숏 레버리지 변경: {curr_lev}x → {target_lev}x")
                    self.safe_limit_close(side='short', reason="레버리지 변경")
                    time.sleep(1)
                    self.safe_limit_entry(target_lev, side='short')
                else:
                    logger.info(f"[{self.symbol}] ➡️ Short 유지")
                    add_hold_position(self.symbol, pos['short']['size'], curr_lev,
                                      pos['short'].get('unrealized_pnl', 0), side='short')

        elif action == 'CASH':
            if has_long:
                logger.info(f"[{self.symbol}] 📉 롱 청산 (현금)")
                self.safe_limit_close(side='long', reason="조건 미충족")
            if has_short:
                logger.info(f"[{self.symbol}] 📈 숏 청산 (현금)")
                self.safe_limit_close(side='short', reason="조건 미충족")
            if not has_long and not has_short:
                logger.info(f"[{self.symbol}] ➡️ 현금 유지")


# ═══════════════════════════════════════════════════════════════════════════════
# 유틸리티
# ═══════════════════════════════════════════════════════════════════════════════

def get_candle_start_time(dt, timeframe):
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


def get_next_candle_time(start, timeframe):
    tf_min = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1H': 60, '4H': 240, '1D': 1440}
    return start + timedelta(minutes=tf_min.get(timeframe, 240))


# ═══════════════════════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global BOT_START_TIME
    BOT_START_TIME = datetime.now()
    setup_shutdown_handlers()

    print("\n" + "=" * 70)
    print("📊 Bitget Major Bot v1.0 (Binance 신호 + Bitget 매매)")
    print("   전략: 롱 우선 + 숏 보조 (독립 파라미터)")
    print("   대상: BTC, ETH, XRP, SOL, DOGE, ADA")
    print("   배분: 6코인 균등배분 (각 16.7%)")
    print("=" * 70)
    print(f"🔧 모드: {'🔵 DRY RUN' if DRY_RUN else '🔴 LIVE'}")
    print(f"📡 신호: Binance Futures 공개 API")
    print(f"💹 매매: Bitget Futures API")
    print(f"📋 지정가 재시도: {MAX_LIMIT_RETRY}회 → 시장가")
    print()

    enabled = [c for c in TRADING_CONFIGS if c['enabled']]
    for c in enabled:
        print(f"  {c['symbol']}: L{c['long_lev']}x MA{c['long_ma']} Stoch({c['long_sk']},{c['long_sks']},{c['long_sd']}) | "
              f"S{c['short_lev']}x MA{c['short_ma']} Stoch({c['short_sk']},{c['short_sks']},{c['short_sd']})")
    print("=" * 70)

    key = API_KEY
    secret = API_SECRET
    pw = API_PASSPHRASE

    if not all([key, secret, pw]):
        logger.error("❌ Bitget API 키 미설정. .env 파일 확인")
        return

    bitget_client = BitgetClient(key, secret, pw)
    binance_client = BinancePublicClient()

    # Binance 연결 테스트
    logger.info("📡 Binance 공개 API 연결 테스트...")
    test_ticker = binance_client.get_ticker('BTCUSDT')
    if test_ticker:
        logger.info(f"✅ Binance 연결 성공 - BTC: ${test_ticker['price']:,.2f}")
    else:
        logger.warning("⚠️ Binance 연결 실패")

    # Bitget 포지션 모드 확인
    try:
        pos_mode = bitget_client.get_position_mode()
        logger.info(f"🔧 Bitget 포지션 모드: {pos_mode}")
    except Exception as e:
        logger.warning(f"⚠️ Bitget 모드 조회 실패: {e}")

    portfolio = PortfolioManager(bitget_client, TRADING_CONFIGS)
    bots = [TradingBot(bitget_client, binance_client, c, portfolio) for c in enabled]

    if not bots:
        logger.error("❌ 활성 전략 없음")
        return

    logger.info(f"\n🚀 봇 시작 ({len(bots)}개 코인)")

    # 시작 알림
    try:
        total_equity = portfolio.get_total_equity()
    except Exception:
        total_equity = 0
    send_bot_start_alert(enabled, total_equity)

    # 즉시 1회 실행
    logger.info(f"\n{'='*60}")
    logger.info(f"🔥 즉시 실행 (1회)")
    logger.info(f"{'='*60}")

    try:
        portfolio.log_portfolio_status()
    except Exception as e:
        logger.warning(f"⚠️ 포트폴리오 조회 실패: {e}")

    clear_trade_results()

    test_ticker = binance_client.get_ticker('BTCUSDT')
    if test_ticker is None:
        logger.warning("⚠️ API 실패, 다음 스케줄에 재시도")
    else:
        for i, bot in enumerate(bots):
            try:
                if i > 0:
                    time.sleep(SYMBOL_DELAY_SECONDS)
                bot.show_status()
                bot.execute()
            except Exception as e:
                logger.error(f"[{bot.symbol}] 오류: {e}")
                send_error_alert(bot.symbol, str(e))
                import traceback
                traceback.print_exc()

        try:
            total_equity = portfolio.get_total_equity()
            available = portfolio.get_available_balance()
            send_trading_summary(total_equity, available)
        except Exception as e:
            logger.warning(f"⚠️ 종합 메시지 실패: {e}")

    # 메인 루프
    now = datetime.now(timezone.utc)
    last_executed = {}
    for bot in bots:
        k = f"{bot.symbol}_{bot.timeframe}"
        last_executed[k] = get_candle_start_time(now, bot.timeframe)

    candle_start = get_candle_start_time(now, '4H')
    next_candle = get_next_candle_time(candle_start, '4H')
    logger.info(f"\n⏰ 다음 4H봉: {next_candle.strftime('%Y-%m-%d %H:%M:%S')} UTC "
                f"({(next_candle - now).total_seconds() / 60:.1f}분)")

    try:
        while True:
            now = datetime.now(timezone.utc)
            executed_count = 0
            api_failed = False
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
                            time.sleep(SYMBOL_DELAY_SECONDS)

                        if not first_bot_executed:
                            clear_trade_results()
                            test_ticker = binance_client.get_ticker('BTCUSDT')
                            if test_ticker is None:
                                logger.warning("⚠️ API 실패, 스킵")
                                api_failed = True
                                for b in bots:
                                    bk = f"{b.symbol}_{b.timeframe}"
                                    last_executed[bk] = start
                                break
                            first_bot_executed = True

                        logger.info(f"\n🕐 {bot.timeframe} 봉: {start}")
                        if bot == bots[0]:
                            try:
                                portfolio.log_portfolio_status()
                            except Exception:
                                pass
                        bot.execute()
                        last_executed[k] = start
                        executed_count += 1
                except Exception as e:
                    logger.error(f"[{bot.symbol}] 오류: {e}")
                    send_error_alert(bot.symbol, str(e))
                    import traceback
                    traceback.print_exc()

            if executed_count > 0 and not api_failed:
                try:
                    total_equity = portfolio.get_total_equity()
                    available = portfolio.get_available_balance()
                    send_trading_summary(total_equity, available)
                except Exception:
                    pass

            next_times = [get_next_candle_time(get_candle_start_time(now, b.timeframe), b.timeframe) for b in bots]
            next_run = min(next_times)
            sleep_sec = (next_run - now).total_seconds() + CANDLE_START_DELAY
            if sleep_sec > 0:
                logger.info(f"\n⏰ 다음: {next_run.strftime('%H:%M:%S')} UTC ({sleep_sec / 60:.1f}분)")
                while sleep_sec > 0:
                    time.sleep(min(sleep_sec, 300))
                    sleep_sec -= 300
            else:
                time.sleep(RETRY_INTERVAL)
    except KeyboardInterrupt:
        logger.info("\n👋 Ctrl+C 종료")
        send_shutdown_alert(reason="Ctrl+C")


if __name__ == "__main__":
    main()
