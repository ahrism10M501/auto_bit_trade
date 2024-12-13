# pip install -r .\requirements.txt

import os
import json
import logging
from logging.handlers import RotatingFileHandler
import requests
import base64
from datetime import datetime, timedelta
import time
from pymongo import MongoClient, DESCENDING
from typing import Dict, Any, Optional, List
from pydantic import BaseModel
from dotenv import load_dotenv
import pyupbit
from openai import OpenAI
import pandas as pd
import numpy as np
import ta
import feedparser
from selenium import webdriver
import schedule
import asyncio
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from dataclasses import dataclass
from contextlib import asynccontextmanager

load_dotenv()









class LoggerSetup:
    def __init__(self, log_dir='logs'):
        self.log_dir = log_dir
        self.ensure_log_directory()
        self.setup_logging()

    def ensure_log_directory(self):
        """로그 디렉토리가 없으면 생성"""
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

    def setup_logging(self):
        """로깅 설정"""
        # 로그 파일명에 날짜 포함
        log_file = os.path.join(self.log_dir, f'trading_{datetime.now().strftime("%Y%m")}.log')
        
        # 로그 포맷 설정
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # RotatingFileHandler 설정 (10MB 제한, 최대 5개 파일)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)

        # 스트림 핸들러 설정
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)

        # 루트 로거 설정
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        
        # 기존 핸들러 제거
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            
        # 새 핸들러 추가
        root_logger.addHandler(file_handler)
        root_logger.addHandler(stream_handler)

# 로깅 설정 초기화
logger_setup = LoggerSetup()

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

# 전역 상태 관리를 위한 클래스
class OperationType(Enum):
    SCHEDULED = "SCHEDULED"
    EMERGENCY = "EMERGENCY"
    NONE = "NONE"

@dataclass
class TradingOperation:
    type: OperationType
    start_time: datetime
    max_duration: timedelta

class TradingState:
    def __init__(self):
        self._state_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._trading_event = asyncio.Event()
        self._is_trading = False
        self._emergency_mode = False
        self._current_operation: Optional[TradingOperation] = None
        self._operation_timeout = timedelta(minutes=5)
        self.logger = logging.getLogger(__name__)

    @property
    def is_trading(self) -> bool:
        return self._is_trading

    @property
    def emergency_mode(self) -> bool:
        return self._emergency_mode

    @property
    def current_operation(self) -> Optional[TradingOperation]:
        return self._current_operation

    async def _check_operation_timeout(self):
        """Check if current operation has exceeded its maximum duration"""
        if (self._current_operation and 
            datetime.now() - self._current_operation.start_time > self._current_operation.max_duration):
            self.logger.warning(f"Operation timeout: {self._current_operation.type}")
            await self.force_reset()

    async def force_reset(self):
        """Force reset all states in case of emergency"""
        async with self._state_lock:
            self._is_trading = False
            self._emergency_mode = False
            self._current_operation = None
            self._trading_event.clear()
            self.logger.info("Trading state forcefully reset")

    @asynccontextmanager
    async def trading_session(self, operation_type: OperationType, max_duration: timedelta = None):
        """Context manager for trading operations"""
        if not await self.start_trading(operation_type, max_duration):
            yield False
            return

        try:
            yield True
        except Exception as e:
            self.logger.error(f"Error in trading session: {e}")
            raise
        finally:
            await self.end_trading()

    async def start_trading(self, operation_type: OperationType, max_duration: timedelta = None) -> bool:
        """Start a trading operation with timeout"""
        async with self._state_lock:
            await self._check_operation_timeout()
            
            if self._is_trading:
                self.logger.warning(f"Trading already in progress: {self._current_operation}")
                return False

            self._is_trading = True
            self._current_operation = TradingOperation(
                type=operation_type,
                start_time=datetime.now(),
                max_duration=max_duration or self._operation_timeout
            )
            self._trading_event.set()
            
            self.logger.info(f"Started trading operation: {operation_type}")
            return True

    async def end_trading(self):
        """End current trading operation"""
        async with self._state_lock:
            self._is_trading = False
            self._current_operation = None
            self._trading_event.clear()
            self.logger.info("Trading operation ended")

    async def set_emergency_mode(self, enabled: bool):
        """Safely set emergency mode"""
        async with self._state_lock:
            if self._emergency_mode == enabled:
                return
            
            self._emergency_mode = enabled
            self.logger.info(f"Emergency mode {'enabled' if enabled else 'disabled'}")

    async def wait_for_trading_completion(self, timeout: float = None) -> bool:
        """Wait for current trading operation to complete"""
        try:
            await asyncio.wait_for(self._trading_event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            self.logger.warning("Timeout waiting for trading completion")
            return False

# 전역 상태 객체 생성
TRADING_STATE = TradingState()

















class MarketAnalyzer:
    """시장 데이터 분석 클래스"""
    def __init__(self):
        self.ticker = "KRW-BTC"
        self.logger = logging.getLogger(__name__)

    def validate_dataframe(self, df: pd.DataFrame) -> bool:
        """데이터프레임 유효성 검사"""
        required_columns = ['open', 'high', 'low', 'close', 'volume']
        if df is None or df.empty:
            self.logger.error("Empty dataframe received")
            return False
        
        if not all(col in df.columns for col in required_columns):
            self.logger.error(f"Missing required columns. Required: {required_columns}")
            return False
            
        if df.shape[0] < 2:
            self.logger.error("Insufficient data points")
            return False
            
        return True
    
    def add_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """기술적 지표 추가"""
        try:
            
            if not self.validate_dataframe(df):
                return df

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
            
            if not self.validate_dataframe(df):
                return self.get_default_analysis()

            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            analysis = {
                'price_action': {
                    'current_price': latest['close'],
                    'price_change': (latest['close'] - prev['close']) / prev['close'] * 100,
                    'volume': latest['volume'],
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
        
    def get_default_analysis(self) -> Dict:
        """기본 분석 결과 반환"""
        return {
            'price_action': {
                'current_price': 0,
                'price_change': 0,
                'volume_ratio': 1.0
            },
            'trend': {
                'sma20_trend': 'neutral',
                'sma60_trend': 'neutral',
                'price_vs_sma20': 'neutral',
                'adx_strength': 0,
                'trend_strength': 'weak',
                'trend_direction': 'neutral'
            },
            'momentum': {
                'rsi': 50,
                'rsi_signal': 'neutral',
                'macd': 'neutral'
            },
            'volatility': {
                'bb_position': 0.5,
                'bb_width': 1.0
            }
        }
        
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

class DecisionMetrics(BaseModel):
    decision_type: str
    count: int
    success_rate: float
    avg_profit: float
    avg_confidence: float

class MarketCondition(BaseModel):
    trend: str
    volatility: str
    volume: str
    correlation_with_success: float

class TradingPattern(BaseModel):
    pattern_type: str
    frequency: int
    success_rate: float
    avg_profit: float
    description: str

class ReflectionInsight(BaseModel):
    category: str
    description: str
    importance: int  # 1-10
    action_items: List[str]

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

    class Config:
        from_attributes = True

class DB_Feedback:
    def __init__(self):
        """MongoDB 연결 초기화"""
        load_dotenv()
        self._client = None
        self._db = None
        self._collection = None
        self._reflection_collection = None
        self.connect()

    def connect(self):
        """데이터베이스 연결 수립"""
        try:
            if self._client is not None:
                self.close()
                
            self._client = MongoClient(
                os.getenv("MONGO_URL"),
                serverSelectionTimeoutMS=5000  # 5초 연결 타임아웃
            )
            # 연결 테스트
            self._client.server_info()
            
            self._db = self._client['auto_trading']
            self._collection = self._db['trade_records']
            self._reflection_collection = self._db['trade_reflections']
            
            # 인덱스 생성
            self._reflection_collection.create_index([("timestamp", DESCENDING)])
            self._collection.create_index([("timestamp", DESCENDING)])
            
            logging.info("Successfully connected to MongoDB")
        except Exception as e:
            logging.error(f"Failed to connect to MongoDB: {str(e)}")
            raise

    def ensure_connection(self):
        """연결 상태 확인 및 재연결"""
        try:
            # 연결 상태 확인
            self._client.server_info()
        except Exception as e:
            logging.warning(f"MongoDB connection lost: {str(e)}")
            self.connect()
            
    def get_recent_records(self, limit: int = 10) -> list:
        """최근 매매 기록 조회"""
        try:
            self.ensure_connection()
            records = list(self._collection.find(
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
            self.ensure_connection()
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
            
            summary = list(self._collection.aggregate(pipeline))
            return summary[0] if summary else {}
            
        except Exception as e:
            logging.error(f"Failed to get trading summary: {str(e)}")
            return {}
    
    def analyze_trade_performance(self, trade_record):
        """개별 거래의 성과 분석"""
        try:
            self.ensure_connection()
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
        if not trades:
            return TradeMetrics(
                total_trades=0,
                profitable_trades=0,
                loss_trades=0,
                avg_profit_percentage=0.0,
                win_rate=0.0,
                largest_profit=0.0,
                largest_loss=0.0,
                avg_holding_time=0.0
            )

        total_trades = len(trades)
        profitable_trades = sum(1 for trade in trades if trade.get('profit', 0) > 0)
        loss_trades = sum(1 for trade in trades if trade.get('profit', 0) < 0)
        
        profits = [trade.get('profit', 0) for trade in trades]
        holding_times = []
        
        # 거래 보유 시간 계산
        buy_time = None
        for trade in trades:
            if trade['decision'] == 'buy':
                buy_time = trade['timestamp']
            elif trade['decision'] == 'sell' and buy_time is not None:
                sell_time = trade['timestamp']
                holding_time = (sell_time - buy_time).total_seconds() / 3600  # 시간 단위로 변환
                holding_times.append(holding_time)
                buy_time = None
        
        avg_profit = sum(profits) / len(profits) if profits else 0
        avg_holding = sum(holding_times) / len(holding_times) if holding_times else 0
        
        return TradeMetrics(
            total_trades=total_trades,
            profitable_trades=profitable_trades,
            loss_trades=loss_trades,
            avg_profit_percentage=avg_profit,
            win_rate=profitable_trades / total_trades if total_trades > 0 else 0,
            largest_profit=max(profits) if profits else 0,
            largest_loss=min(profits) if profits else 0,
            avg_holding_time=avg_holding  # 시간 단위로 평균 보유 시간 저장
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
        self.ensure_connection()
        try:
            # 최근 30일간의 거래 기록 조회
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30)
            
            recent_trades = list(self._collection.find({
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
            self._reflection_collection.insert_one(reflection.model_dump())
            logging.info("Self-reflection completed and saved to database")

            return reflection

        except Exception as e:
            logging.error(f"Self-reflection failed: {str(e)}", exc_info=True)
            return None

    def get_reflection_history(self, limit: int = 10) -> List[AIReflection]:
        """반성 기록 조회"""
        try:
            self.ensure_connection()
            reflections = list(self._reflection_collection.find(
                {},
                {'_id': 0}
            ).sort('timestamp', DESCENDING).limit(limit))
            
            return [AIReflection(**reflection) for reflection in reflections]
        except Exception as e:
            logging.error(f"Failed to get reflection history: {str(e)}")
            return []

    def get_initial_investment(self, start_date=None):
        """특정 시점 또는 최초 거래 시점의 총 자산 가치 조회"""
        try:
            self.ensure_connection()
            query = {}
            if start_date:
                query['timestamp'] = {'$gte': start_date}
                
            # 가장 오래된(또는 지정된 시점 이후) 거래 기록 조회
            first_record = self._collection.find_one(
                query,
                sort=[('timestamp', 1)]  # 오름차순 정렬
            )
            
            if first_record:
                initial_value = first_record['krw_balance'] + \
                            (first_record['btc_balance'] * first_record['btc_krw_price'])
                return initial_value
                
            return 0  # 기록이 없는 경우
        except Exception as e:
            logging.error(f"Failed to get initial investment: {str(e)}")
            return 0

    def save_trade_record(self, decision, upbit):
        """거래 기록 저장"""
        try:
            self.ensure_connection()
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

            current_total_value = krw_balance + (btc_balance * current_price)
            initial_investment = self.get_initial_investment() 
            # 전체 수익률 계산
            profit = ((current_total_value - initial_investment) / initial_investment) * 100 if initial_investment > 0 else 0
            
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
                "market_analysis": market_analysis
            }
            
            # self.collection을 self._collection으로 수정
            self._collection.insert_one(record)
            logging.info(f"Trade record saved successfully")
            # logging.info(f"Trade record saved successfully: {record}")
            
        except Exception as e:
            logging.error(f"Failed to save trade record: {str(e)}")

    def close(self):
        """MongoDB 연결 종료"""
        try:
            if self._client is not None:
                self._client.close()
                self._client = None
                self._db = None
                self._collection = None
                self._reflection_collection = None
                logging.info("MongoDB connection closed")
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
            
            retry_count = 3
            while retry_count > 0 and (df is None or df.empty):
                asyncio.sleep(1)
                df = pyupbit.get_ohlcv(self.ticker, count=30, interval="day")
                retry_count -= 1

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













class TradingScheduler:
    def __init__(self):
        self.trading_times = {
            "10:00": datetime.strptime("10:00", "%H:%M").time(),
            "11:00": datetime.strptime("11:00", "%H:%M").time(),
            "17:00": datetime.strptime("17:00", "%H:%M").time(),
            "18:00": datetime.strptime("18:00", "%H:%M").time(),
            "22:00": datetime.strptime("22:00", "%H:%M").time(),
            "23:00": datetime.strptime("23:00", "%H:%M").time()
        }
        self.monitor = MarketEmergencyMonitor()
        self.stop_event = asyncio.Event()
        self.trader = AITrader()
        self.loop = asyncio.get_event_loop()
        self.executor = ThreadPoolExecutor(max_workers=3)
        self._last_run_times = {}  # 각 시간대별 마지막 실행 시간 저장
        self._running_tasks = set()

    async def start(self):
        """스케줄러 및 모니터링 시작"""
        try:
            # 모니터링 시작
            monitor_task = asyncio.create_task(self.monitor.start_monitoring())
            scheduler_task = asyncio.create_task(self._run_scheduler())

            self._running_tasks.add(monitor_task)
            self._running_tasks.add(scheduler_task)
            
            # 태스크 완료 시 set에서 제거
            for task in (monitor_task, scheduler_task):
                task.add_done_callback(self._running_tasks.discard)

            # 모든 태스크 실행
            await asyncio.gather(*self._running_tasks)
            
        except Exception as e:
            logging.error(f"Scheduler startup failed: {e}")
            self.stop_event.set()
            for task in self._running_tasks:
                if not task.done():
                    task.cancel()

    def _get_next_run_time(self) -> datetime:
        """다음 실행 시간 계산"""
        now = datetime.now()
        next_times = []
        
        for time_str, schedule_time in self.trading_times.items():
            # 오늘의 예정 시간 생성
            schedule_datetime = datetime.combine(now.date(), schedule_time)
            
            # 이미 지난 시간이면 다음 날로 설정
            if schedule_datetime <= now:
                schedule_datetime += timedelta(days=1)
                
            next_times.append(schedule_datetime)
            
        return min(next_times) 

    async def _run_scheduler(self):
        while not self.stop_event.is_set():
            try:
                now = datetime.now()
                next_run = self._get_next_run_time()
                
                # 다음 실행 시간까지 대기
                wait_seconds = (next_run - now).total_seconds()
                await asyncio.sleep(wait_seconds)
                
                # 실행 시간이 되면 거래 작업 수행
                if not self.stop_event.is_set():  # 대기 중 종료되지 않았는지 확인
                    current_time = now.strftime("%H:%M")
                    last_run = self._last_run_times.get(current_time)
                    
                    if not last_run or (now - last_run).total_seconds() > 3600:  # 1시간 이상 지났으면
                        async with TRADING_STATE.trading_session(
                            OperationType.SCHEDULED,
                            max_duration=timedelta(minutes=5)
                        ) as trading:
                            if trading:
                                await self._run_trading_job()
                                self._last_run_times[current_time] = now
                
            except Exception as e:
                logging.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)  # 에러 발생시 1분 대기

    async def _run_trading_job(self):
        """정기 거래 작업 실행"""
        try:
            async with TRADING_STATE.trading_session(OperationType.SCHEDULED) as trading:
                if trading:
                    await self.loop.run_in_executor(
                        self.executor,
                        self.trader.run
                    )
        except Exception as e:
            logging.error(f"Trading job failed: {e}")


class MarketEmergencyMonitor:
    def __init__(self):
        self.market_analyzer = MarketAnalyzer()
        self.ai_trader = AITrader()
        self.check_interval = 600
        self.stop_event = asyncio.Event()
        self.danger_threshold = -5.0
        self.emergency_threshold = -7.0
        self.loop = asyncio.get_event_loop()
        self.executor = ThreadPoolExecutor(max_workers=3)
        self._last_emergency_time = None
        self._emergency_cooldown = timedelta(minutes=30)

    async def _can_handle_emergency(self) -> bool:
        """긴급 상황 처리 가능 여부 확인"""
        if not self._last_emergency_time:
            return True
        time_since_last = datetime.now() - self._last_emergency_time
        return time_since_last > self._emergency_cooldown

    async def start_monitoring(self):
        """비동기 모니터링 시작"""
        try:
            monitoring_task = self.loop.create_task(self._monitor_loop())
            await monitoring_task
        except Exception as e:
            logging.error(f"Monitoring start failed: {e}")

    async def _monitor_loop(self):
        """비동기 모니터링 루프"""
        while not self.stop_event.is_set():
            try:
                # 다른 거래 작업 중인지 확인
                if TRADING_STATE.is_trading:
                    await TRADING_STATE.wait_for_trading_completion(timeout=10)  # 10초 대기 후 재시도
                    continue

                # 시장 데이터 수집 (비동기로 실행)
                df = await self.loop.run_in_executor(
                    self.executor,
                    pyupbit.get_ohlcv,
                    "KRW-BTC",
                    "minute60",
                    24
                )

                if df is not None:
                    # 시장 분석 (비동기로 실행)
                    df = await self.loop.run_in_executor(
                        self.executor,
                        self.market_analyzer.add_technical_indicators,
                        df
                    )
                    
                    price_change = ((df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2]) * 100

                    if price_change <= self.emergency_threshold:
                        # 긴급 상황 처리
                        if not TRADING_STATE.emergency_mode:
                            TRADING_STATE.emergency_mode = True
                            await self._handle_emergency(df)
                    
                    elif price_change <= self.danger_threshold:
                        # 위험 상황 처리
                        if not TRADING_STATE.is_trading:
                            await self._handle_danger(df)

                await asyncio.sleep(self.check_interval)

            except Exception as e:
                logging.error(f"Monitoring error: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _handle_emergency(self, df):
        """비동기 긴급 상황 처리"""
        try:
            # trading_session 컨텍스트 매니저가 모든 상태 관리를 담당
            async with TRADING_STATE.trading_session(
                OperationType.EMERGENCY, 
                max_duration=timedelta(minutes=2)
            ) as trading:
                if not trading:
                    logging.warning("Could not start emergency trading session - another operation in progress")
                    return

                try:
                    # 시장 분석
                    market_analysis = await self.loop.run_in_executor(
                        self.executor,
                        self.market_analyzer.analyze_market,
                        df
                    )
                    
                    # 공포/탐욕 지수 조회
                    fear_greed = await self.loop.run_in_executor(
                        self.executor,
                        self.market_analyzer.get_fear_greed_index
                    )

                    # AI 의사결정
                    decision = await self.loop.run_in_executor(
                        self.executor,
                        self.ai_trader.get_ai_decision,
                        df,
                        market_analysis,
                        fear_greed
                    )

                    if not decision:
                        logging.warning("No trading decision made in emergency situation")
                        return

                    # 거래 실행
                    await self.loop.run_in_executor(
                        self.executor,
                        self.ai_trader.execute_trade,
                        decision
                    )
                    logging.info(f"Emergency action completed: {decision.decision}")

                except Exception as e:
                    logging.error(f"Emergency trading operation failed: {str(e)}")
                    raise  # 상위 예외 처리로 전파

        except Exception as e:
            logging.error(f"Emergency handling failed: {str(e)}")
            # 심각한 오류 발생 시 강제 리셋
            await TRADING_STATE.force_reset()

async def main():
    """메인 함수"""
    scheduler = TradingScheduler()
    try:
        await scheduler.start()
    except KeyboardInterrupt:
        scheduler.stop_event.set()
        scheduler.monitor.stop_event.set()
        logging.info("Trading system shutdown complete")

if __name__ == "__main__":
    asyncio.run(main())

