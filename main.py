from __future__ import annotations

import asyncio
import calendar
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from database import engine, get_db, init_db, migrate_schema
from models import (
    AssetPrice, BankDeposit, HiddenAsset, PortfolioHistory,
    Transaction, TransactionType, User,
)


app = FastAPI(title="Трекер портфеля")

InterestPaymentType = Literal["daily", "monthly", "at_end", "upfront"]

# ============ Auth Configuration ============

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production-34982")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 72

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": str(user_id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user_dep(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    token = auth[7:]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Недействительный токен")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user


# ============ Pydantic Schemas ============


class UserRegister(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=4)


class UserLogin(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserInfo(BaseModel):
    id: int
    username: str
    created_at: Optional[str] = None


class TransactionCreate(BaseModel):
    date: datetime
    type: TransactionType
    ticker: Optional[str] = None
    quantity: Optional[float] = None
    total_amount: Optional[float] = None


class PriceUpdate(BaseModel):
    ticker: str
    current_price: float = Field(ge=0)


class BankDepositCreate(BaseModel):
    bank_name: str
    amount: float = Field(gt=0)
    start_date: date
    end_date: date
    apy_percent: float = Field(ge=0)
    interest_payment_type: InterestPaymentType = "at_end"
    expected_profit: Optional[float] = None
    capitalize: bool = False


class AssetHolding(BaseModel):
    ticker: str
    quantity: float
    current_price: float
    total_value: float
    portfolio_share_percent: float
    cost_basis: float
    average_cost: float
    unrealized_pnl: float
    unrealized_pnl_percent: float
    realized_pnl: float
    total_pnl: float
    total_pnl_percent: float


class DashboardResponse(BaseModel):
    total_capital: float
    total_portfolio_value: float
    total_assets_value: float
    total_invested_in_assets: float
    total_assets_pnl: float
    total_assets_pnl_percent: float
    total_in_deposits: float
    total_deposits_principal: float
    total_deposits_accrued_profit: float
    total_deposits_expected_profit: float
    total_unrealized_pnl: float
    total_unrealized_pnl_percent: float
    assets_change_1d: float
    assets_change_1d_percent: float
    assets_change_1m: float
    assets_change_1m_percent: float
    prices_updated_at: Optional[str] = None
    usd_rub_rate: float = 90.0
    total_capital_usd: float = 0.0
    total_assets_value_usd: float = 0.0
    total_in_deposits_usd: float = 0.0
    cash_balance: float = 0.0
    cash_balance_usd: float = 0.0
    assets: list[AssetHolding]
    deposits: list[dict[str, Any]]


# ============ MOEX API Integration ============


def _value_by_column(block: dict[str, Any], row: list[Any], name: str) -> Any:
    try:
        return row[block["columns"].index(name)]
    except (ValueError, IndexError, KeyError):
        return None


async def get_moex_price(ticker: str) -> Optional[float]:
    secid = ticker.strip().upper()
    if not secid:
        return None
    url = (
        "https://iss.moex.com/iss/engines/stock/markets/shares/"
        f"securities/{secid}.json"
    )
    params = {"iss.meta": "off", "lang": "ru"}
    preferred_boards = ["TQBR", "TQTF", "TQIF", "TQTD", "TQPI"]
    price_fields = ["LAST", "LCURRENTPRICE", "MARKETPRICE", "WAPRICE", "LEGALCLOSEPRICE", "PREVPRICE"]
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        print(f"MOEX price error for {secid}: {exc}")
        return None
    marketdata = data.get("marketdata", {})
    rows = marketdata.get("data", [])
    if not rows:
        return None
    def row_score(row: list[Any]) -> int:
        board = _value_by_column(marketdata, row, "BOARDID")
        try:
            return preferred_boards.index(board)
        except ValueError:
            return len(preferred_boards)
    for row in sorted(rows, key=row_score):
        for field in price_fields:
            value = _value_by_column(marketdata, row, field)
            if value not in (None, "", 0):
                return float(value)
    return None


async def get_moex_historical_price(ticker: str, trade_date: date) -> Optional[float]:
    secid = ticker.strip().upper()
    if not secid:
        return None
    start_date = trade_date - timedelta(days=14)
    url = (
        "https://iss.moex.com/iss/history/engines/stock/markets/shares/"
        f"securities/{secid}.json"
    )
    params = {"from": start_date.isoformat(), "till": trade_date.isoformat(), "iss.meta": "off", "lang": "ru"}
    preferred_boards = ["TQBR", "TQTF", "TQIF", "TQTD", "TQPI"]
    price_fields = ["CLOSE", "MARKETPRICE3", "MARKETPRICE2", "LEGALCLOSEPRICE", "WAPRICE"]
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        print(f"MOEX history price error for {secid}: {exc}")
        return None
    history = data.get("history", {})
    rows = history.get("data", [])
    if not rows:
        return None
    def row_score(row: list[Any]) -> tuple[str, int]:
        row_date = _value_by_column(history, row, "TRADEDATE") or ""
        board = _value_by_column(history, row, "BOARDID")
        try:
            board_rank = preferred_boards.index(board)
        except ValueError:
            board_rank = len(preferred_boards)
        return (str(row_date), -board_rank)
    for row in sorted(rows, key=row_score, reverse=True):
        for field in price_fields:
            value = _value_by_column(history, row, field)
            if value not in (None, "", 0):
                return float(value)
    return None


async def get_transaction_price(ticker: str, transaction_date: date) -> Optional[float]:
    if transaction_date < date.today():
        historical_price = await get_moex_historical_price(ticker, transaction_date)
        if historical_price is not None:
            return historical_price
    return await get_moex_price(ticker)


async def search_moex_stocks(query: str = "") -> list[dict[str, str]]:
    normalized_query = query.strip().upper()
    if not normalized_query:
        return []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://iss.moex.com/iss/securities.json",
                params={"q": normalized_query, "is_trading": 1, "iss.meta": "off", "lang": "ru"},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        print(f"MOEX search error for {normalized_query}: {exc}")
        return []
    securities = data.get("securities", {})
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in securities.get("data", []):
        secid = _value_by_column(securities, row, "secid")
        name = (
            _value_by_column(securities, row, "name")
            or _value_by_column(securities, row, "shortname")
            or secid
        )
        if not secid:
            continue
        secid = str(secid).upper()
        name = str(name)
        if normalized_query not in secid and normalized_query not in name.upper():
            continue
        if secid in seen:
            continue
        seen.add(secid)
        results.append({"ticker": secid, "name": name})
    return results[:20]


async def update_price_from_moex(db: Session, ticker: str) -> Optional[float]:
    secid = ticker.strip().upper()
    price = await get_moex_price(secid)
    if price is None:
        return None
    price_record = db.query(AssetPrice).filter(AssetPrice.ticker == secid).first()
    if price_record:
        price_record.current_price = price
        price_record.last_updated = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        db.add(AssetPrice(ticker=secid, current_price=price, last_updated=datetime.now(timezone.utc).replace(tzinfo=None)))
    db.commit()
    return price


async def get_usd_rub_rate() -> float:
    """Fetch USD/RUB exchange rate from MOEX. Returns rate or fallback of 90.0"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://iss.moex.com/iss/engines/currency/markets/selt/securities/USDRUB_TOM.json",
                params={"iss.meta": "off", "lang": "ru"},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
        marketdata = data.get("marketdata", {})
        rows = marketdata.get("data", [])
        if rows:
            rate = _value_by_column(marketdata, rows[0], "LAST")
            if rate and float(rate) > 0:
                return float(rate)
    except Exception as exc:
        print(f"MOEX USD/RUB rate error: {exc}")
    return 90.0


# ============ Financial Calculations ============


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def simple_deposit_profit(amount: float, apy_percent: float, start_date: date, end_date: date) -> float:
    days = max((end_date - start_date).days, 0)
    return amount * (apy_percent / 100) * days / 365


def compound_deposit_profit(amount: float, apy_percent: float, start_date: date, end_date: date, payment_type: str) -> float:
    days = max((end_date - start_date).days, 0)
    if days == 0 or apy_percent <= 0:
        return 0.0
    rate = apy_percent / 100
    if payment_type == "daily":
        return amount * ((1 + rate / 365) ** days - 1)
    if payment_type == "monthly":
        months = max(1, round(days / 30.44))
        return amount * ((1 + rate / 12) ** months - 1)
    return simple_deposit_profit(amount, apy_percent, start_date, end_date)


def monthly_payment_dates(start_date: date, end_date: date) -> list[date]:
    payment_dates: list[date] = []
    month_number = 1
    while True:
        payment_date = add_months(start_date, month_number)
        if payment_date >= end_date:
            break
        payment_dates.append(payment_date)
        month_number += 1
    if end_date > start_date:
        payment_dates.append(end_date)
    return payment_dates


def deposit_expected_profit(deposit: BankDeposit) -> float:
    if deposit.expected_profit and deposit.expected_profit > 0:
        return float(deposit.expected_profit)
    if getattr(deposit, "capitalize", False):
        payment_type = getattr(deposit, "interest_payment_type", None) or "at_end"
        return compound_deposit_profit(
            float(deposit.amount), float(deposit.apy_percent),
            deposit.start_date, deposit.end_date, payment_type,
        )
    return simple_deposit_profit(
        float(deposit.amount), float(deposit.apy_percent),
        deposit.start_date, deposit.end_date,
    )


def deposit_accrued_profit(deposit: BankDeposit, valuation_date: date) -> float:
    expected_profit = deposit_expected_profit(deposit)
    payment_type = getattr(deposit, "interest_payment_type", None) or "at_end"
    capitalize = getattr(deposit, "capitalize", False)
    if valuation_date < deposit.start_date:
        return 0.0
    if valuation_date >= deposit.end_date:
        return expected_profit
    if payment_type == "upfront":
        return expected_profit
    if payment_type == "at_end":
        return 0.0
    amount = float(deposit.amount)
    apy = float(deposit.apy_percent)
    total_days = max((deposit.end_date - deposit.start_date).days, 1)
    elapsed_days = max((valuation_date - deposit.start_date).days, 0)
    if capitalize and apy > 0:
        rate = apy / 100
        if payment_type == "daily":
            return amount * ((1 + rate / 365) ** elapsed_days - 1)
        if payment_type == "monthly":
            payments = monthly_payment_dates(deposit.start_date, deposit.end_date)
            if not payments:
                return 0.0
            completed_payments = sum(1 for p in payments if p <= valuation_date)
            return amount * ((1 + rate / 12) ** completed_payments - 1)
    if payment_type == "daily":
        return expected_profit * min(elapsed_days / total_days, 1)
    if payment_type == "monthly":
        payments = monthly_payment_dates(deposit.start_date, deposit.end_date)
        if not payments:
            return 0.0
        completed_payments = sum(1 for payment_date in payments if payment_date <= valuation_date)
        return expected_profit * completed_payments / len(payments)
    return 0.0


def asset_transaction_amount(transaction: Transaction) -> float:
    quantity = float(transaction.quantity or 0)
    price_per_unit = float(transaction.price_per_unit or 0)
    if (
        transaction.type in [TransactionType.BUY_ASSET, TransactionType.SELL_ASSET]
        and quantity > 0 and price_per_unit > 0
    ):
        return quantity * price_per_unit
    return float(transaction.total_amount or 0)


def get_hidden_tickers(db: Session, user_id: int) -> set[str]:
    return {
        item.ticker.upper()
        for item in db.query(HiddenAsset).filter(HiddenAsset.user_id == user_id).all()
        if item.ticker
    }


def calculate_current_holdings(db: Session, user_id: int) -> dict[str, dict[str, float]]:
    holdings: dict[str, dict[str, float]] = {}
    transactions = (
        db.query(Transaction)
        .filter(Transaction.user_id == user_id, Transaction.ticker.isnot(None))
        .order_by(Transaction.date, Transaction.id)
        .all()
    )
    for transaction in transactions:
        ticker = (transaction.ticker or "").upper()
        if not ticker:
            continue
        holding = holdings.setdefault(ticker, {"quantity": 0.0, "cost_basis": 0.0, "realized_pnl": 0.0})
        quantity = float(transaction.quantity or 0)
        amount = asset_transaction_amount(transaction)
        if transaction.type == TransactionType.BUY_ASSET:
            holding["quantity"] += quantity
            holding["cost_basis"] += amount
        elif transaction.type == TransactionType.SELL_ASSET and quantity > 0:
            current_quantity = holding["quantity"]
            if current_quantity <= 0:
                holding["realized_pnl"] += amount
                continue
            sold_quantity = min(quantity, current_quantity)
            average_cost = holding["cost_basis"] / current_quantity
            sold_cost_basis = average_cost * sold_quantity
            holding["quantity"] = current_quantity - sold_quantity
            holding["cost_basis"] = max(holding["cost_basis"] - sold_cost_basis, 0.0)
            holding["realized_pnl"] += amount - sold_cost_basis
    return {
        ticker: holding
        for ticker, holding in holdings.items()
        if holding["quantity"] > 0 or abs(holding["realized_pnl"]) > 0.0001
    }


def calculate_cash_balance(db: Session, user_id: int) -> float:
    cash_balance = 0.0
    for transaction in (
        db.query(Transaction)
        .filter(Transaction.user_id == user_id)
        .order_by(Transaction.date, Transaction.id)
        .all()
    ):
        amount = (
            asset_transaction_amount(transaction)
            if transaction.type in [TransactionType.BUY_ASSET, TransactionType.SELL_ASSET]
            else float(transaction.total_amount or 0)
        )
        if transaction.type == TransactionType.DEPOSIT_FIAT:
            cash_balance += amount
        elif transaction.type == TransactionType.WITHDRAW_FIAT:
            cash_balance -= amount
        elif transaction.type == TransactionType.BUY_ASSET:
            cash_balance -= amount
        elif transaction.type == TransactionType.SELL_ASSET:
            cash_balance += amount
    return cash_balance


def get_or_create_price(db: Session, ticker: str) -> AssetPrice:
    secid = ticker.strip().upper()
    price = db.query(AssetPrice).filter(AssetPrice.ticker == secid).first()
    if price:
        return price
    price = AssetPrice(ticker=secid, current_price=0.0, last_updated=datetime.now(timezone.utc).replace(tzinfo=None))
    db.add(price)
    db.commit()
    db.refresh(price)
    return price


async def compute_asset_period_changes(
    db: Session, user_id: int, current_assets_value: float
) -> dict[str, float]:
    holdings = calculate_current_holdings(db, user_id)
    active_tickers = [
        (ticker, holding["quantity"])
        for ticker, holding in holdings.items()
        if holding["quantity"] > 0
    ]
    if not active_tickers or current_assets_value <= 0:
        return {"assets_change_1d": 0.0, "assets_change_1d_percent": 0.0,
                "assets_change_1m": 0.0, "assets_change_1m_percent": 0.0}
    today = date.today()
    yesterday = today - timedelta(days=1)
    month_ago = today - timedelta(days=30)
    async def fetch_pair(ticker: str) -> tuple[str, Optional[float], Optional[float]]:
        price_1d, price_1m = await asyncio.gather(
            get_moex_historical_price(ticker, yesterday),
            get_moex_historical_price(ticker, month_ago),
        )
        return ticker, price_1d, price_1m
    results = await asyncio.gather(*(fetch_pair(t) for t, _ in active_tickers))
    price_map = {ticker: (p1d, p1m) for ticker, p1d, p1m in results}
    value_1d_ago = 0.0
    value_1m_ago = 0.0
    for ticker, quantity in active_tickers:
        price_record = db.query(AssetPrice).filter(AssetPrice.ticker == ticker).first()
        current_price = float(price_record.current_price or 0) if price_record else 0.0
        asset_current_value = quantity * current_price
        p1d, p1m = price_map.get(ticker, (None, None))
        if p1d is not None and p1d > 0:
            value_1d_ago += quantity * p1d
        else:
            value_1d_ago += asset_current_value
        if p1m is not None and p1m > 0:
            value_1m_ago += quantity * p1m
        else:
            value_1m_ago += asset_current_value
    change_1d = current_assets_value - value_1d_ago
    change_1m = current_assets_value - value_1m_ago
    return {
        "assets_change_1d": change_1d,
        "assets_change_1d_percent": (change_1d / value_1d_ago * 100) if value_1d_ago > 0 else 0.0,
        "assets_change_1m": change_1m,
        "assets_change_1m_percent": (change_1m / value_1m_ago * 100) if value_1m_ago > 0 else 0.0,
    }


async def refresh_open_asset_prices(db: Session, user_id: int, max_age_minutes: int = 15) -> None:
    holdings = calculate_current_holdings(db, user_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for ticker, holding in holdings.items():
        if holding["quantity"] <= 0:
            continue
        price_record = db.query(AssetPrice).filter(AssetPrice.ticker == ticker).first()
        is_stale = (
            price_record is None
            or price_record.current_price in (None, 0)
            or price_record.last_updated is None
            or price_record.last_updated < now - timedelta(minutes=max_age_minutes)
        )
        if is_stale:
            await update_price_from_moex(db, ticker)


def build_dashboard_snapshot(
    db: Session, user_id: int, valuation_date: Optional[date] = None, usd_rub_rate: float = 90.0
) -> DashboardResponse:
    valuation_date = valuation_date or date.today()
    holdings = calculate_current_holdings(db, user_id)
    cash_balance = calculate_cash_balance(db, user_id)
    active_deposits = (
        db.query(BankDeposit)
        .filter(BankDeposit.user_id == user_id,
                BankDeposit.start_date <= valuation_date,
                BankDeposit.end_date >= valuation_date)
        .all()
    )
    total_deposits_principal = 0.0
    total_deposits_accrued_profit = 0.0
    total_deposits_expected_profit = 0.0
    deposits_data: list[dict[str, Any]] = []
    for deposit in active_deposits:
        expected_profit = deposit_expected_profit(deposit)
        accrued_profit = deposit_accrued_profit(deposit, valuation_date)
        current_value = float(deposit.amount) + accrued_profit
        days_total = max((deposit.end_date - deposit.start_date).days, 0)
        days_elapsed = min(max((valuation_date - deposit.start_date).days, 0), days_total)
        total_deposits_principal += float(deposit.amount)
        total_deposits_accrued_profit += accrued_profit
        total_deposits_expected_profit += expected_profit
        deposits_data.append({
            "id": deposit.id, "bank_name": deposit.bank_name,
            "amount": float(deposit.amount), "current_value": current_value,
            "apy_percent": float(deposit.apy_percent),
            "start_date": deposit.start_date.isoformat(),
            "end_date": deposit.end_date.isoformat(),
            "interest_payment_type": getattr(deposit, "interest_payment_type", None) or "at_end",
            "capitalize": bool(getattr(deposit, "capitalize", False)),
            "expected_profit": expected_profit, "accrued_profit": accrued_profit,
            "days_elapsed": days_elapsed, "days_total": days_total,
            "days_remaining": max((deposit.end_date - valuation_date).days, 0),
        })
    assets: list[AssetHolding] = []
    hidden_tickers = get_hidden_tickers(db, user_id)
    total_assets_value = 0.0
    total_assets_cost_basis = 0.0
    total_assets_unrealized_pnl = 0.0
    total_assets_realized_pnl = 0.0
    for ticker, holding in holdings.items():
        quantity = float(holding["quantity"])
        cost_basis = float(holding["cost_basis"])
        realized_pnl = float(holding["realized_pnl"])
        price_record = get_or_create_price(db, ticker)
        current_price = float(price_record.current_price or 0)
        total_value = quantity * current_price
        unrealized_pnl = total_value - cost_basis
        total_pnl = unrealized_pnl + realized_pnl
        if quantity <= 0 and abs(realized_pnl) > 0.0001:
            total_assets_realized_pnl += realized_pnl
            continue
        total_assets_value += total_value
        total_assets_cost_basis += cost_basis
        total_assets_unrealized_pnl += unrealized_pnl
        total_assets_realized_pnl += realized_pnl
        if ticker not in hidden_tickers:
            assets.append(AssetHolding(
                ticker=ticker, quantity=quantity, current_price=current_price,
                total_value=total_value, portfolio_share_percent=0,
                cost_basis=cost_basis,
                average_cost=(cost_basis / quantity) if quantity > 0 else 0,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_percent=(unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0,
                realized_pnl=realized_pnl, total_pnl=total_pnl,
                total_pnl_percent=(total_pnl / cost_basis * 100) if cost_basis > 0 else 0,
            ))
    total_in_deposits = total_deposits_principal + total_deposits_accrued_profit
    total_portfolio_value = total_assets_value + total_in_deposits + cash_balance
    total_assets_pnl = total_assets_unrealized_pnl + total_assets_realized_pnl
    total_unrealized_pnl = total_assets_pnl + total_deposits_accrued_profit
    total_cost_basis = total_assets_cost_basis + total_deposits_principal
    for asset in assets:
        asset.portfolio_share_percent = (
            asset.total_value / total_assets_value * 100 if total_assets_value > 0 else 0
        )
    # USD conversions
    rate = usd_rub_rate if usd_rub_rate > 0 else 90.0
    total_capital_usd = total_portfolio_value / rate
    total_assets_value_usd = total_assets_value / rate
    total_in_deposits_usd = total_in_deposits / rate
    cash_balance_usd = cash_balance / rate
    return DashboardResponse(
        total_capital=total_portfolio_value, total_portfolio_value=total_portfolio_value,
        total_assets_value=total_assets_value, total_invested_in_assets=total_assets_cost_basis,
        total_assets_pnl=total_assets_pnl,
        total_assets_pnl_percent=(total_assets_pnl / total_assets_cost_basis * 100) if total_assets_cost_basis > 0 else 0,
        total_in_deposits=total_in_deposits, total_deposits_principal=total_deposits_principal,
        total_deposits_accrued_profit=total_deposits_accrued_profit,
        total_deposits_expected_profit=total_deposits_expected_profit,
        total_unrealized_pnl=total_unrealized_pnl,
        total_unrealized_pnl_percent=(total_unrealized_pnl / total_cost_basis * 100) if total_cost_basis > 0 else 0,
        assets_change_1d=0.0, assets_change_1d_percent=0.0,
        assets_change_1m=0.0, assets_change_1m_percent=0.0,
        usd_rub_rate=rate,
        total_capital_usd=total_capital_usd,
        total_assets_value_usd=total_assets_value_usd,
        total_in_deposits_usd=total_in_deposits_usd,
        cash_balance=cash_balance,
        cash_balance_usd=cash_balance_usd,
        assets=assets, deposits=deposits_data,
    )


def update_portfolio_history(db: Session, user_id: int) -> None:
    today = date.today()
    snapshot = build_dashboard_snapshot(db, user_id, today)
    previous_history = (
        db.query(PortfolioHistory)
        .filter(PortfolioHistory.user_id == user_id, PortfolioHistory.date < today)
        .order_by(PortfolioHistory.date.desc())
        .first()
    )
    previous_value = previous_history.total_value if previous_history else 0
    daily_change_percent = (
        (snapshot.total_portfolio_value - previous_value) / previous_value * 100
        if previous_value > 0 else 0
    )
    history_record = (
        db.query(PortfolioHistory)
        .filter(PortfolioHistory.user_id == user_id, PortfolioHistory.date == today)
        .first()
    )
    if history_record:
        history_record.total_value = snapshot.total_portfolio_value
        history_record.daily_change_percent = daily_change_percent
    else:
        db.add(PortfolioHistory(
            user_id=user_id, date=today,
            total_value=snapshot.total_portfolio_value,
            daily_change_percent=daily_change_percent,
        ))
    db.commit()


# ============ API Endpoints ============


@app.on_event("startup")
def startup_event() -> None:
    migrate_schema()
    # Create default user and assign existing data if no users exist
    db = next(get_db())
    try:
        if db.query(User).count() == 0:
            default_user = User(username="admin", password_hash=hash_password("S1-ola11"))
            db.add(default_user)
            db.commit()
            db.refresh(default_user)
            # Assign all orphaned data to default user
            db.query(Transaction).filter(Transaction.user_id.is_(None)).update({"user_id": default_user.id})
            db.query(BankDeposit).filter(BankDeposit.user_id.is_(None)).update({"user_id": default_user.id})
            db.query(HiddenAsset).filter(HiddenAsset.user_id.is_(None)).update({"user_id": default_user.id})
            db.query(PortfolioHistory).filter(PortfolioHistory.user_id.is_(None)).update({"user_id": default_user.id})
            db.commit()
    finally:
        db.close()


@app.get("/")
async def root() -> HTMLResponse:
    with open("index.html", "r", encoding="utf-8") as file:
        return HTMLResponse(content=file.read())


# ---- Auth Endpoints ----


@app.post("/api/auth/register", response_model=TokenResponse)
async def register(body: UserRegister, db: Session = Depends(get_db)) -> TokenResponse:
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    user = User(username=body.username, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@app.post("/api/auth/login", response_model=TokenResponse)
async def login(body: UserLogin, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@app.get("/api/auth/me", response_model=UserInfo)
async def get_me(user: User = Depends(get_current_user_dep)) -> UserInfo:
    return UserInfo(id=user.id, username=user.username, created_at=user.created_at.isoformat() if user.created_at else None)


# ---- Data Endpoints (all require auth) ----


@app.get("/api/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    force: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
) -> DashboardResponse:
    await refresh_open_asset_prices(db, user.id, max_age_minutes=0 if force else 15)
    usd_rate = await get_usd_rub_rate()
    snapshot = build_dashboard_snapshot(db, user.id, usd_rub_rate=usd_rate)
    period_changes = await compute_asset_period_changes(db, user.id, snapshot.total_assets_value)
    snapshot.assets_change_1d = period_changes["assets_change_1d"]
    snapshot.assets_change_1d_percent = period_changes["assets_change_1d_percent"]
    snapshot.assets_change_1m = period_changes["assets_change_1m"]
    snapshot.assets_change_1m_percent = period_changes["assets_change_1m_percent"]
    update_portfolio_history(db, user.id)
    holdings = calculate_current_holdings(db, user.id)
    active_tickers = [t for t, h in holdings.items() if h["quantity"] > 0]
    if active_tickers:
        latest_update = (
            db.query(AssetPrice.last_updated)
            .filter(AssetPrice.ticker.in_(active_tickers))
            .order_by(AssetPrice.last_updated.desc())
            .first()
        )
        if latest_update and latest_update[0]:
            snapshot.prices_updated_at = latest_update[0].isoformat()
    return snapshot


@app.post("/api/transactions")
async def add_transaction(
    transaction: TransactionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
) -> dict[str, Any]:
    price_per_unit = None
    ticker = transaction.ticker.strip().upper() if transaction.ticker else None
    total_amount = float(transaction.total_amount or 0)
    if transaction.type in [TransactionType.BUY_ASSET, TransactionType.SELL_ASSET]:
        if not ticker or not transaction.quantity or transaction.quantity <= 0:
            raise HTTPException(status_code=400, detail="Укажите тикер и количество акций.")
        price_per_unit = await update_price_from_moex(db, ticker)
        if price_per_unit is None:
            raise HTTPException(status_code=400, detail=f"Не удалось получить цену для {ticker} с Мосбиржи.")
        total_amount = float(price_per_unit) * float(transaction.quantity)
        hidden_asset = db.query(HiddenAsset).filter(
            HiddenAsset.user_id == user.id, HiddenAsset.ticker == ticker
        ).first()
        if hidden_asset:
            db.delete(hidden_asset)
            db.commit()
    elif total_amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше нуля.")
    db_transaction = Transaction(
        user_id=user.id, date=transaction.date, type=transaction.type,
        ticker=ticker, quantity=transaction.quantity,
        price_per_unit=price_per_unit, total_amount=total_amount,
    )
    db.add(db_transaction)
    db.commit()
    db.refresh(db_transaction)
    update_portfolio_history(db, user.id)
    return {"id": db_transaction.id, "message": "Операция добавлена", "price": price_per_unit}


@app.get("/api/prices/{ticker}")
async def get_price(
    ticker: str, db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
) -> dict[str, Any]:
    price = await update_price_from_moex(db, ticker)
    if price is None:
        raise HTTPException(status_code=404, detail=f"Цена для {ticker.upper()} не найдена")
    return {"ticker": ticker.upper(), "price": price}


@app.post("/api/prices")
async def update_price(
    price_update: PriceUpdate, db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
) -> dict[str, Any]:
    ticker = price_update.ticker.strip().upper()
    price_record = db.query(AssetPrice).filter(AssetPrice.ticker == ticker).first()
    if price_record:
        price_record.current_price = price_update.current_price
        price_record.last_updated = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        db.add(AssetPrice(ticker=ticker, current_price=price_update.current_price, last_updated=datetime.now(timezone.utc).replace(tzinfo=None)))
    db.commit()
    update_portfolio_history(db, user.id)
    return {"ticker": ticker, "price": price_update.current_price}


@app.delete("/api/assets/{ticker}")
async def delete_asset(
    ticker: str, db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
) -> dict[str, str]:
    secid = ticker.strip().upper()
    if not secid:
        raise HTTPException(status_code=400, detail="Укажите тикер.")
    transactions = (
        db.query(Transaction)
        .filter(Transaction.user_id == user.id, Transaction.ticker == secid)
        .all()
    )
    if not transactions:
        raise HTTPException(status_code=404, detail=f"Операции для актива {secid} не найдены.")
    for txn in transactions:
        db.delete(txn)
    hidden_asset = db.query(HiddenAsset).filter(
        HiddenAsset.user_id == user.id, HiddenAsset.ticker == secid
    ).first()
    if hidden_asset:
        db.delete(hidden_asset)
    db.commit()
    update_portfolio_history(db, user.id)
    return {"ticker": secid, "message": f"Актив {secid} и все связанные операции удалены."}


@app.delete("/api/assets/{ticker}/hide")
async def hide_asset(
    ticker: str, db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
) -> dict[str, str]:
    secid = ticker.strip().upper()
    if not secid:
        raise HTTPException(status_code=400, detail="Укажите тикер.")
    holdings = calculate_current_holdings(db, user.id)
    if secid not in holdings or holdings[secid]["quantity"] <= 0:
        raise HTTPException(status_code=404, detail=f"Актив {secid} не найден в открытых позициях.")
    hidden_asset = db.query(HiddenAsset).filter(
        HiddenAsset.user_id == user.id, HiddenAsset.ticker == secid
    ).first()
    if not hidden_asset:
        db.add(HiddenAsset(user_id=user.id, ticker=secid, hidden_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        db.commit()
    return {"ticker": secid, "message": "Актив скрыт из таблицы."}


@app.get("/api/history")
async def get_portfolio_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
) -> list[dict[str, Any]]:
    update_portfolio_history(db, user.id)
    history = (
        db.query(PortfolioHistory)
        .filter(PortfolioHistory.user_id == user.id)
        .order_by(PortfolioHistory.date)
        .all()
    )
    if not history:
        snapshot = build_dashboard_snapshot(db, user.id)
        return [{"date": date.today().isoformat(), "total_value": snapshot.total_portfolio_value, "daily_change_percent": 0}]
    return [
        {"date": item.date.isoformat(), "total_value": float(item.total_value or 0),
         "daily_change_percent": float(item.daily_change_percent or 0)}
        for item in history
    ]


@app.get("/api/stocks/search")
async def search_stocks(
    q: str = "",
    user: User = Depends(get_current_user_dep),
) -> list[dict[str, str]]:
    if not q.strip():
        return []
    return await search_moex_stocks(q)


@app.post("/api/bank-deposits")
async def add_bank_deposit(
    deposit: BankDepositCreate, db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
) -> dict[str, Any]:
    if deposit.end_date <= deposit.start_date:
        raise HTTPException(status_code=400, detail="Дата окончания должна быть позже даты начала.")
    expected_profit = (
        deposit.expected_profit
        if deposit.expected_profit is not None and deposit.expected_profit > 0
        else (
            compound_deposit_profit(deposit.amount, deposit.apy_percent, deposit.start_date, deposit.end_date, deposit.interest_payment_type)
            if deposit.capitalize
            else simple_deposit_profit(deposit.amount, deposit.apy_percent, deposit.start_date, deposit.end_date)
        )
    )
    new_deposit = BankDeposit(
        user_id=user.id, bank_name=deposit.bank_name.strip(), amount=deposit.amount,
        start_date=deposit.start_date, end_date=deposit.end_date,
        apy_percent=deposit.apy_percent, expected_profit=expected_profit,
        interest_payment_type=deposit.interest_payment_type, capitalize=deposit.capitalize,
    )
    db.add(new_deposit)
    db.commit()
    db.refresh(new_deposit)
    update_portfolio_history(db, user.id)
    return {"id": new_deposit.id, "message": "Вклад добавлен", "expected_profit": expected_profit}


@app.delete("/api/bank-deposits/{deposit_id}")
async def delete_bank_deposit(
    deposit_id: int, db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
) -> dict[str, Any]:
    deposit = db.query(BankDeposit).filter(
        BankDeposit.id == deposit_id, BankDeposit.user_id == user.id
    ).first()
    if not deposit:
        raise HTTPException(status_code=404, detail="Вклад не найден.")
    bank_name = deposit.bank_name
    db.delete(deposit)
    db.commit()
    update_portfolio_history(db, user.id)
    return {"id": deposit_id, "message": f"Вклад '{bank_name}' удалён."}


@app.get("/api/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
