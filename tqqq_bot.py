"""
================================================================================
TQQQ Sniper 텔레그램 알림 봇 v1.2.0 (동적 MA 전략)
================================================================================
- TQQQ 가격 및 시그널 분석
- 매일 지정된 시간에 텔레그램 알림 전송
- 시그널 변경 시 알림
- [v1.1.0] API 실패 시 다음 스케줄에 자동 재시도
- [v1.2.0] k<d 구간 동적 MA 2개 선택 전략 적용
================================================================================
"""

import sys
import typing

if sys.version_info < (3, 10) and hasattr(typing, '_GenericAlias'):
    # yfinance 0.2.60 uses Python 3.10 union annotations during import.
    # This keeps the bot runnable in the existing Python 3.9 conda setup.
    def _generic_alias_or(self, other):
        return typing.Union[self, other]

    def _generic_alias_ror(self, other):
        return typing.Union[other, self]

    typing._GenericAlias.__or__ = _generic_alias_or
    typing._GenericAlias.__ror__ = _generic_alias_ror

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import requests
import schedule
import time
import logging
import os
import signal
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return False

# .env 파일 로드
load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 설정
# ═══════════════════════════════════════════════════════════════════════════════

# 텔레그램 설정 (환경변수에서 로드)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 알림 시간 설정 (한국 시간 기준)
ALERT_TIME = '06:00'  # 화~토 아침 6시 (미국 월~금 장 마감 후)

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('tqqq_alert.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 API 호출 Timeout Wrapper
# ═══════════════════════════════════════════════════════════════════════════════

class APITimeoutError(Exception):
    """API 호출 타임아웃 에러"""
    pass


def _timeout_handler(signum, frame):
    """타임아웃 시그널 핸들러"""
    raise APITimeoutError("API 호출 타임아웃")


def call_with_timeout(func, timeout=60):
    """
    함수를 timeout과 함께 실행 (signal.alarm 방식 - Linux 전용)
    
    Args:
        func: 실행할 함수 (lambda로 전달)
        timeout: 타임아웃 시간 (초)
    
    Returns:
        함수 실행 결과 또는 None (타임아웃/에러 시)
    """
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    
    try:
        result = func()
        signal.alarm(0)
        return result
    except APITimeoutError:
        logger.warning(f"API 호출 타임아웃 ({timeout}초)")
        return None
    except Exception as e:
        signal.alarm(0)
        logger.warning(f"API 호출 중 오류: {e}")
        return None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 텔레그램 함수
# ═══════════════════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    """텔레그램 메시지 전송"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("텔레그램 설정이 되어있지 않습니다.")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message
        }
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            logger.info("텔레그램 알림 전송 성공")
            return True
        else:
            logger.error(f"텔레그램 전송 실패: {response.text}")
            return False
    except Exception as e:
        logger.error(f"텔레그램 전송 중 오류: {e}")
        return False

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 TQQQ 분석기
# ═══════════════════════════════════════════════════════════════════════════════

class TQQQAnalyzer:
    def __init__(self):
        # 나스닥100 기반 동적 MA 전략 최적화 결과
        # CAGR 31.91%, Sharpe 0.833, MDD -63.47%, Calmar 0.503
        self.stoch_config = {'period': 128, 'k_period': 92, 'd_period': 86}
        self.ma_periods = [31, 84, 263, 315]

    def get_data(self, days_back=None):
        """TQQQ 데이터 가져오기 (동적 상태 추적을 위해 기본값은 상장 이후 최대기간)"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back) if days_back else None
        try:
            # timeout wrapper 적용
            def fetch_data():
                ticker = yf.Ticker('TQQQ')
                if start_date is None:
                    return ticker.history(period='max', auto_adjust=True)
                return ticker.history(start=start_date, end=end_date, auto_adjust=True)
            
            data = call_with_timeout(fetch_data, timeout=60)
            
            if data is None or data.empty:
                logger.error("데이터 로드 실패 또는 빈 데이터")
                return None
            
            df = pd.DataFrame({
                'Open': data['Open'],
                'High': data['High'],
                'Low': data['Low'],
                'Close': data['Close']
            })
            return df.dropna()
        except Exception as e:
            logger.error(f"데이터 로드 실패: {e}")
            return None

    def calculate_indicators(self, data):
        """기술적 지표 계산"""
        df = data.copy()
        p, k, d = self.stoch_config.values()
        
        # Stochastic
        df['HH'] = df['High'].rolling(window=p).max()
        df['LL'] = df['Low'].rolling(window=p).min()
        df['%K'] = ((df['Close'] - df['LL']) / (df['HH'] - df['LL']) * 100).rolling(window=k).mean()
        df['%D'] = df['%K'].rolling(window=d).mean()
        
        # 이동평균선
        for ma in self.ma_periods:
            df[f'MA{ma}'] = df['Close'].rolling(window=ma).mean()
            df[f'Dev{ma}'] = ((df['Close'] - df[f'MA{ma}']) / df[f'MA{ma}']) * 100
        
        return df.dropna()

    def _fallback_two_closest(self, price, ma_values, selected):
        """부족한 MA를 현재가와 가까운 순서로 채운다."""
        selected = list(selected)
        for idx in np.argsort(np.abs(ma_values - price)):
            idx = int(idx)
            if idx not in selected:
                selected.append(idx)
            if len(selected) >= 2:
                break
        return selected[:2]

    def _calculate_dynamic_allocations(self, data):
        """
        동적 MA 2개 선택 전략을 전체 데이터에 순차 적용한다.

        k>d:
          4개 MA 충족 수를 25%씩 반영.
        k<d:
          전일 선정 MA쌍을 당일 종가로 판정한다.
          0%권  -> 현재가 위 근접 MA 2개 고정 추적, 1개 돌파=50%, 2개 돌파=100%
          50%권 -> 현재가 위/아래 근접 MA 1개씩 새로 선택
          100%권 -> 현재가 아래 근접 MA 2개 고정 추적, 1개 이탈=50%, 2개 이탈=0%
        """
        ratios = []
        selected_periods = []
        selected_modes = []
        prev_target = 0.0
        active_pair = None
        active_mode = None

        ma_cols = [f'MA{p}' for p in self.ma_periods]

        def select_pair_for_next(target, price, ma_values, current_pair=None, current_mode=None):
            """오늘 종가 기준으로 다음 거래일에 판정할 MA쌍을 고른다."""
            if target <= 0.0:
                if current_mode == "resistance" and current_pair is not None:
                    return current_pair, current_mode
                above = np.where(ma_values > price)[0]
                above = sorted(above, key=lambda idx: ma_values[idx] - price)
                return self._fallback_two_closest(price, ma_values, above[:2]), "resistance"

            if target >= 1.0:
                if current_mode == "support" and current_pair is not None:
                    return current_pair, current_mode
                below = np.where(ma_values < price)[0]
                below = sorted(below, key=lambda idx: price - ma_values[idx])
                return self._fallback_two_closest(price, ma_values, below[:2]), "support"

            above = np.where(ma_values > price)[0]
            below = np.where(ma_values < price)[0]
            selected = []
            if len(above) > 0:
                selected.append(int(min(above, key=lambda idx: ma_values[idx] - price)))
            if len(below) > 0:
                selected.append(int(min(below, key=lambda idx: price - ma_values[idx])))
            return self._fallback_two_closest(price, ma_values, selected), "balanced"

        mode_labels = {
            "resistance": "0%: 위 근접 MA 2개",
            "balanced": "50%: 위/아래 근접 MA 1개씩",
            "support": "100%: 아래 근접 MA 2개",
        }

        for _, row in data.iterrows():
            price = row['Close']
            ma_values = row[ma_cols].to_numpy(dtype=float)
            is_bullish = row['%K'] > row['%D']

            if is_bullish:
                target = sum(price > ma for ma in ma_values) * 0.25
                selected = []
                mode = "BULLISH: 4개 MA 조건 수"
            else:
                if active_pair is None:
                    active_pair, active_mode = select_pair_for_next(prev_target, price, ma_values)
                selected = active_pair
                mode = mode_labels.get(active_mode, "동적 MA쌍")
                hit_count = sum(price > ma_values[idx] for idx in active_pair)
                target = hit_count * 0.5

            active_pair, active_mode = select_pair_for_next(
                target, price, ma_values, active_pair, active_mode
            )

            ratios.append(target)
            selected_periods.append([self.ma_periods[idx] for idx in selected])
            selected_modes.append(mode)
            prev_target = target

        return (
            pd.Series(ratios, index=data.index),
            pd.Series(selected_periods, index=data.index),
            pd.Series(selected_modes, index=data.index)
        )

    def analyze(self, data):
        """시그널 분석"""
        curr = data.iloc[-1]
        prev = data.iloc[-2]
        
        is_bullish = curr['%K'] > curr['%D']
        ma_signals = {p: curr['Close'] > curr[f'MA{p}'] for p in self.ma_periods}

        allocations, selected_mas, selected_modes = self._calculate_dynamic_allocations(data)
        tqqq_ratio = allocations.iloc[-1]
        
        cash_ratio = 1 - tqqq_ratio
        prev_tqqq = allocations.iloc[-2]
        
        change = tqqq_ratio - prev_tqqq
        
        return {
            'price': curr['Close'],
            'prev_price': prev['Close'],
            'price_change': curr['Close'] - prev['Close'],
            'price_change_pct': (curr['Close'] - prev['Close']) / prev['Close'] * 100,
            'tqqq': tqqq_ratio,
            'cash': cash_ratio,
            'prev_tqqq': prev_tqqq,
            'change': change,
            'is_bullish': is_bullish,
            'ma_signals': ma_signals,
            'stoch_k': curr['%K'],
            'stoch_d': curr['%D'],
            'deviations': {p: curr[f'Dev{p}'] for p in self.ma_periods},
            'selected_mas': selected_mas.iloc[-1],
            'selected_mode': selected_modes.iloc[-1],
            'ma_periods': self.ma_periods,
            'strategy': '동적 MA 전략',
            'date': curr.name
        }

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 알림 생성
# ═══════════════════════════════════════════════════════════════════════════════

def create_alert_message(result: dict) -> str:
    """텔레그램 알림 메시지 생성"""
    r = result
    
    # 날짜 정보
    day_names = ['화', '수', '목', '금', '토']
    
    # date가 timestamp인 경우와 datetime인 경우 처리
    if hasattr(r['date'], 'weekday'):
        date_obj = r['date']
    else:
        date_obj = pd.to_datetime(r['date'])
        
    date_str = date_obj.strftime('%Y.%m.%d')
    # 요일 처리 (월=0, ... 금=4) - TQQQ 데이터는 미국 기준이라 한국 요일과 맞추기 위해 조정
    # 미국 장 마감(16:00)은 한국 다음날 새벽이므로, 한국 기준 요일 표시
    weekday = date_obj.weekday()
    # 안전하게 처리
    day_str = day_names[weekday] if weekday < 5 else "주말"
    
    # 가격 변동
    price_up = r['price_change'] >= 0
    price_sign = '+' if price_up else ''
    price_emoji = '📈' if price_up else '📉'
    
    # 비중 퍼센트
    tqqq_pct = int(r['tqqq'] * 100)
    cash_pct = int(r['cash'] * 100)
    prev_tqqq_pct = int(r['prev_tqqq'] * 100)
    change_pct = int(r['change'] * 100)
    
    # 시그널 결정
    if r['change'] > 0.01:
        action_emoji = '🚀'
        action_text = f"TQQQ {change_pct}% 매수"
    elif r['change'] < -0.01:
        action_emoji = '⚠️'
        action_text = f"TQQQ {abs(change_pct)}% 매도"
    else:
        action_emoji = '☕'
        action_text = "HOLD (변동 없음)"
    
    # 국면
    regime_emoji = '🟢' if r['is_bullish'] else '🔴'
    regime_text = 'BULLISH (K > D)' if r['is_bullish'] else 'BEARISH (K < D)'
    
    # MA 시그널
    ma_items = [
        f"MA{p}: {'✅' if r['ma_signals'][p] else '❌'}"
        for p in r['ma_periods']
    ]
    ma_lines = "   " + " | ".join(ma_items[:2])
    if len(ma_items) > 2:
        ma_lines += "\n   " + " | ".join(ma_items[2:])

    if r['selected_mas']:
        selected_line = "   기준 MA: " + ", ".join(f"MA{p}" for p in r['selected_mas'])
    else:
        selected_line = "   기준 MA: 4개 MA 조건 수"
    
    # 메시지 생성
    msg = f"""⚡ TQQQ SNIPER
━━━━━━━━━━━━━━━
📅 {date_str} ({day_str})
━━━━━━━━━━━━━━━

{price_emoji} 가격
   ${r['price']:.2f} ({price_sign}{r['price_change_pct']:.2f}%)

{action_emoji} TODAY'S ACTION
   {action_text}
   비중: {prev_tqqq_pct}% → {tqqq_pct}%

📊 포트폴리오
   TQQQ: {tqqq_pct}%
   CASH: {cash_pct}%

{regime_emoji} 시장 국면
   {regime_text}
   K: {r['stoch_k']:.1f} / D: {r['stoch_d']:.1f}

📡 MA 시그널
{ma_lines}

🎯 동적 기준
   {r['selected_mode']}
{selected_line}
━━━━━━━━━━━━━━━
"""
    return msg

def send_tqqq_alert():
    """TQQQ 알림 전송"""
    logger.info("TQQQ 분석 시작...")
    
    analyzer = TQQQAnalyzer()
    
    # 데이터 가져오기
    data = analyzer.get_data()
    if data is None:
        logger.warning("⚠️ 데이터를 불러올 수 없습니다. 다음 스케줄에 재시도합니다.")
        send_telegram("⚠️ TQQQ 알림 오류\n데이터를 불러올 수 없습니다.\n다음 스케줄 시간에 자동 재시도합니다.")
        return
    
    # 지표 계산
    try:
        data = analyzer.calculate_indicators(data)
    except Exception as e:
        logger.warning(f"⚠️ 지표 계산 실패: {e}. 다음 스케줄에 재시도합니다.")
        send_telegram(f"⚠️ TQQQ 알림 오류\n지표 계산 실패: {e}\n다음 스케줄 시간에 자동 재시도합니다.")
        return
    
    # 분석
    try:
        result = analyzer.analyze(data)
    except Exception as e:
        logger.warning(f"⚠️ 분석 실패: {e}. 다음 스케줄에 재시도합니다.")
        send_telegram(f"⚠️ TQQQ 알림 오류\n분석 실패: {e}\n다음 스케줄 시간에 자동 재시도합니다.")
        return
    
    # 알림 메시지 생성 및 전송
    message = create_alert_message(result)
    send_telegram(message)
    
    logger.info(f"알림 전송 완료 - TQQQ: {int(result['tqqq']*100)}%, 변동: {int(result['change']*100):+}%")

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 메인
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 50)
    logger.info("TQQQ Sniper 텔레그램 알림 봇 v1.2.0 시작")
    logger.info("(동적 MA 전략 + 서버 점검 시 자동 복구 기능 적용)")
    logger.info("=" * 50)
    
    # 시작 시 즉시 한 번 실행 (잘 돌아가는지 확인용)
    logger.info("시작 시 즉시 알림 전송...")
    send_tqqq_alert()
    
    # 스케줄 설정 (화~토 06:00 = 미국 월~금 장 마감 후)
    schedule.every().tuesday.at(ALERT_TIME).do(send_tqqq_alert)
    schedule.every().wednesday.at(ALERT_TIME).do(send_tqqq_alert)
    schedule.every().thursday.at(ALERT_TIME).do(send_tqqq_alert)
    schedule.every().friday.at(ALERT_TIME).do(send_tqqq_alert)
    schedule.every().saturday.at(ALERT_TIME).do(send_tqqq_alert)
    logger.info(f"스케줄 등록: 화~토 {ALERT_TIME}")
    
    logger.info("스케줄러 시작. Ctrl+C로 종료.")
    
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logger.error(f"스케줄 실행 중 오류: {e}. 다음 스케줄에 재시도합니다.")
        time.sleep(60)

if __name__ == "__main__":
    main()
