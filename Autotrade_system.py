# pip install -r .\requirements.txt

import os
import json
import logging
import requests
import base64
from datetime import datetime, timedelta
import time
from pymongo import MongoClient, DESCENDING
from typing import Dict, Any, Optional, Union, List
from pydantic import BaseModel
from dotenv import load_dotenv
import pyupbit
from openai import OpenAI
import pandas as pd
import numpy as np
import ta
import feedparser
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
import streamlit

load_dotenv()

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading.log', encoding='utf-8'),  # 인코딩 지정
        logging.StreamHandler()
    ]
)

def datetime_handler(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

def numpy_encoder(obj):
    """numpy 타입을 Python 기본 타입으로 변환"""
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        # percentage나 ratio가 포함된 키워드가 있으면 float 유지
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class MarketAnalyzer:
    """시장 데이터 분석 클래스"""
    def __init__(self):
        self.ticker = "KRW-BTC"

    def add_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """기술적 지표 추가"""
        try:
            # 이동평균선
            df['sma_20'] = ta.trend.sma_indicator(df['close'], window=20)
            df['sma_60'] = ta.trend.sma_indicator(df['close'], window=60)
            df['ema_20'] = ta.trend.ema_indicator(df['close'], window=20)
            
            # MACD
            macd = ta.trend.MACD(df['close'])
            df['macd_line'] = macd.macd()
            df['macd_signal'] = macd.macd_signal()
            df['macd_histogram'] = macd.macd_diff()

            # ADX
            adx_indicator = ta.trend.ADXIndicator(df['high'], df['low'], df['close'])
            df['adx'] = adx_indicator.adx()
            df['plus_di'] = adx_indicator.adx_pos()
            df['minus_di'] = adx_indicator.adx_neg()

            # RSI
            df['rsi'] = ta.momentum.RSIIndicator(df['close']).rsi()
            
            # Bollinger Bands
            bollinger = ta.volatility.BollingerBands(df['close'])
            df['bb_upper'] = bollinger.bollinger_hband()
            df['bb_middle'] = bollinger.bollinger_mavg()
            df['bb_lower'] = bollinger.bollinger_lband()
            
            # Volume Indicators
            df['volume_sma'] = ta.trend.sma_indicator(df['volume'], window=20)
            df['volume_ratio'] = df['volume'] / df['volume_sma']
            
            return df.ffill()
        except Exception as e:
            logging.error(f"Technical analysis failed: {str(e)}")
            return df

    def get_fear_greed_index(self) -> Optional[Dict]:
        """공포 탐욕 지수 조회"""
        try:
            response = requests.get("https://api.alternative.me/fng/?limit=1")
            data = response.json()
            return {
                'value': int(data['data'][0]['value']),
                'classification': data['data'][0]['value_classification']
            }
        except Exception as e:
            logging.error(f"Failed to get Fear & Greed Index: {str(e)}")
            return None

    def analyze_market(self, df: pd.DataFrame) -> Dict:
        """시장 분석 데이터 생성"""
        try:
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            analysis = {
                'price_action': {
                    'current_price': latest['close'],
                    'price_change': (latest['close'] - prev['close']) / prev['close'] * 100,
                    'volume_ratio': latest['volume_ratio']
                },
                'trend': {
                    'sma20_trend': 'bullish' if latest['sma_20'] > prev['sma_20'] else 'bearish',
                    'sma60_trend': 'bullish' if latest['sma_60'] > prev['sma_60'] else 'bearish',
                    'price_vs_sma20': 'above' if latest['close'] > latest['sma_20'] else 'below',
                    
                    'adx_strength': latest['adx'],
                    'trend_strength': 'strong' if latest['adx'] > 25 else 'weak',
                    'trend_direction': 'bullish' if latest['plus_di'] > latest['minus_di'] else 'bearish'
                },
                'momentum': {
                    'rsi': latest['rsi'],
                    'rsi_signal': 'oversold' if latest['rsi'] < 30 else 'overbought' if latest['rsi'] > 70 else 'neutral',
                    'macd': 'bullish' if latest['macd_histogram'] > 0 else 'bearish'
                },
                'volatility': {
                    'bb_position': (latest['close'] - latest['bb_lower']) / (latest['bb_upper'] - latest['bb_lower']),
                    'bb_width': (latest['bb_upper'] - latest['bb_lower']) / latest['bb_middle']
                }
            }
            
            return analysis
        except Exception as e:
            logging.error(f"Market analysis failed: {str(e)}")
            return {}
        
    def get_crypto_news(self) -> list:
        """비트코인 관련 뉴스 수집"""
        try:
            url = "https://news.google.com/rss/search?q=비트코인+가상화폐&hl=ko&gl=KR&ceid=KR:ko"
            feed = feedparser.parse(url)
            
            news_list = []
            for entry in feed.entries[:5]:
                news_list.append({
                    'title': entry.title.replace('\ufeff', ''),  # BOM 문자 제거
                    'date': entry.published,
                    'link': entry.link
                })

            return news_list
        except Exception as e:
            logging.error(f"Failed to fetch news: {str(e)}")
            return []
        
    def get_btc_chart_image(self) -> Optional[str]:
        try:
            options = webdriver.ChromeOptions()

            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--window-size=1920,1080')
            
            # GPU 관련 오류 해결을 위한 옵션
            options.add_argument('--disable-gpu')
            options.add_argument('--disable-software-rasterizer')
            options.add_argument('--disable-gpu-sandbox')
            options.add_argument('--disable-accelerated-2d-canvas')
            options.add_argument('--disable-accelerated-jpeg-decoding')
            options.add_argument('--disable-accelerated-mjpeg-decode')
            options.add_argument('--disable-accelerated-video-decode')
            options.add_argument('--disable-webgl')
            options.add_argument('--ignore-gpu-blocklist')
            
            # 헤드리스 모드 및 기타 옵션
            options.add_argument('--headless')
            options.add_argument('--disable-extensions')
            options.add_argument('--disable-popup-blocking')
            options.add_argument('--disable-infobars')
            
            # 로깅 레벨 설정
            options.add_argument('--log-level=3')  # WARNING 이상의 로그만 표시
            options.add_experimental_option('excludeSwitches', ['enable-logging'])  # 불필요한 로그 제외
            

            driver = webdriver.Chrome(options=options)
            driver.get("https://www.upbit.com/full_chart?code=CRIX.UPBIT.KRW-BTC")
            
            # 명시적 대기 설정
            wait = WebDriverWait(driver, 20)
            driver.set_page_load_timeout(30)

            # 페이지 완전 로드를 위한 대기
            time.sleep(5)
            try:
                driver.find_element('xpath', '//*[@id="fullChartiq"]/div/div/div[1]/div/div/cq-menu[1]/span/cq-clickable').click()
                time.sleep(1)
                driver.find_element('xpath', '//*[@id="fullChartiq"]/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[8]').click()
                time.sleep(1)

                driver.find_element('xpath', '//*[@id="fullChartiq"]/div/div/div[1]/div/div/cq-menu[3]/span').click()
                time.sleep(1)
                driver.find_element('xpath', '//*[@id="fullChartiq"]/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[1]').click()
                time.sleep(1)

                driver.find_element('xpath', '//*[@id="fullChartiq"]/div/div/div[1]/div/div/cq-menu[3]/span').click()
                time.sleep(1)
                driver.find_element('xpath', '//*[@id="fullChartiq"]/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[15]').click()
                time.sleep(1)
                
                # 스크린샷 저장
                screenshot_path = "btc_chart.png"
                driver.save_screenshot(screenshot_path)
                
                return screenshot_path
                
            except Exception as e:
                logging.error(f"Failed to add indicators: {str(e)}")
                screenshot_path = "btc_chart.png"
                driver.save_screenshot(screenshot_path)
                return screenshot_path
                
        except Exception as e:
            logging.error(f"Failed to capture chart image: {str(e)}")
            return None
        finally:
            if 'driver' in locals():
                driver.quit()

# 반성 데이터를 위한 Pydantic 모델 정의
class TradeMetrics(BaseModel):
    total_trades: int
    profitable_trades: int
    loss_trades: int
    avg_profit_percentage: float
    win_rate: float
    largest_profit: float
    largest_loss: float
    avg_holding_time: float

    def model_dump(self, **kwargs):
        return {
            "total_trades": self.total_trades,
            "profitable_trades": self.profitable_trades,
            "loss_trades": self.loss_trades,
            "avg_profit_percentage": self.avg_profit_percentage,
            "win_rate": self.win_rate,
            "largest_profit": self.largest_profit,
            "largest_loss": self.largest_loss,
            "avg_holding_time": self.avg_holding_time
        }

class DecisionMetrics(BaseModel):
    decision_type: str
    count: int
    success_rate: float
    avg_profit: float
    avg_confidence: float

    def model_dump(self, **kwargs):
        return {
            "decision_type": self.decision_type,
            "count": self.count,
            "success_rate": self.success_rate,
            "avg_profit": self.avg_profit,
            "avg_confidence": self.avg_confidence
        }

class MarketCondition(BaseModel):
    trend: str
    volatility: str
    volume: str
    correlation_with_success: float

    def model_dump(self, **kwargs):
        return {
            "trend": self.trend,
            "volatility": self.volatility,
            "volume": self.volume,
            "correlation_with_success": self.correlation_with_success
        }

class TradingPattern(BaseModel):
    pattern_type: str
    frequency: int
    success_rate: float
    avg_profit: float
    description: str

    def model_dump(self, **kwargs):
        return {
            "pattern_type": self.pattern_type,
            "frequency": self.frequency,
            "success_rate": self.success_rate,
            "avg_profit": self.avg_profit,
            "description": self.description
        }

class ReflectionInsight(BaseModel):
    category: str
    description: str
    importance: int  # 1-10
    action_items: List[str]

    def model_dump(self, **kwargs):
        return {
            "category": self.category,
            "description": self.description,
            "importance": self.importance,
            "action_items": self.action_items
        }

class AIReflection(BaseModel):
    timestamp: datetime
    performance_metrics: TradeMetrics
    decision_analysis: List[DecisionMetrics]
    market_conditions: List[MarketCondition]
    successful_patterns: List[TradingPattern]
    mistake_patterns: List[TradingPattern]
    insights: List[ReflectionInsight]
    confidence_adjustments: Dict[str, float]
    improvement_priorities: List[str]

    def model_dump(self, **kwargs):
        return {
            "timestamp": self.timestamp,
            "performance_metrics": self.performance_metrics.model_dump(),
            "decision_analysis": [d.model_dump() for d in self.decision_analysis],
            "market_conditions": [c.model_dump() for c in self.market_conditions],
            "successful_patterns": [p.model_dump() for p in self.successful_patterns],
            "mistake_patterns": [p.model_dump() for p in self.mistake_patterns],
            "insights": [i.model_dump() for i in self.insights],
            "confidence_adjustments": self.confidence_adjustments,
            "improvement_priorities": self.improvement_priorities
        }

class DB_Feedback:
    def __init__(self):
        """MongoDB 연결 초기화"""
        load_dotenv()
        self.client = MongoClient(os.getenv("MONGO_URL"))
        self.db = self.client['auto_trading']
        self.collection = self.db['trade_records']
        self.reflection_collection = self.db['trade_reflections']
        self.reflection_collection.create_index([("timestamp", DESCENDING)])
        self.collection.create_index([("timestamp", DESCENDING)])
            
    def get_recent_records(self, limit: int = 10) -> list:
        """최근 매매 기록 조회"""
        try:
            records = list(self.collection.find(
                {},
                {'_id': 0}  # _id 필드 제외
            ).sort('timestamp', DESCENDING).limit(limit))
            return records
            
        except Exception as e:
            logging.error(f"Failed to get recent records: {str(e)}")
            return []
            
    def get_trading_summary(self) -> Dict[str, Any]:
        """매매 요약 정보 조회"""
        try:
            pipeline = [
                {
                    '$group': {
                        '_id': None,
                        'total_trades': {'$sum': 1},
                        'buy_count': {
                            '$sum': {'$cond': [{'$eq': ['$decision', 'buy']}, 1, 0]}
                        },
                        'sell_count': {
                            '$sum': {'$cond': [{'$eq': ['$decision', 'sell']}, 1, 0]}
                        },
                        'hold_count': {
                            '$sum': {'$cond': [{'$eq': ['$decision', 'hold']}, 1, 0]}
                        },
                        'avg_btc_price': {'$avg': '$btc_krw_price'},
                        'last_trade_time': {'$max': '$timestamp'}
                    }
                }
            ]
            
            summary = list(self.collection.aggregate(pipeline))
            return summary[0] if summary else {}
            
        except Exception as e:
            logging.error(f"Failed to get trading summary: {str(e)}")
            return {}
    
    def analyze_trade_performance(self, trade_record):
        """개별 거래의 성과 분석"""
        try:
            # 거래 후 24시간 이내의 최고/최저 가격 조회
            next_day_data = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=24, 
                                             to=trade_record['timestamp'] + timedelta(days=1))
            
            performance_metrics = {
                'max_potential_profit': (next_day_data['high'].max() - trade_record['btc_krw_price']) / trade_record['btc_krw_price'] * 100,
                'max_potential_loss': (next_day_data['low'].min() - trade_record['btc_krw_price']) / trade_record['btc_krw_price'] * 100,
                'actual_profit': None  # 다음 거래 기록에서 계산
            }
            
            return performance_metrics
        except Exception as e:
            logging.error(f"Failed to analyze trade performance: {str(e)}")
            return None

    def calculate_trade_metrics(self, trades: list) -> TradeMetrics:
        """거래 지표 계산"""
        total_trades = len(trades)
        profitable_trades = sum(1 for trade in trades if trade.get('profit', 0) > 0)
        loss_trades = sum(1 for trade in trades if trade.get('profit', 0) < 0)
        
        profits = [trade.get('profit', 0) for trade in trades]
        avg_profit = sum(profits) / len(profits) if profits else 0
        
        return TradeMetrics(
            total_trades=total_trades,
            profitable_trades=profitable_trades,
            loss_trades=loss_trades,
            avg_profit_percentage=avg_profit,
            win_rate=profitable_trades / total_trades if total_trades > 0 else 0,
            largest_profit=max(profits) if profits else 0,
            largest_loss=min(profits) if profits else 0,
            avg_holding_time=0  # 실제 구현에서는 거래 시간 차이 계산 필요
        )

    def analyze_decisions(self, trades: list) -> List[DecisionMetrics]:
        """결정별 분석"""
        decision_data = {}
        
        for trade in trades:
            decision = trade['decision']
            if decision not in decision_data:
                decision_data[decision] = {
                    'count': 0,
                    'profits': [],
                    'confidences': []
                }
                
            data = decision_data[decision]
            data['count'] += 1
            data['profits'].append(trade.get('profit', 0))
            data['confidences'].append(trade['confidence'])
            
        return [
            DecisionMetrics(
                decision_type=decision,
                count=data['count'],
                success_rate=len([p for p in data['profits'] if p > 0]) / data['count'],
                avg_profit=sum(data['profits']) / len(data['profits']),
                avg_confidence=sum(data['confidences']) / len(data['confidences'])
            )
            for decision, data in decision_data.items()
        ]

    def self_reflection(self) -> Optional[AIReflection]:
        """AI 기반 거래 분석 및 자기 성찰"""
        try:
            # 최근 30일간의 거래 기록 조회
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30)
            
            recent_trades = list(self.collection.find({
                'timestamp': {'$gte': start_date, '$lte': end_date}
            }).sort('timestamp', DESCENDING))

            if not recent_trades:
                logging.info("No recent trades found for reflection")
                return None

            # 1. 기본 지표 계산
            trade_metrics = self.calculate_trade_metrics(recent_trades)
            decision_metrics = self.analyze_decisions(recent_trades)

            # 2. AI 분석 요청을 위한 데이터 준비
            analysis_data = {
                "trade_metrics": trade_metrics.model_dump(),
                "decision_metrics": [metric.model_dump() for metric in decision_metrics],
                "trade_history": recent_trades
            }

            # 3. OpenAI API를 통한 분석 요청
            client = OpenAI(api_key=os.getenv("API_KEY"))
            prompt = f"""
            You are a cryptocurrency trading expert. Please analyse the following trading data and provide detailed insights:

            Trading indicators:
            {json.dumps(analysis_data['trade_metrics'], indent=2)}

            Analysis by decision:
            {json.dumps(analysis_data['decision_metrics'], indent=2)}

            Please respond in JSON format:
            {{
                "market_conditions": [
                    {{
                        "trend": string,
                        "volatility": string,
                        "volume": string,
                        "correlation_with_success": float
                    }}
                ],
                "successful_patterns": [
                    {{
                        "pattern_type": string,
                        "frequency": integer,
                        "success_rate": float,
                        "avg_profit": float,
                        "description": string
                    }}
                ],
                "mistake_patterns": [Same format],
                "insights": [
                    {{
                        "category": string,
                        "description": string,
                        "importance": integer (1-10),
                        "action_items": [string]
                    }}
                ],
                "confidence_adjustments": {{
                    "buy": float,
                    "sell": float,
                    "hold": float
                }},
                "improvement_priorities": [string]
            }}
            """

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )

            ai_analysis = json.loads(response.choices[0].message.content)

            # 4. 반성 데이터 생성
            reflection = AIReflection(
                timestamp=datetime.now(),
                performance_metrics=trade_metrics,
                decision_analysis=decision_metrics,
                market_conditions=[MarketCondition(**condition) for condition in ai_analysis['market_conditions']],
                successful_patterns=[TradingPattern(**pattern) for pattern in ai_analysis['successful_patterns']],
                mistake_patterns=[TradingPattern(**pattern) for pattern in ai_analysis['mistake_patterns']],
                insights=[ReflectionInsight(**insight) for insight in ai_analysis['insights']],
                confidence_adjustments=ai_analysis['confidence_adjustments'],
                improvement_priorities=ai_analysis['improvement_priorities']
            )

            # 5. MongoDB에 저장
            self.reflection_collection.insert_one(reflection.model_dump())
            logging.info("Self-reflection completed and saved to database")

            return reflection

        except Exception as e:
            logging.error(f"Self-reflection failed: {str(e)}", exc_info=True)
            return None

    def get_reflection_history(self, limit: int = 10) -> List[AIReflection]:
        """반성 기록 조회"""
        try:
            reflections = list(self.reflection_collection.find(
                {},
                {'_id': 0}
            ).sort('timestamp', DESCENDING).limit(limit))
            
            return [AIReflection(**reflection) for reflection in reflections]
        except Exception as e:
            logging.error(f"Failed to get reflection history: {str(e)}")
            return []

    def get_reflection_history(self, limit: int = 10):
        """과거 반성 기록 조회"""
        try:
            return list(self.reflection_collection.find(
                {},
                {'_id': 0}
            ).sort('timestamp', DESCENDING).limit(limit))
        except Exception as e:
            logging.error(f"Failed to get reflection history: {str(e)}")
            return []

    def save_trade_record(self, decision, upbit):
        """거래 기록 저장"""
        try:
            # MarketAnalyzer 인스턴스 생성
            market_analyzer = MarketAnalyzer()
            
            # 현재 시장 데이터 수집 및 분석
            df = pyupbit.get_ohlcv("KRW-BTC", count=30, interval="day")
            df = market_analyzer.add_technical_indicators(df)
            market_analysis = market_analyzer.analyze_market(df)

            market_analysis = json.loads(json.dumps(market_analysis, default=numpy_encoder))
            # 현재 가격 및 잔고 정보
            current_price = int(pyupbit.get_current_price("KRW-BTC"))
            krw_balance = int(upbit.get_balance("KRW"))
            btc_balance = float(upbit.get_balance("KRW-BTC"))
            
            # 이전 거래 기록 조회하여 수익률 계산
            previous_record = self.collection.find_one(
                sort=[('timestamp', -1)]
            )
            
            profit = None
            if previous_record:
                previous_price = previous_record['btc_krw_price']
                if previous_price:
                    profit = ((current_price - previous_price) / previous_price) * 100
            
            # 거래 기록 생성
            record = {
                "timestamp": datetime.now(),
                "decision": decision.decision,
                "percentage": int(decision.percentage),
                "reason": decision.reason,
                "confidence": int(decision.confidence),
                "btc_krw_price": current_price,
                "krw_balance": krw_balance,
                "btc_balance": btc_balance,
                "profit": profit,
                "market_analysis": market_analysis  # MarketAnalyzer의 분석 결과를 직접 사용
            }
            
            self.collection.insert_one(record)
            logging.info(f"Trade record saved successfully")
            # logging.info(f"Trade record saved successfully: {record}")
            
        except Exception as e:
            logging.error(f"Failed to save trade record: {str(e)}")

    def close(self):
        """MongoDB 연결 종료"""
        try:
            self.client.close()
        except Exception as e:
            logging.error(f"Failed to close MongoDB connection: {str(e)}")


from enum import Enum
from pydantic import BaseModel

class DecisionType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"

class AIDecision(BaseModel):
    decision: DecisionType
    percentage: float
    reason: str
    confidence: float

class AITrader:
    def __init__(self):
        load_dotenv()
        self.client = OpenAI(api_key=os.getenv("API_KEY"))
        self.access = os.getenv("UPBIT_ACCESS_KEY")
        self.secret = os.getenv("UPBIT_SECRET_KEY")
        self.upbit = pyupbit.Upbit(self.access, self.secret)
        self.ticker = "KRW-BTC"
        self.analyzer = MarketAnalyzer()
            
    def get_market_data(self):
        """시장 데이터 수집"""
        try:
            df = pyupbit.get_ohlcv(self.ticker, count=30, interval="day")
            if df is None or df.empty:
                raise ValueError("Market data is empty")
            return self.analyzer.add_technical_indicators(df)
        except Exception as e:
            logging.error(f"Failed to get market data: {str(e)}")
            return None
            
    def get_ai_decision(self, df, market_analysis, fear_greed):
        """AI 분석 요청"""
        try:
            db_feedback = DB_Feedback()
            # 거래 성과 메트릭 계산
            recent_trades = db_feedback.get_recent_records(limit=30)
            trade_metrics = {
                'profitable_trades': sum(1 for trade in recent_trades if trade.get('profit', 0) > 0),
                'total_trades': len(recent_trades),
                'avg_profit': sum(trade.get('profit', 0) for trade in recent_trades) / len(recent_trades) if recent_trades else 0,
                'win_rate': sum(1 for trade in recent_trades if trade.get('profit', 0) > 0) / len(recent_trades) if recent_trades else 0
            }

            profitable_trades = [trade for trade in recent_trades if trade.get('profit', 0) > 0]
            successful_conditions = []
            if profitable_trades:
                for trade in profitable_trades:
                    if 'market_analysis' in trade:
                        successful_conditions.append({
                            'price_action': trade['market_analysis'].get('price_action', {}),
                            'trend': trade['market_analysis'].get('trend', {}),
                            'momentum': trade['market_analysis'].get('momentum', {})
                        })
            
            news_data = self.analyzer.get_crypto_news()
            chart_image_path = self.analyzer.get_btc_chart_image()
            
            recent_reflections = db_feedback.get_reflection_history(limit=1)
            reflection_data = {
                'success_patterns': [],
                'mistake_patterns': [],
                'recommendations': [],
                'confidence_adjustments': {
                    'buy': 0.0,
                    'sell': 0.0,
                    'hold': 0.0
                }
            }
            
            # 반성 기록이 있는 경우에만 데이터 업데이트
            if recent_reflections:
                reflection = recent_reflections[0]
                if 'ai_reflection' in reflection:
                    reflection_data.update({
                        'success_patterns': reflection['ai_reflection'].get('success_patterns', []),
                        'mistake_patterns': reflection['ai_reflection'].get('mistake_patterns', []),
                        'recommendations': reflection['ai_reflection'].get('recommendations', []),
                        'confidence_adjustments': reflection['ai_reflection'].get('confidence_adjustments', {
                            'buy': 0.0,
                            'sell': 0.0,
                            'hold': 0.0
                        })
                    })

            base64_image = ""
            if chart_image_path:
                with open(chart_image_path, "rb") as image_file:
                    base64_image = base64.b64encode(image_file.read()).decode('utf-8')

            # AI에게 제공할 데이터 준비
            analysis_data = {
                'chart_data': df.to_dict(orient='records')[-5:],  # 최근 5일 데이터
                'technical_analysis': market_analysis,
                'fear_greed_index': fear_greed,
                'recent_news': news_data,
                'current_time': datetime.now().isoformat(),
                'reflection_insights': {
                    'success_patterns': reflection_data['success_patterns'],
                    'mistake_patterns': reflection_data['mistake_patterns'],
                    'recommendations': reflection_data['recommendations']
                },
                'trading_history': {
                    'metrics': trade_metrics,
                    'recent_trades': recent_trades[-5:],  # 최근 5개 거래만
                    'successful_patterns': successful_conditions,
                    'market_conditions': [
                        trade.get('market_analysis', {}) for trade in recent_trades[-5:]
                    ]
                },
                'performance_trend': {
                    'daily_profits': [trade.get('profit', 0) for trade in recent_trades],
                    'confidence_history': [trade.get('confidence', 0) for trade in recent_trades]
                }
            }
            
            prompt = (
                "You're a bitcoin investment expert and you want to trade short-term, with a target of at least 0.7% profit per day. "
                "Use technical indicators, but give more weight to the appearance of the underlying candlestick chart to guide your decisions.\n\n"

                 f"Recent Trading Performance:\n"
                f"- Win Rate: {trade_metrics['win_rate']:.2%}\n"
                f"- Average Profit: {trade_metrics['avg_profit']:.2%}\n"
                f"- Recent Success Patterns: {json.dumps(successful_conditions, indent=2)}\n\n"

                "Consider the following insights from past trades:\n"
                f"Success Patterns: {json.dumps(reflection_data['success_patterns'], indent=2)}\n"
                f"Common Mistakes to Avoid:{json.dumps(reflection_data['mistake_patterns'], indent=2)}\n"
                f"Key Recommendations: {json.dumps(reflection_data['recommendations'], indent=2)}\n"

                "Apply these lessons to your current analysis:\n"
                "1. Technical indicators (MACD, RSI, Bollinger Bands)\n"
                "2. Market sentiment (Fear & Greed Index)\n"
                "3. Price action and volume\n"
                "4. recent news headlines\n"
                "5. Overall market trends\n"
                "6. BTC Chart image\n\n"
                "For your response:\n"
                "- Decision must be one of: 'buy', 'sell', 'hold'\n"
                "- Provide detailed analysis reasoning, summarizing why in near 20 words.\n"
                "- Specify the percentage of available assets to trade:\n"
                "  * For BUY: What percentage of available KRW to spend (0-100)\n"
                "  * For SELL: What percentage of held BTC to sell (0-100)\n"
                "  * For HOLD: Must be 0\n"
                """Note: Adjust your base confidence levels by:
Buy: {confidence_adjustments['buy']}
Sell: {confidence_adjustments['sell']}
Hold: {confidence_adjustments['hold']}
"""
                "response in json format:"
                "{\"decision\": \"[buy/sell/hold]\", \"percentage\": [0-100], \"reason\": \"[your detailed analysis]\", \"confidence\": [0-100]}"
            )

            # 메시지 구성
            AImessage = [
                {"role": "system", "content": prompt},
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": json.dumps(analysis_data, default=datetime_handler)}
                    ]
                }
            ]
            
            # 이미지가 있으면 메시지에 추가
            if base64_image:
                AImessage[1]["content"].append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}",
                        "detail": "high"
                    }
                })

            response = self.client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=AImessage,
                response_format=AIDecision,
                temperature=0.7,
                max_tokens=512
            )
            decision_data = json.loads(response.choices[0].message.content)

            decision = AIDecision(
                decision=decision_data['decision'],
                percentage=decision_data['percentage'],
                reason=decision_data['reason'],
                confidence=decision_data['confidence']
            )

            decision.confidence *= (1 + reflection_data['confidence_adjustments'][decision.decision])

            return decision

        except Exception as e:
            logging.error(f"AI analysis with reflection failed: {str(e)}")
            return None

    def execute_trade(self, decision):
        """매매 실행"""
        try:
            db_feedback = DB_Feedback()

            # percentage 유효성 검사 
            if not (0 <= decision.percentage <= 100):
                logging.error(f"Invalid percentage value: {decision.percentage}")
                return
                
            current_price = pyupbit.get_orderbook(ticker=self.ticker)['orderbook_units'][0]["ask_price"]

            if decision.decision == DecisionType.BUY:
                my_krw = self.upbit.get_balance("KRW")
                trade_amount = my_krw * (decision.percentage / 100) * 0.9995  # 수수료 고려
                
                if trade_amount > 5000:
                    # 실제 매매를 원할 경우 주석 해제
                    order = self.upbit.buy_market_order(self.ticker, trade_amount)
                    logging.info(f"Buy order executed: {order}")
                    logging.info(f"Buy signal generated for {decision.percentage}% of KRW")
                    logging.info(f"Amount: {trade_amount} KRW")
                    logging.info(f"Reason: {decision.reason}")
                else:
                    logging.warning("Buy order failed: Insufficient funds (min: 5,000 KRW)")                    
            
            elif decision.decision == DecisionType.SELL:
                my_btc = self.upbit.get_balance(self.ticker)
                trade_amount = my_btc * (decision.percentage / 100)
                value = trade_amount * current_price
                
                if value > 5000:
                    # 실제 매매를 원할 경우 주석 해제
                    order = self.upbit.sell_market_order(self.ticker, my_btc)
                    logging.info(f"Sell order executed: {order}")
                    logging.info(f"Sell signal generated for {decision.percentage}% of BTC")
                    logging.info(f"Amount: {trade_amount} BTC")
                    logging.info(f"Value: {value} KRW")
                    logging.info(f"Reason: {decision.reason}")
                else:
                    logging.warning("Sell order failed: Insufficient coin amount")
                    
            elif decision.decision == DecisionType.HOLD:
                logging.info(f"Decision: HOLD {decision.percentage}% of current position")
                logging.info(f"Reason: {decision.reason}")

            else:
                logging.warning(f"Undefined decision: {decision.decision}")

            db_feedback.save_trade_record(decision, self.upbit)
            db_feedback.close()

        except Exception as e:
            logging.error(f"Trade execution failed: {str(e)}")

    def run(self):
        """트레이딩 실행"""
        try:
            # 1. 시장 데이터 수집
            df = self.get_market_data()
            if df is None:
                return
                
            # 2. 시장 분석 수행
            market_analysis = self.analyzer.analyze_market(df)
            
            # 3. 공포 탐욕 지수 조회
            fear_greed = self.analyzer.get_fear_greed_index()

            # 4. 자기 성찰 수행
            db_feedback = DB_Feedback()
            reflection = db_feedback.self_reflection()
            if reflection:
                logging.info("Self-reflection completed successfully")
                logging.info(f"Key insights: {reflection.insights}")
        
            # 5. AI 분석
            decision = self.get_ai_decision(df, market_analysis, fear_greed)
            if decision is None:
                return
                
            # 6. 결과 출력
            logging.info(f"AI DECISION: {decision.decision.upper()}")
            logging.info(f"percentage: {decision.percentage}")
            logging.info(f"REASON: {decision.reason}")
            logging.info(f"confidence: {decision.confidence}")
            logging.info(f"Market Analysis: {market_analysis}")
            logging.info(f"Fear & Greed Index: {fear_greed}")
            logging.info("Recent News Headlines:")
            for news in self.analyzer.get_crypto_news():
                logging.info(f"- {news['title']}")

            # 7. 매매 실행
            self.execute_trade(decision)
            
        except Exception as e:
            logging.error(f"Trading cycle failed: {str(e)}")

def main():
    """메인 함수"""
    trader = AITrader()
    interval = 3600
    
    logging.info("Enhanced AI Trading Bot Started")
    
    while True:
        try:
            trader.run()
            time.sleep(interval)
        except KeyboardInterrupt:
            logging.info("AI Trading Bot Stopped")
            break
        except Exception as e:
            logging.error(f"Unexpected error: {str(e)}")
            time.sleep(interval/12)

if __name__ == "__main__":
    main()

# def test_ai_trader():
#     trader = AITrader()
#     try:
#         # 단일 사이클 실행
#         trader.run()
#         logging.info("Test completed successfully")
        
#     except Exception as e:
#         logging.error(f"Test failed: {str(e)}")

# if __name__ == "__main__":
#     logging.info("Starting test run...")
#     test_ai_trader()

    
