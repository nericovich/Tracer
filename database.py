from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from models import Base
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./portfolio.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)


def migrate_schema():
    """Migrate existing database schema to support multi-user auth."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        # --- Recreate hidden_assets with new PK structure ---
        if "hidden_assets" in existing_tables:
            ha_cols = {c["name"] for c in inspector.get_columns("hidden_assets")}
            if "id" not in ha_cols:
                # Old schema: ticker as PK. Migrate to new schema.
                conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS hidden_assets_new ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  user_id INTEGER,"
                    "  ticker VARCHAR,"
                    "  hidden_at DATETIME"
                    ")"
                ))
                conn.execute(text(
                    "INSERT INTO hidden_assets_new (ticker, hidden_at) "
                    "SELECT ticker, hidden_at FROM hidden_assets"
                ))
                conn.execute(text("DROP TABLE hidden_assets"))
                conn.execute(text(
                    "ALTER TABLE hidden_assets_new RENAME TO hidden_assets"
                ))

        # --- Recreate portfolio_history with new PK structure ---
        if "portfolio_history" in existing_tables:
            ph_cols = {c["name"] for c in inspector.get_columns("portfolio_history")}
            if "id" not in ph_cols:
                conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS portfolio_history_new ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  user_id INTEGER,"
                    "  date DATE,"
                    "  total_value FLOAT,"
                    "  daily_change_percent FLOAT"
                    ")"
                ))
                conn.execute(text(
                    "INSERT INTO portfolio_history_new (date, total_value, daily_change_percent) "
                    "SELECT date, total_value, daily_change_percent FROM portfolio_history"
                ))
                conn.execute(text("DROP TABLE portfolio_history"))
                conn.execute(text(
                    "ALTER TABLE portfolio_history_new RENAME TO portfolio_history"
                ))

        # --- Add user_id to transactions ---
        if "transactions" in existing_tables:
            cols = {c["name"] for c in inspector.get_columns("transactions")}
            if "user_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE transactions ADD COLUMN user_id INTEGER"
                ))

        # --- Add user_id to bank_deposits ---
        if "bank_deposits" in existing_tables:
            cols = {c["name"] for c in inspector.get_columns("bank_deposits")}
            if "user_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE bank_deposits ADD COLUMN user_id INTEGER"
                ))

    # Now create_all will create the users table and any missing tables
    Base.metadata.create_all(bind=engine)
