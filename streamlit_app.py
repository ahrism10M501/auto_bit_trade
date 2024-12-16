
from pymongo import MongoClient, DESCENDING, IndexModel
from datetime import datetime, timedelta
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from functools import wraps
from typing import Optional, List, Dict
import aiocache
from aiocache import Cache
from aiocache.serializers import PickleSerializer
import os
import logging
import pandas as pd

class OptimizedDBFeedback:
    def __init__(self):
        self._client: Optional[AsyncIOMotorClient] = None
        self._db = None
        self._collection = None
        self._reflection_collection = None
        self._cache = Cache(Cache.MEMORY)

    async def ensure_connection(self):
        """연결 확인 및 필요시 연결 수립"""
        if self._client is None:
            try:
                self._client = MongoClient(
                os.getenv("MONGO_URL"),
                serverSelectionTimeoutMS=5000
                 )
                
                self._db = self._client['auto_trading']
                self._collection = self._db['trade_records']
                self._reflection_collection = self._db['trade_reflections']
                
                # 인덱스 생성
                self._create_indexes()
                
            except Exception as e:
                logging.error(f"Failed to connect to MongoDB: {str(e)}")
                raise

    async def connect(self):
        """비동기 데이터베이스 연결 수립"""
        try:
            if self._client is not None:
                await self.close()
                
            self._client = MongoClient(
                os.getenv("MONGO_URL"),
                serverSelectionTimeoutMS=5000
            )
            
            self._db = self._client['auto_trading']
            self._collection = self._db['trade_records']
            self._reflection_collection = self._db['trade_reflections']
            
            # 복합 인덱스 생성
            self._create_indexes()
            
        except Exception as e:
            logging.error(f"Failed to connect to MongoDB: {str(e)}")
            raise

    async def _create_indexes(self):
        """최적화된 인덱스 생성"""
        indexes = [
            IndexModel([("timestamp", DESCENDING)]),
            IndexModel([("decision", 1), ("timestamp", DESCENDING)]),
            IndexModel([("profit", 1)]),
            IndexModel([
                ("timestamp", DESCENDING),
                ("btc_krw_price", 1),
                ("decision", 1)
            ]),
        ]
        
        await self._collection.create_indexes(indexes)
        
    @aiocache.cached(ttl=300)
    async def get_recent_records(self, limit: int = 10) -> list:
        """캐시된 최근 거래 기록 조회"""
        await self.ensure_connection()  # 연결 확인
        cursor = self._collection.find(
            {},
            {'_id': 0}
        ).sort('timestamp', DESCENDING).limit(limit)
        
        return await cursor.to_list(length=limit)
    
    @aiocache.cached(ttl=300)
    async def get_recent_reflection(self) -> dict:
        """캐시된 최근 거래 분석 데이터 조회"""
        await self.ensure_connection()
        result = await self._reflection_collection.find_one(
            {},
            {'_id': 0},
            sort=[('timestamp', DESCENDING)]
        )
        return result if result else {}
    
    async def get_aggregated_stats(self, start_date: datetime, end_date: datetime) -> Dict:
        """특정 기간의 집계된 거래 통계"""
        await self.ensure_connection()
        
        # MongoDB의 ISODate와 호환되도록 변환
        start = start_date.replace(microsecond=0)
        end = end_date.replace(microsecond=0)
        
        pipeline = [
            {
                '$match': {
                    'timestamp': {
                        '$gte': {'$date': start.isoformat()},
                        '$lte': {'$date': end.isoformat()}
                    }
                }
            },
            {
                '$group': {
                    '_id': None,
                    'total_profit': {'$sum': '$profit'},
                    'total_trades': {'$sum': 1},
                    'successful_trades': {
                        '$sum': {'$cond': [{'$gt': ['$profit', 0]}, 1, 0]}
                    },
                    'avg_profit': {'$avg': '$profit'},
                    'max_profit': {'$max': '$profit'},
                    'min_profit': {'$min': '$profit'}
                }
            }
        ]
        
        result = await self._collection.aggregate(pipeline).to_list(length=1)
        
        if not result:
            return {
                'total_profit': 0,
                'success_rate': 0,
                'avg_duration': 0,
                'risk_adjusted_return': 0,
                'total_profit_change': 0,
                'success_rate_change': 0,
                'avg_duration_change': 0,
                'risk_adjusted_return_change': 0
            }
            
        stats = result[0]
        success_rate = (stats['successful_trades'] / stats['total_trades'] * 100) if stats.get('total_trades', 0) > 0 else 0
        
        return {
            'total_profit': stats.get('total_profit', 0),
            'success_rate': success_rate,
            'avg_duration': 0,
            'risk_adjusted_return': stats.get('avg_profit', 0) / (stats.get('max_profit', 1) - stats.get('min_profit', 0) + 1),
            'total_profit_change': 0,
            'success_rate_change': 0,
            'avg_duration_change': 0,
            'risk_adjusted_return_change': 0
        }

    async def get_data_by_date_range(self, start_date: datetime, end_date: datetime) -> List:
        """날짜 범위로 데이터 조회"""
        await self.ensure_connection()
        
        query = {
            'timestamp': {
                '$gte': start_date,
                '$lte': end_date
            }
        }
        
        try:
            cursor = self._collection.find(
                query,
                {'_id': 0}
            ).sort('timestamp', DESCENDING)
            
            results = await cursor.to_list(length=None)
            logging.info(f"Found {len(results)} documents between {start_date} and {end_date}")
            return results
            
        except Exception as e:
            logging.error(f"Query error: {str(e)}")
            return []


    async def get_reflection_by_date_range(self, start_date: datetime, end_date: datetime) -> Dict:
        """특정 기간의 가장 최신 리플렉션 데이터 조회"""
        await self.ensure_connection()
        
        query = {
            'timestamp': {
                '$gte': start_date,
                '$lte': end_date
            }
        }
        
        try:
            result = await self._reflection_collection.find_one(
                query,
                {'_id': 0},
                sort=[('timestamp', DESCENDING)]
            )
            return result if result else {}
            
        except Exception as e:
            logging.error(f"Reflection query error: {str(e)}")
            return {}
    














# 2. 개선된 Streamlit 대시보드

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

class Dashboard:
    def __init__(self):
        self.db = OptimizedDBFeedback()
        st.set_page_config(
            page_title="Trading Dashboard",
            page_icon="📈",
            layout="wide",
            initial_sidebar_state="expanded"
        )

    def create_sidebar_filters(self):
            """사이드바 필터 옵션 생성"""
            st.sidebar.title("대시보드 설정")
            
            # 날짜 범위 선택
            col1, col2 = st.sidebar.columns(2)
            with col1:
                start_date = st.date_input(
                    "시작일",
                    value=datetime.now() - timedelta(days=7)
                )
                start_time = st.time_input("시작 시간", value=datetime.min.time())
            with col2:
                end_date = st.date_input("종료일", value=datetime.now())
                end_time = st.time_input("종료 시간", value=datetime.max.time())
                
            start_datetime = datetime.combine(start_date, start_time)
            end_datetime = datetime.combine(end_date, end_time)
            
            # 거래 유형 필터
            trade_types = st.sidebar.multiselect(
                "거래 유형",
                options=["매수", "매도", "홀딩"],
                default=["매수", "매도", "홀딩"]
            )
            
            return (start_datetime, end_datetime), trade_types

    async def load_data(self):
        """데이터 로드"""
        trades_data = await self.db.get_recent_records(100)
        stats_data = await self.db.get_aggregated_stats()
        reflection_data = await self.db.get_recent_reflection()
        return trades_data, stats_data, reflection_data
    @staticmethod
    def extract_timestamp(ts_field):
        """MongoDB timestamp 필드에서 datetime 추출"""
        if isinstance(ts_field, dict):
            if '$date' in ts_field:
                date_value = ts_field['$date']
                if isinstance(date_value, dict) and '$numberLong' in date_value:
                    return pd.to_datetime(int(date_value['$numberLong']), unit='ms')
                return pd.to_datetime(date_value)
        elif isinstance(ts_field, datetime):
            return ts_field
        return pd.to_datetime(ts_field)

    @staticmethod
    def extract_number(field):
        """MongoDB 숫자 필드에서 값 추출"""
        if isinstance(field, dict):
            for key in ['$numberInt', '$numberDouble', '$numberLong']:
                if key in field:
                    return float(field[key])
        return float(field) if field else 0
    
    def _extract_mongo_value(self, value):
        """MongoDB 문서에서 실제 값 추출"""
        if isinstance(value, dict):
            if '$numberInt' in value:
                return int(value['$numberInt'])
            elif '$numberDouble' in value:
                return float(value['$numberDouble'])
            elif '$date' in value:
                if isinstance(value['$date'], dict) and '$numberLong' in value['$date']:
                    return pd.to_datetime(int(value['$date']['$numberLong']), unit='ms')
                return pd.to_datetime(value['$date'])
            elif '$numberLong' in value:
                return int(value['$numberLong'])
        return value
            
 
    
    def _process_trades_data(self, trades_data, trade_types):
        """거래 데이터 전처리"""
        if not trades_data:
            return pd.DataFrame()

        # 거래 유형 매핑
        type_mapping = {
            "매수": "buy",
            "매도": "sell", 
            "홀딩": "hold"
        }
        selected_types = [type_mapping[t] for t in trade_types]

        processed_data = []
        for trade in trades_data:
            try:
                decision = trade.get('decision', '')
                # 선택된 거래 유형에 해당하는 데이터만 처리
                if decision in selected_types:
                    row = {
                        'timestamp': self.extract_timestamp(trade['timestamp']),
                        'btc_krw_price': self.extract_number(trade['btc_krw_price']),
                        'profit': self.extract_number(trade.get('profit', 0)),
                        'confidence': self.extract_number(trade.get('confidence', 0)),
                        'decision': decision,
                        'volume': self.extract_number(
                            trade.get('market_analysis', {})
                            .get('price_action', {})
                            .get('volume', 0)
                        )
                    }
                    processed_data.append(row)
            except Exception as e:
                logging.error(f"Error processing trade data: {str(e)}")
                continue
        
        return pd.DataFrame(processed_data)
    
    async def load_data_with_date_range(self, date_range):
        """날짜 범위로 데이터 로드"""
        start_date, end_date = date_range
        
        trades_data = await self.db.get_data_by_date_range(start_date, end_date)
        stats_data = await self.db.get_aggregated_stats(start_date, end_date)
        reflection_data = await self.db.get_reflection_by_date_range(start_date, end_date)
        
        return trades_data, stats_data, reflection_data


    def create_metric_charts(self, df):
        """확대/축소 가능한 메트릭 차트 생성"""
        if df.empty:
            st.warning("차트를 만들 데이터가 없습니다")
            return None, None

        try:
            # 수익률 차트
            profit_fig = go.Figure()
            if 'profit' in df.columns and 'timestamp' in df.columns:
                profit_fig.add_trace(
                    go.Scatter(
                        x=df['timestamp'],
                        y=df['profit'],
                        name="수익률",
                        line=dict(color='green')
                    )
                )
                profit_fig.update_layout(
                    title="수익률 추이",
                    xaxis_title="시간",
                    yaxis_title="수익률 (%)",
                    height=300,
                    xaxis=dict(rangeslider=dict(visible=True)),
                    margin=dict(l=0, r=0, t=30, b=0)
                )

            # 신뢰도 차트
            confidence_fig = go.Figure()
            if 'confidence' in df.columns and 'timestamp' in df.columns:
                confidence_fig.add_trace(
                    go.Scatter(
                        x=df['timestamp'],
                        y=df['confidence'],
                        name="신뢰도",
                        line=dict(color='blue')
                    )
                )
                confidence_fig.update_layout(
                    title="신뢰도 추이",
                    xaxis_title="시간",
                    yaxis_title="신뢰도",
                    height=300,
                    xaxis=dict(rangeslider=dict(visible=True)),
                    margin=dict(l=0, r=0, t=30, b=0)
                )

            return profit_fig, confidence_fig

        except Exception as e:
            st.error(f"차트 생성 오류: {str(e)}")
            return None, None

    def create_price_volume_chart(self, df):
        """가격/거래량 차트 생성"""
        if df.empty:
            return None

        try:
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.1,
                row_heights=[0.7, 0.3],
                subplot_titles=("가격 & 거래", "거래량")
            )

            # 가격 차트
            if 'btc_krw_price' in df.columns and 'timestamp' in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df['timestamp'],
                        y=df['btc_krw_price'],
                        name="BTC 가격",
                        line=dict(color='blue')
                    ),
                    row=1, col=1
                )

                # 거래 포인트
                if 'decision' in df.columns:
                    decision_colors = {
                        'buy': ('매수', 'green', 'triangle-up'),
                        'sell': ('매도', 'red', 'triangle-down'),
                        'hold': ('홀딩', 'gray', 'circle')
                    }
                    
                    for decision, (name, color, symbol) in decision_colors.items():
                        mask = df['decision'] == decision
                        if mask.any():
                            fig.add_trace(
                                go.Scatter(
                                    x=df.loc[mask, 'timestamp'],
                                    y=df.loc[mask, 'btc_krw_price'],
                                    mode='markers',
                                    name=name,
                                    marker=dict(size=10, symbol=symbol, color=color)
                                ),
                                row=1, col=1
                            )

            # 거래량 차트
            if 'volume' in df.columns:
                fig.add_trace(
                    go.Bar(
                        x=df['timestamp'],
                        y=df['volume'],
                        name="거래량"
                    ),
                    row=2, col=1
                )

            fig.update_layout(
                height=800,
                showlegend=True,
                xaxis_rangeslider_visible=True,
                hovermode='x unified',
                margin=dict(l=0, r=0, t=30, b=0)
            )

            return fig

        except Exception as e:
            logging.error(f"Error creating price volume chart: {str(e)}")
            return None


    def create_market_analysis_table(self, trades_data):
        """시장 분석 테이블 생성"""
        if not trades_data:
            return pd.DataFrame()
            
        market_data = []
        for trade in trades_data:
            try:
                ma = trade.get('market_analysis', {})
                price_action = ma.get('price_action', {})
                trend = ma.get('trend', {})
                momentum = ma.get('momentum', {})
                volatility = ma.get('volatility', {})
                
                row = {
                    '시간': self.extract_timestamp(trade['timestamp']),
                    '행동': trade.get('decision', 'unknown').upper(),
                    '현재가': self.extract_number(trade['btc_krw_price']),
                    '가격변화': self.extract_number(price_action.get('price_change', 0)),
                    '거래량': self.extract_number(price_action.get('volume', 0)),
                    'RSI': self.extract_number(momentum.get('rsi', 0)),
                    'BB위치': self.extract_number(volatility.get('bb_position', 0)),
                    '추세방향': trend.get('trend_direction', 'unknown')
                }
                market_data.append(row)
            except Exception as e:
                logging.error(f"Error processing market data: {str(e)}")
                continue
        
        return pd.DataFrame(market_data)
    
    def create_reflection_analysis_table(self, reflection_data):
        """트레이딩 분석 리포트 테이블 생성"""
        if not reflection_data:
            return pd.DataFrame()
                
        try:
            # 성과 메트릭스
            metrics = reflection_data.get('performance_metrics', {})
            
            performance_df = pd.DataFrame({
                '지표': [
                    '총 거래 수',
                    '수익 거래',
                    '손실 거래',
                    '평균 수익률',
                    '승률',
                    '최대 수익',
                    '최대 손실',
                    '평균 보유시간'
                ],
                '값': [
                    str(1+self.extract_number(metrics.get('total_trades', 0))),
                    str(self.extract_number(metrics.get('profitable_trades', 0))),
                    str(self.extract_number(metrics.get('loss_trades', 0))),
                    f"{self.extract_number(metrics.get('avg_profit_percentage', 0)):.2f}%",
                    f"{self.extract_number(metrics.get('win_rate', 0)):.2f}%",
                    f"{self.extract_number(metrics.get('largest_profit', 0)):.2f}%",
                    f"{self.extract_number(metrics.get('largest_loss', 0)):.2f}%",
                    f"{self.extract_number(metrics.get('avg_holding_time', 0)):.1f}h"
                ]
            })
            
            return performance_df
        except Exception as e:
            logging.error(f"Error creating reflection analysis table: {str(e)}")
            return pd.DataFrame()

    def create_decision_analysis_table(self, trades_data, reflection_data):
        """의사결정 분석 테이블 생성"""
        if not trades_data:
            return pd.DataFrame()
                
        try:
            # 거래 데이터로부터 직접 의사결정 분석
            decision_stats = {
                'buy': {'count': 0, 'profits': [], 'confidences': []},
                'sell': {'count': 0, 'profits': [], 'confidences': []},
                'hold': {'count': 0, 'profits': [], 'confidences': []}
            }
            
            # 각 거래별 통계 수집
            for trade in trades_data:
                decision = trade.get('decision', '').lower()
                if decision in decision_stats:
                    stats = decision_stats[decision]
                    stats['count'] += 1
                    
                    profit = self.extract_number(trade.get('profit', 0))
                    confidence = self.extract_number(trade.get('confidence', 0))
                    
                    stats['profits'].append(profit)
                    stats['confidences'].append(confidence)
            
            # 분석 결과를 테이블 형태로 변환
            decision_data = []
            decision_mapping = {
                'buy': '매수',
                'sell': '매도',
                'hold': '홀딩'
            }
            
            for decision, stats in decision_stats.items():
                if stats['count'] > 0:
                    profits = stats['profits']
                    confidences = stats['confidences']
                    
                    success_count = sum(1 for p in profits if p > 0)
                    success_rate = (success_count / stats['count'] * 100) if stats['count'] > 0 else 0
                    
                    decision_data.append({
                        '결정유형': decision_mapping[decision],
                        '거래횟수': str(stats['count']),
                        '성공률': f"{success_rate:.2f}%",
                        '평균수익': f"{(sum(profits) / len(profits)):.2f}%" if profits else "0.00%",
                        '평균신뢰도': f"{(sum(confidences) / len(confidences)):.1f}%" if confidences else "0.0%"
                    })
            
            return pd.DataFrame(decision_data)
        except Exception as e:
            logging.error(f"Error creating decision analysis table: {str(e)}")
            return pd.DataFrame()

    def create_insights_table(self, trades_data, reflection_data):
        """거래 데이터와 리플렉션 데이터 기반의 통합 인사이트 분석"""
        if not trades_data:
            return pd.DataFrame()
                
        try:
            insights = []
            
            # 1. 리플렉션 데이터의 인사이트 처리
            if reflection_data and 'insights' in reflection_data:
                reflection_insights = reflection_data['insights']
                for insight in reflection_insights:
                    # MongoDB 형식의 데이터 처리
                    importance = (insight.get('importance', {}).get('$numberInt', 0) 
                                if isinstance(insight.get('importance'), dict) 
                                else insight.get('importance', 0))
                    
                    insights.append({
                        '카테고리': f"성찰_{insight.get('category', '')}",
                        '중요도': 'High' if int(importance) > 7 else 'Medium',
                        '설명': insight.get('description', ''),
                        '개선사항': '\n'.join(insight.get('action_items', []))
                    })
            
            # 2. 거래 데이터 기반 실시간 분석
            trades_df = pd.DataFrame([{
                'timestamp': self.extract_timestamp(trade['timestamp']),
                'decision': trade.get('decision', ''),
                'profit': self.extract_number(trade.get('profit', 0)),
                'confidence': self.extract_number(trade.get('confidence', 0)),
                'price': self.extract_number(trade['btc_krw_price']),
                'rsi': self.extract_number(
                    trade.get('market_analysis', {})
                    .get('momentum', {})
                    .get('rsi', 0)
                )
            } for trade in trades_data])
            
            if not trades_df.empty:
                # 2.1 수익성 분석
                total_trades = len(trades_df)
                profitable_trades = len(trades_df[trades_df['profit'] > 0])
                profit_rate = (profitable_trades / total_trades * 100) if total_trades > 0 else 0
                avg_profit = trades_df['profit'].mean()
                
                if profit_rate < 50:
                    insights.append({
                        '카테고리': '실시간_수익성',
                        '중요도': 'High',
                        '설명': f'승률이 {profit_rate:.1f}%로 저조합니다.',
                        '개선사항': '거래 진입 조건을 더 엄격하게 설정하고, 손실 거래의 패턴을 분석하세요.'
                    })
                elif avg_profit < 0:
                    insights.append({
                        '카테고리': '실시간_수익성',
                        '중요도': 'High',
                        '설명': f'평균 수익이 {avg_profit:.2f}%로 음수입니다.',
                        '개선사항': '손절 전략을 재검토하고, 수익 실현 시점을 최적화하세요.'
                    })
                
                # 2.2 거래 패턴 분석
                by_decision = trades_df.groupby('decision')['profit'].agg(['mean', 'count'])
                for decision, stats in by_decision.iterrows():
                    if stats['mean'] < 0 and stats['count'] >= 3:
                        insights.append({
                            '카테고리': '실시간_거래패턴',
                            '중요도': 'Medium',
                            '설명': f'{decision.upper()} 결정의 평균 수익이 {stats["mean"]:.2f}%로 음수입니다.',
                            '개선사항': f'{decision.upper()} 진입 조건과 타이밍을 재검토하세요.'
                        })
                
                # 2.3 신뢰도 분석
                high_confidence_trades = trades_df[trades_df['confidence'] > 70]
                if not high_confidence_trades.empty:
                    high_conf_profit = high_confidence_trades['profit'].mean()
                    if high_conf_profit < avg_profit:
                        insights.append({
                            '카테고리': '실시간_신뢰도',
                            '중요도': 'Medium',
                            '설명': '높은 신뢰도 거래의 수익성이 평균보다 낮습니다.',
                            '개선사항': '신뢰도 계산 로직을 재검토하고 보정이 필요합니다.'
                        })
                
                # 2.4 RSI 기반 분석
                if 'rsi' in trades_df.columns:
                    overbought_trades = trades_df[trades_df['rsi'] > 70]
                    oversold_trades = trades_df[trades_df['rsi'] < 30]
                    
                    if not overbought_trades.empty and overbought_trades['profit'].mean() < 0:
                        insights.append({
                            '카테고리': '실시간_기술적지표',
                            '중요도': 'Medium',
                            '설명': 'RSI 과매수 구간 거래의 수익성이 저조합니다.',
                            '개선사항': 'RSI 과매수 구간에서의 매수를 재검토하세요.'
                        })
                    if not oversold_trades.empty and oversold_trades['profit'].mean() < 0:
                        insights.append({
                            '카테고리': '실시간_기술적지표',
                            '중요도': 'Medium',
                            '설명': 'RSI 과매도 구간 거래의 수익성이 저조합니다.',
                            '개선사항': 'RSI 과매도 구간에서의 매도를 재검토하세요.'
                        })
                
                # 2.5 최근 추세 분석
                recent_trades = trades_df.tail(5)  # 최근 5개 거래
                recent_profit_trend = recent_trades['profit'].mean()
                if recent_profit_trend < avg_profit:
                    insights.append({
                        '카테고리': '실시간_최근추세',
                        '중요도': 'High',
                        '설명': '최근 거래들의 수익성이 전체 평균보다 낮아지고 있습니다.',
                        '개선사항': '최근 시장 상황에 맞춰 전략을 조정하세요.'
                    })

            # 결과를 데이터프레임으로 변환하고 중요도로 정렬
            df = pd.DataFrame(insights)
            if not df.empty:
                # 중요도 순서 정의 (High가 먼저 오도록)
                importance_order = {'High': 0, 'Medium': 1}
                df['importance_order'] = df['중요도'].map(importance_order)
                df = df.sort_values('importance_order').drop('importance_order', axis=1)
            
            return df
        except Exception as e:
            logging.error(f"Error creating insights table: {str(e)}")
            return pd.DataFrame()

    def display_tables(self, trades_data, reflection_data):
        """테이블 표시"""
        try:
            tabs = st.tabs(["시장 분석", "성과 분석", "의사결정 분석", "인사이트"])
            
            # 시장 분석 탭
            with tabs[0]:
                try:
                    st.subheader("시장 분석 데이터")
                    market_table = self.create_market_analysis_table(trades_data)
                    if not market_table.empty:
                        st.dataframe(
                            market_table.style.format({
                                '현재가': '{:,.0f}',
                                '가격변화': '{:.2f}%',
                                '거래량': '{:.2f}',
                                'RSI': '{:.2f}',
                                'BB위치': '{:.2f}'
                            }),
                            use_container_width=True
                        )
                    else:
                        st.info("시장 분석 데이터가 없습니다.")
                except Exception as e:
                    st.error("시장 분석 데이터 표시 중 오류가 발생했습니다.")
                    logging.error(f"Market analysis table error: {str(e)}")

            # 성과 분석 탭
            with tabs[1]:
                try:
                    st.subheader("성과 분석")
                    performance_table = self.create_reflection_analysis_table(reflection_data)
                    if not performance_table.empty:
                        st.dataframe(performance_table, use_container_width=True)
                    else:
                        st.info("성과 분석 데이터가 없습니다.")
                except Exception as e:
                    st.error("성과 분석 데이터 표시 중 오류가 발생했습니다.")
                    logging.error(f"Performance analysis table error: {str(e)}")

            # 의사결정 분석 탭
            with tabs[2]:
                try:
                    st.subheader("의사결정 분석")
                    decision_table = self.create_decision_analysis_table(trades_data, reflection_data)
                    if not decision_table.empty:
                        st.dataframe(decision_table, use_container_width=True)
                    else:
                        st.info("의사결정 분석 데이터가 없습니다.")
                except Exception as e:
                    st.error("의사결정 분석 데이터 표시 중 오류가 발생했습니다.")
                    logging.error(f"Decision analysis table error: {str(e)}")

            # 인사이트 탭
            with tabs[3]:
                try:
                    st.subheader("인사이트")
                    insights_table = self.create_insights_table(trades_data, reflection_data)
                    if not insights_table.empty:
                        # 컬럼 순서 지정
                        columns_order = ['카테고리', '중요도', '설명', '개선사항']
                        insights_table = insights_table.reindex(columns=columns_order)
                        
                        # 데이터프레임 표시
                        st.dataframe(
                            insights_table,
                            use_container_width=True,
                            height=400  # 높이 지정으로 스크롤 가능하게
                        )
                    else:
                        st.info("인사이트 데이터가 없습니다.")
                except Exception as e:
                    st.error("인사이트 데이터 표시 중 오류가 발생했습니다.")
                    logging.error(f"Insights table error: {str(e)}")

        except Exception as e:
            st.error("테이블 표시 중 오류가 발생했습니다.")
            logging.error(f"Overall table display error: {str(e)}")
            
        finally:
            # 메모리 정리
            try:
                del market_table
                del performance_table
                del decision_table
                del insights_table
            except:
                pass

    def show_metrics(self, stats_data):
        """메트릭 표시"""
        try:
            cols = st.columns(4)
            
            metrics = {
                "Total Profit": (
                    f"{self.extract_number(stats_data.get('total_profit', 0)):,.2f}%", 
                    f"{self.extract_number(stats_data.get('total_profit_change', 0)):+.2f}%"
                ),
                "Success Rate": (
                    f"{self.extract_number(stats_data.get('success_rate', 0)):.1f}%", 
                    f"{self.extract_number(stats_data.get('success_rate_change', 0)):+.1f}%"
                ),
                "Avg Trade Duration": (
                    f"{self.extract_number(stats_data.get('avg_duration', 0)):.1f}h", 
                    f"{self.extract_number(stats_data.get('avg_duration_change', 0)):+.1f}h"
                ),
                "Risk Adjusted Return": (
                    f"{self.extract_number(stats_data.get('risk_adjusted_return', 0)):.2f}", 
                    f"{self.extract_number(stats_data.get('risk_adjusted_return_change', 0)):+.2f}"
                )
            }
            
            for col, (label, (value, delta)) in zip(cols, metrics.items()):
                col.metric(label=label, value=value, delta=delta)
                
        except Exception as e:
            st.error("메트릭 표시 중 오류가 발생했습니다.")
            logging.error(f"Error displaying metrics: {str(e)}")
    
    def run(self):
        """대시보드 실행"""
        st.title("Trading Dashboard")

        try:
            # 사이드바 필터
            date_range, trade_types = self.create_sidebar_filters()

            # 데이터 로드
            with st.spinner('데이터를 불러오는 중...'):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                trades_data, stats_data, reflection_data = loop.run_until_complete(
                    self.load_data_with_date_range(date_range)
                )
                loop.close()

            if trades_data and stats_data:
                # 상단 메트릭스 표시
                self.show_metrics(stats_data)
                
                # 데이터프레임 생성 (거래 유형 필터링 적용)
                df = self._process_trades_data(trades_data, trade_types)
                
                if not df.empty:
                    # 메트릭 차트 표시
                    col1, col2 = st.columns(2)
                    profit_fig, confidence_fig = self.create_metric_charts(df)
                    
                    if profit_fig and confidence_fig:
                        with col1:
                            st.plotly_chart(profit_fig, use_container_width=True)
                        with col2:
                            st.plotly_chart(confidence_fig, use_container_width=True)
                    
                    # 메인 차트 표시
                    fig = self.create_price_volume_chart(df)
                    if fig:
                        st.plotly_chart(fig, use_container_width=True)
                    
                    # 테이블 표시
                    self.display_tables(trades_data, reflection_data)
                else:
                    st.info("선택한 기간에 해당하는 거래 데이터가 없습니다.")
            else:
                st.info("데이터를 불러올 수 없습니다. 다른 기간을 선택해보세요.")

        except Exception as e:
            st.error("대시보드 로딩 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
            logging.error(f"Dashboard error: {str(e)}")
            
if __name__ == "__main__":
    dashboard = Dashboard()
    dashboard.run()
