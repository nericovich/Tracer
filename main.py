from fastapi import FastAPI, Depends, HTTPException, File, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from datetime import datetime, date, timedelta
from typing import List, Optional
from decimal import Decimal
import json
import httpx
import asyncio

from database import get_db, init_db
from models import Transaction, TransactionType, AssetPrice, BankDeposit, PortfolioHistory

app = FastAPI(title="Трекер Портфеля")


# ============ Pydantic Schemas ============

class TransactionCreate(BaseModel):
    date: datetime
    type: TransactionType
    ticker: Optional[str] = None
    quantity: Optional[float] = None
    total_amount: float


class PriceUpdate(BaseModel):
    ticker: str
    current_price: float


class AssetHolding(BaseModel):
    ticker: str
    quantity: float
    current_price: float
    total_value: float
    portfolio_share_percent: float
    cost_basis: float
    unrealized_pnl: float
    unrealized_pnl_percent: float


class DashboardResponse(BaseModel):
    total_capital: float
    total_invested_in_assets: float
    total_in_deposits: float
    total_portfolio_value: float
    total_unrealized_pnl: float
    total_unrealized_pnl_percent: float
    assets: List[AssetHolding]
    deposits: List[dict]
    cash_balance: float


# ============ MOEX API Integration ============

async def get_moex_price(ticker: str) -> Optional[float]:
    """Получить цену акции с API Мосбиржи."""
    try:
        async with httpx.AsyncClient() as client:
            # Получить информацию об инструменте
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}.json"
            response = await client.get(url, timeout=10)

            if response.status_code == 200:
                data = response.json()
                marketdata = data.get("marketdata", {})

                if marketdata and "data" in marketdata and len(marketdata["data"]) > 0:
                    row = marketdata["data"][0]
                    if len(row) > 11:
                        price = row[11]
                        if price:
                            return float(price)
    except Exception as e:
        print(f"Ошибка получения цены {ticker}: {e}")

    return None


async def search_moex_stocks(query: str = "") -> list:
    """Поиск акций на Мосбирже."""
    try:
        async with httpx.AsyncClient() as client:
            url = "https://iss.moex.com/iss/engines/stock/markets/shares/securities.json"
            params = {"lang": "ru"}

            response = await client.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                securities = data.get("securities", {})

                if securities and "data" in securities:
                    results = []
                    for row in securities["data"]:
                        if len(row) > 1:
                            ticker = row[0]
                            name = row[1]

                            # Фильтруем по запросу
                            if query.upper() in ticker or query.upper() in name:
                                results.append({
                                    "ticker": ticker,
                                    "name": name
                                })

                    return results[:20]  # Возвращаем первые 20 результатов
    except Exception as e:
        print(f"Ошибка поиска акций: {e}")

    return []


async def update_price_from_moex(db: Session, ticker: str) -> Optional[float]:
    """Обновить цену акции из Мосбиржи."""
    try:
        price = await get_moex_price(ticker)

        if price:
            price_record = db.query(AssetPrice).filter(
                AssetPrice.ticker == ticker
            ).first()

            if not price_record:
                price_record = AssetPrice(
                    ticker=ticker,
                    current_price=price,
                    last_updated=datetime.utcnow()
                )
                db.add(price_record)
            else:
                price_record.current_price = price
                price_record.last_updated = datetime.utcnow()

            db.commit()
            print(f"Цена для {ticker} обновлена: {price}")
            return price
        else:
            print(f"Не удалось получить цену для {ticker}")
            return None
    except Exception as e:
        print(f"Ошибка при обновлении цены {ticker}: {e}")
        return None


# ============ Utility Functions ============

def calculate_current_holdings(db: Session) -> dict:
    """Calculate current holdings for each ticker from transactions."""
    holdings = {}

    transactions = db.query(Transaction).filter(
        Transaction.ticker.isnot(None)
    ).order_by(Transaction.date).all()

    for txn in transactions:
        if txn.ticker not in holdings:
            holdings[txn.ticker] = {
                "quantity": 0,
                "cost_basis": 0,
                "transactions": []
            }

        if txn.type == TransactionType.BUY_ASSET:
            holdings[txn.ticker]["quantity"] += txn.quantity
            holdings[txn.ticker]["cost_basis"] += txn.total_amount
        elif txn.type == TransactionType.SELL_ASSET:
            holdings[txn.ticker]["quantity"] -= txn.quantity
            holdings[txn.ticker]["cost_basis"] -= txn.total_amount * (txn.quantity / txn.quantity) if txn.quantity else 0

        holdings[txn.ticker]["transactions"].append(txn)

    return {k: v for k, v in holdings.items() if v["quantity"] > 0}


def calculate_cash_balance(db: Session) -> float:
    """Calculate current cash balance from deposits and withdrawals."""
    deposits = db.query(func.sum(Transaction.total_amount)).filter(
        Transaction.type == TransactionType.DEPOSIT_FIAT
    ).scalar() or 0

    withdrawals = db.query(func.sum(Transaction.total_amount)).filter(
        Transaction.type == TransactionType.WITHDRAW_FIAT
    ).scalar() or 0

    asset_purchases = db.query(func.sum(Transaction.total_amount)).filter(
        Transaction.type == TransactionType.BUY_ASSET
    ).scalar() or 0

    asset_sales = db.query(func.sum(Transaction.total_amount)).filter(
        Transaction.type == TransactionType.SELL_ASSET
    ).scalar() or 0

    return float(deposits - withdrawals - asset_purchases + asset_sales)


def get_or_create_price(db: Session, ticker: str) -> AssetPrice:
    """Get or create an asset price record."""
    price = db.query(AssetPrice).filter(AssetPrice.ticker == ticker).first()
    if not price:
        price = AssetPrice(ticker=ticker, current_price=0.0)
        db.add(price)
        db.commit()
        db.refresh(price)
    return price


# ============ API Endpoints ============

@app.on_event("startup")
def startup_event():
    init_db()


@app.get("/")
async def root():
    """Serve the dashboard HTML."""
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/api/dashboard", response_model=DashboardResponse)
async def get_dashboard(db: Session = Depends(get_db)):
    """
    Получить текущее состояние портфеля.
    Включает активы, оценки и активные депозиты.
    """
    holdings = calculate_current_holdings(db)
    cash_balance = calculate_cash_balance(db)

    # Получить общий капитал (пополнения - выводы)
    deposits = db.query(func.sum(Transaction.total_amount)).filter(
        Transaction.type == TransactionType.DEPOSIT_FIAT
    ).scalar() or 0

    withdrawals = db.query(func.sum(Transaction.total_amount)).filter(
        Transaction.type == TransactionType.WITHDRAW_FIAT
    ).scalar() or 0

    total_capital_fiat = float(deposits - withdrawals)

    assets = []
    total_invested = 0
    total_value = 0
    total_cost_basis = 0
    total_unrealized_pnl = 0

    for ticker, holding_data in holdings.items():
        price_record = get_or_create_price(db, ticker)
        current_price = price_record.current_price
        quantity = holding_data["quantity"]
        cost_basis = holding_data["cost_basis"]

        holding_value = quantity * current_price
        unrealized_pnl = holding_value - cost_basis
        unrealized_pnl_percent = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0

        total_value += holding_value
        total_invested += cost_basis
        total_cost_basis += cost_basis
        total_unrealized_pnl += unrealized_pnl

        assets.append(AssetHolding(
            ticker=ticker,
            quantity=quantity,
            current_price=current_price,
            total_value=holding_value,
            portfolio_share_percent=0,
            cost_basis=cost_basis,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_percent=unrealized_pnl_percent
        ))

    total_portfolio_value = total_value + cash_balance

    # Пересчет процентов распределения
    for asset in assets:
        asset.portfolio_share_percent = (asset.total_value / total_portfolio_value * 100) if total_portfolio_value > 0 else 0

    # Получить активные депозиты
    today = date.today()
    active_deposits = db.query(BankDeposit).filter(
        BankDeposit.end_date >= today
    ).all()

    total_in_deposits = sum(d.amount for d in active_deposits)
    total_deposit_profit = sum(d.expected_profit for d in active_deposits)

    deposits_data = [
        {
            "bank_name": d.bank_name,
            "amount": d.amount,
            "apy_percent": d.apy_percent,
            "start_date": d.start_date.isoformat(),
            "end_date": d.end_date.isoformat(),
            "expected_profit": d.expected_profit,
            "days_remaining": (d.end_date - today).days
        }
        for d in active_deposits
    ]

    # Добавить депозиты к общей стоимости портфеля
    total_portfolio_value += total_in_deposits

    # Добавить прибыль от депозитов к общей прибыли
    total_unrealized_pnl += total_deposit_profit

    # Пересчитать проценты распределения с учетом депозитов
    for asset in assets:
        asset.portfolio_share_percent = (asset.total_value / total_portfolio_value * 100) if total_portfolio_value > 0 else 0

    # Общий капитал = внесено пользователем (депозиты - выводы) + капитал в депозитах
    total_capital = total_capital_fiat + total_in_deposits

    # Процент прибыли считается от общей себестоимости (акции + депозиты)
    total_cost_basis_all = total_cost_basis + total_in_deposits
    total_unrealized_pnl_percent = (total_unrealized_pnl / total_cost_basis_all * 100) if total_cost_basis_all > 0 else 0

    return DashboardResponse(
        total_capital=total_capital,
        total_invested_in_assets=total_invested,
        total_in_deposits=total_in_deposits,
        total_portfolio_value=total_portfolio_value,
        total_unrealized_pnl=total_unrealized_pnl,
        total_unrealized_pnl_percent=total_unrealized_pnl_percent,
        assets=assets,
        deposits=deposits_data,
        cash_balance=cash_balance
    )


@app.post("/api/transactions")
async def add_transaction(transaction: TransactionCreate, db: Session = Depends(get_db)):
    """Добавить новую транзакцию. Цена получается автоматически из API Мосбиржи."""
    price_per_unit = None

    try:
        # Если это покупка/продажа - получить цену с MOEX
        if transaction.type in [TransactionType.BUY_ASSET, TransactionType.SELL_ASSET]:
            if transaction.ticker:
                print(f"Получаю цену для {transaction.ticker}...")
                price_per_unit = await update_price_from_moex(db, transaction.ticker)
                print(f"Получена цена: {price_per_unit}")
                if not price_per_unit:
                    return {
                        "error": f"Не удалось получить цену для {transaction.ticker}. Проверьте тикер.",
                        "status": "error"
                    }

        db_transaction = Transaction(
            date=transaction.date,
            type=transaction.type,
            ticker=transaction.ticker,
            quantity=transaction.quantity,
            price_per_unit=price_per_unit,
            total_amount=transaction.total_amount
        )
        db.add(db_transaction)
        db.commit()
        db.refresh(db_transaction)

        # Обновить историю портфеля
        update_portfolio_history(db)

        return {
            "id": db_transaction.id,
            "message": "Транзакция добавлена успешно",
            "price": price_per_unit
        }
    except Exception as e:
        print(f"Ошибка добавления транзакции: {e}")
        raise HTTPException(status_code=400, detail=f"Ошибка: {str(e)}")


@app.get("/api/prices/{ticker}")
async def get_price(ticker: str, db: Session = Depends(get_db)):
    """Получить текущую цену акции."""
    price = await update_price_from_moex(db, ticker)

    if not price:
        raise HTTPException(status_code=404, detail=f"Цена для {ticker} не найдена")

    return {"ticker": ticker, "price": price}


@app.get("/api/history")
async def get_portfolio_history(db: Session = Depends(get_db)):
    """Return daily portfolio value for the chart."""
    history = db.query(PortfolioHistory).order_by(PortfolioHistory.date).all()

    return [
        {
            "date": h.date.isoformat(),
            "total_value": h.total_value,
            "daily_change_percent": h.daily_change_percent
        }
        for h in history
    ]


def update_portfolio_history(db: Session):
    """Recalculate and update the portfolio history for today."""
    today = date.today()

    # Get previous day's value
    previous_history = db.query(PortfolioHistory).filter(
        PortfolioHistory.date < today
    ).order_by(PortfolioHistory.date.desc()).first()

    previous_value = previous_history.total_value if previous_history else 0

    # Calculate today's total value
    holdings = calculate_current_holdings(db)
    cash_balance = calculate_cash_balance(db)

    today_value = cash_balance
    for ticker, holding_data in holdings.items():
        price_record = get_or_create_price(db, ticker)
        today_value += holding_data["quantity"] * price_record.current_price

    # Calculate daily change
    daily_change_percent = 0
    if previous_value > 0:
        daily_change_percent = ((today_value - previous_value) / previous_value) * 100

    # Update or create history record
    history_record = db.query(PortfolioHistory).filter(
        PortfolioHistory.date == today
    ).first()

    if history_record:
        history_record.total_value = today_value
        history_record.daily_change_percent = daily_change_percent
    else:
        history_record = PortfolioHistory(
            date=today,
            total_value=today_value,
            daily_change_percent=daily_change_percent
        )
        db.add(history_record)

    db.commit()


@app.get("/api/stocks/search")
async def search_stocks(q: str = ""):
    """Поиск акций на Мосбирже."""
    if not q or len(q) < 1:
        return []

    results = await search_moex_stocks(q)
    return results


@app.post("/api/bank-deposits")
async def add_bank_deposit(deposit: dict, db: Session = Depends(get_db)):
    """Добавить новый банковский депозит."""
    try:
        new_deposit = BankDeposit(
            bank_name=deposit.get("bank_name"),
            amount=float(deposit.get("amount", 0)),
            start_date=datetime.fromisoformat(deposit.get("start_date")).date(),
            end_date=datetime.fromisoformat(deposit.get("end_date")).date(),
            apy_percent=float(deposit.get("apy_percent", 0)),
            expected_profit=float(deposit.get("expected_profit", 0))
        )
        db.add(new_deposit)
        db.commit()
        db.refresh(new_deposit)

        return {
            "id": new_deposit.id,
            "message": "Депозит добавлен успешно"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка: {str(e)}")


# Health check
@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
