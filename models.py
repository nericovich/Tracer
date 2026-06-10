from sqlalchemy import Column, Integer, String, Float, DateTime, Date, Enum, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, date, timezone
import enum

Base = declarative_base()


class TransactionType(enum.Enum):
    DEPOSIT_FIAT = "deposit_fiat"
    WITHDRAW_FIAT = "withdraw_fiat"
    BUY_ASSET = "buy_asset"
    SELL_ASSET = "sell_asset"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    transactions = relationship("Transaction", back_populates="user", cascade="all, delete-orphan")
    bank_deposits = relationship("BankDeposit", back_populates="user", cascade="all, delete-orphan")
    hidden_assets = relationship("HiddenAsset", back_populates="user", cascade="all, delete-orphan")
    portfolio_history = relationship("PortfolioHistory", back_populates="user", cascade="all, delete-orphan")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    date = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    type = Column(Enum(TransactionType), index=True)
    ticker = Column(String, nullable=True, index=True)
    quantity = Column(Float, nullable=True)
    price_per_unit = Column(Float, nullable=True)
    total_amount = Column(Float)

    user = relationship("User", back_populates="transactions")


class AssetPrice(Base):
    __tablename__ = "assets_prices"

    ticker = Column(String, primary_key=True, index=True)
    current_price = Column(Float)
    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class HiddenAsset(Base):
    __tablename__ = "hidden_assets"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    ticker = Column(String, index=True)
    hidden_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    user = relationship("User", back_populates="hidden_assets")


class BankDeposit(Base):
    __tablename__ = "bank_deposits"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    bank_name = Column(String)
    amount = Column(Float)
    start_date = Column(Date)
    end_date = Column(Date)
    apy_percent = Column(Float)
    expected_profit = Column(Float)
    interest_payment_type = Column(String, default="at_end")
    capitalize = Column(Boolean, default=False)

    user = relationship("User", back_populates="bank_deposits")


class PortfolioHistory(Base):
    __tablename__ = "portfolio_history"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    date = Column(Date, index=True)
    total_value = Column(Float)
    daily_change_percent = Column(Float)

    user = relationship("User", back_populates="portfolio_history")
