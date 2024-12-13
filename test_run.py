import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
from datetime import datetime
from dotenv import load_dotenv

# 기존 코드에서 필요한 클래스들 import
from Autotrade_system import LoggerSetup, AITrader, MarketAnalyzer, DB_Feedback

async def test_run():
    """단일 테스트 실행을 위한 함수"""
    try:
        # 로깅 설정
        logger_setup = LoggerSetup()
        logging.info("Starting test run...")

        # AITrader 인스턴스 생성
        trader = AITrader()
        
        # 한 번의 거래 사이클 실행
        logging.info("Executing single trading cycle...")
        trader.run()
        
        logging.info("Test run completed successfully")
        
    except Exception as e:
        logging.error(f"Test run failed: {str(e)}")
        raise

if __name__ == "__main__":
    # 비동기 실행
    asyncio.run(test_run())