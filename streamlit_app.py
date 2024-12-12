import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
from Autotrade_system import DB_Feedback
# import pyupbit

def load_data():
    """MongoDB에서 데이터 로드"""
    db = DB_Feedback()
    try:
        recent_trades = db.get_recent_records(limit=50)
        trading_summary = db.get_trading_summary()
        reflections = db.get_reflection_history(limit=5)
        return recent_trades, trading_summary, reflections
    finally:
        db.close()

def create_trade_history_chart(trades):
    """거래 기록 차트 생성"""
    df = pd.DataFrame(trades)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    fig = go.Figure()
    
    # 가격 라인
    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=df['btc_krw_price'],
        name='BTC Price',
        line=dict(color='blue')
    ))
    
    # 매수/매도 포인트 표시
    buys = df[df['decision'] == 'buy']
    sells = df[df['decision'] == 'sell']
    
    fig.add_trace(go.Scatter(
        x=buys['timestamp'],
        y=buys['btc_krw_price'],
        mode='markers',
        name='Buy',
        marker=dict(color='green', size=10, symbol='triangle-up')
    ))
    
    fig.add_trace(go.Scatter(
        x=sells['timestamp'],
        y=sells['btc_krw_price'],
        mode='markers',
        name='Sell',
        marker=dict(color='red', size=10, symbol='triangle-down')
    ))
    
    fig.update_layout(
        title='Trading History',
        xaxis_title='Date',
        yaxis_title='Price (KRW)',
        hovermode='x unified'
    )
    
    return fig

def create_profit_chart(trades):
    """수익률 차트 생성"""
    df = pd.DataFrame(trades)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['cumulative_profit'] = df['profit'].cumsum()
    
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=df['timestamp'],
        y=df['cumulative_profit'],
        name='Cumulative Profit',
        fill='tozeroy'
    ))
    
    fig.update_layout(
        title='Cumulative Profit/Loss',
        xaxis_title='Date',
        yaxis_title='Profit (%)',
        hovermode='x unified'
    )
    
    return fig

def show_market_analysis(market_data):
    """시장 분석 데이터 표시"""
    if not market_data:
        return
        
    last_record = market_data[0]  # 가장 최근 데이터
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(
            "Current BTC Price",
            f"₩{last_record['btc_krw_price']:,.0f}",
            f"{last_record.get('profit', 0):.2f}%"
        )
        
    with col2:
        st.metric(
            "24h Volume",
            f"₩{last_record['market_analysis']['price_action']['volume_ratio']:,.0f}"
        )
        
    with col3:
        st.metric(
            "Market Direction",
            last_record['market_analysis']['trend']['trend_direction']
        )

def get_numeric_value(data):
    """MongoDB 숫자 데이터를 파이썬 숫자로 변환"""
    if isinstance(data, dict):
        if '$numberInt' in data:
            return int(data['$numberInt'])
        elif '$numberDouble' in data:
            return float(data['$numberDouble'])
    return data

def show_reflection_insights(reflections):
    """AI 반성 데이터 표시"""
    if not reflections:
        return
        
    latest_reflection = reflections[0]
    
    st.subheader("Latest AI Reflection")
    
    # 성과 지표
    metrics = latest_reflection['performance_metrics']
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        win_rate = get_numeric_value(metrics['win_rate'])
        st.metric("Win Rate", f"{win_rate:.1%}")
    with col2:
        total_trades = get_numeric_value(metrics['total_trades'])
        st.metric("Total Trades", total_trades)
    with col3:
        avg_profit = get_numeric_value(metrics['avg_profit_percentage'])
        st.metric("Avg Profit", f"{avg_profit:.2f}%")
    with col4:
        profitable_trades = get_numeric_value(metrics['profitable_trades'])
        st.metric("Profitable Trades", profitable_trades)
    
    # 인사이트
    st.subheader("Key Insights")
    for insight in latest_reflection['insights']:
        importance = get_numeric_value(insight['importance'])
        with st.expander(f"{insight['category']} (Importance: {importance}/10)"):
            st.write(insight['description'])
            st.write("Action Items:")
            for item in insight['action_items']:
                st.write(f"• {item}")
    
    # 성공/실패 패턴
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Successful Patterns")
        for pattern in latest_reflection['successful_patterns']:
            success_rate = get_numeric_value(pattern['success_rate'])
            avg_profit = get_numeric_value(pattern['avg_profit'])
            st.success(
                f"**{pattern['pattern_type']}**\n\n"
                f"Success Rate: {success_rate:.1%}\n\n"
                f"Avg Profit: {avg_profit:.2f}%"
            )
    
    with col2:
        st.subheader("Mistake Patterns")
        for pattern in latest_reflection['mistake_patterns']:
            frequency = get_numeric_value(pattern['frequency'])
            success_rate = get_numeric_value(pattern['success_rate'])
            avg_profit = get_numeric_value(pattern['avg_profit'])
            st.error(
                f"**{pattern['pattern_type']}**\n\n"
                f"Frequency: {frequency}\n\n"
                f"Success Rate: {success_rate:.1%}\n\n"
                f"Avg Profit: {avg_profit:.2f}%\n\n"
                f"{pattern['description']}"
            )

def main():
    st.set_page_config(
        page_title="AI Trading Dashboard",
        page_icon="📈",
        layout="wide"
    )
    
    st.title("AI Trading Dashboard")
    
    # 데이터 로드
    trades, summary, reflections = load_data()
    
    # 상단 지표
    show_market_analysis(trades)
    
    # 차트 섹션
    st.subheader("Trading Performance")
    col1, col2 = st.columns(2)
    
    with col1:
        st.plotly_chart(create_trade_history_chart(trades), use_container_width=True)
    with col2:
        st.plotly_chart(create_profit_chart(trades), use_container_width=True)
    
    # 거래 기록 테이블
    st.subheader("Recent Trades")
    trade_df = pd.DataFrame(trades)
    trade_df['timestamp'] = pd.to_datetime(trade_df['timestamp'])
    trade_df = trade_df[[
        'timestamp', 'decision', 'btc_krw_price', 'profit', 
        'confidence', 'reason'
    ]].sort_values('timestamp', ascending=False)
    
    st.dataframe(
        trade_df.style.format({
            'btc_krw_price': '{:,.0f}',
            'profit': '{:.2f}%',
            'confidence': '{:.1f}%'
        })
    )
    
    # AI 반성 섹션
    show_reflection_insights(reflections)

if __name__ == "__main__":
    main()