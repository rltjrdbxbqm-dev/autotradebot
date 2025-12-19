"""
================================================================================
TQQQ Sniper 텔레그램 알림 봇
================================================================================
- TQQQ 가격 및 시그널 분석
- 매일 지정된 시간에 텔레그램 알림 전송
- 시그널 변경 시 알림
================================================================================
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import requests
import schedule
import time
import logging
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 설정
# ═══════════════════════════════════════════════════════════════════════════════

# 텔레그램 설정 (환경변수에서 로드)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 알림 시간 설정 (한국 시간 기준)
ALERT_TIMES = ['06:00']  # 매일 아침 6시

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
        self.stoch_config = {'period': 166, 'k_period': 57, 'd_period': 19}
        self.ma_periods = [20, 45, 151, 212]

    def get_data(self, days_back=400):
        """TQQQ 데이터 가져오기"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        try:
            ticker = yf.Ticker('TQQQ')
            data = ticker.history(start=start_date, end=end_date, auto_adjust=True)
            if data.empty:
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

    def analyze(self, data):
        """시그널 분석"""
        curr = data.iloc[-1]
        prev = data.iloc[-2]
        
        is_bullish = curr['%K'] > curr['%D']
        ma_signals = {p: curr['Close'] > curr[f'MA{p}'] for p in self.ma_periods}
        
        # 현재 비중 계산
        if is_bullish:
            tqqq_ratio = sum(ma_signals.values()) * 0.25
        else:
            tqqq_ratio = (int(ma_signals[20]) + int(ma_signals[45])) * 0.5
        
        cash_ratio = 1 - tqqq_ratio
        
        # 전일 비중
        prev_bullish = prev['%K'] > prev['%D']
        prev_ma = {p: prev['Close'] > prev[f'MA{p}'] for p in self.ma_periods}
        if prev_bullish:
            prev_tqqq = sum(prev_ma.values()) * 0.25
        else:
            prev_tqqq = (int(prev_ma[20]) + int(prev_ma[45])) * 0.5
        
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
    ma20 = '✅' if r['ma_signals'][20] else '❌'
    ma45 = '✅' if r['ma_signals'][45] else '❌'
    ma151 = '✅' if r['ma_signals'][151] else '❌'
    ma212 = '✅' if r['ma_signals'][212] else '❌'
    
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
   MA20: {ma20} | MA45: {ma45}
   MA151: {ma151} | MA212: {ma212}
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
        logger.error("데이터를 불러올 수 없습니다.")
        send_telegram("⚠️ TQQQ 알림 오류\n데이터를 불러올 수 없습니다.")
        return
    
    # 지표 계산
    data = analyzer.calculate_indicators(data)
    
    # 분석
    result = analyzer.analyze(data)
    
    # 알림 메시지 생성 및 전송
    message = create_alert_message(result)
    send_telegram(message)
    
    logger.info(f"알림 전송 완료 - TQQQ: {int(result['tqqq']*100)}%, 변동: {int(result['change']*100):+}%")

# ═══════════════════════════════════════════════════════════════════════════════
# 📌 메인
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 50)
    logger.info("TQQQ Sniper 텔레그램 알림 봇 시작")
    logger.info("=" * 50)
    
    # 시작 시 즉시 한 번 실행 (잘 돌아가는지 확인용)
    logger.info("시작 시 즉시 알림 전송...")
    send_tqqq_alert()
    
    # 스케줄 설정
    for alert_time in ALERT_TIMES:
        schedule.every().day.at(alert_time).do(send_tqqq_alert)
        logger.info(f"스케줄 등록: 매일 {alert_time}")
    
    logger.info("스케줄러 시작. Ctrl+C로 종료.")
    
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()