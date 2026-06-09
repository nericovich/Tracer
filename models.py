from sqlalchemy import Column, Integer, String, Float, DateTime, Date, Enum
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, date
import enum

Base = declarative_base()


class TransactionType(enum.Enum):
    DEPOSIT_FIAT = "deposit_fiat"
    WITHDRAW_FIAT = "withdraw_fiat"
    BUY_ASSET = "buy_asset"
    SELL_ASSET = "sell_asset"


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, default=datetime.utcnow, index=True)
    type = Column(Enum(TransactionType), index=True)
    ticker = Column(String, nullable=True, index=True)
    quantity = Column(Float, nullable=True)
    price_per_unit = Column(Float, nullable=True)
    total_amount = Column(Float)


class AssetPrice(Base):
    __tablename__ = "assets_prices"

    ticker = Column(String, primary_key=True, index=True)
    current_price = Column(Float)
    last_updated = Column(DateTime, default=datetime.utcnow)


class BankDeposit(Base):
    __tablename__ = "bank_deposits"

    id = Column(Integer, primary_key=True, index=True)
    bank_name = Column(String)
    amount = Column(Float)
    start_date = Column(Date)
    end_date = Column(Date)
    apy_percent = Column(Float)
    expected_profit = Column(Float)


class PortfolioHistory(Base):
    __tablename__ = "portfolio_history"

    date = Column(Date, primary_key=True, index=True)
    total_value = Column(Float)
    daily_change_percent = Column(Float)
