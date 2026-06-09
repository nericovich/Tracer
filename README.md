# Portfolio Tracker

A self-hosted web application for tracking personal investments and bank deposits, built with FastAPI, SQLite, and Vue 3. Replace your complex Excel/CSV spreadsheets with a modern dashboard.

## Features

- 📊 Real-time portfolio dashboard with total value and asset allocation
- 💰 Track multiple asset holdings with cost basis and unrealized P&L
- 🏦 Manage bank deposits with APY tracking
- 📈 Visual portfolio history chart
- 💳 Complete transaction history (buy, sell, deposit, withdraw)
- 🎨 Dark-themed modern UI with Tailwind CSS
- 🐳 Docker & Docker Compose ready for easy deployment

## Tech Stack

- **Backend**: Python, FastAPI, SQLAlchemy, Pydantic
- **Database**: SQLite (self-contained, easy backup)
- **Frontend**: HTML, Vue 3 (via CDN), Tailwind CSS (via CDN), Chart.js
- **Deployment**: Docker & Docker Compose

## Quick Start

### Local Development (Linux/Mac/Windows with WSL)

1. **Clone and setup**
```bash
cd portfolio-tracker
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

2. **Run the application**
```bash
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

3. **Access the dashboard**
Open your browser to `http://localhost:8000`

### Docker Deployment (Recommended for VPS)

1. **Build and run**
```bash
docker-compose up --build -d
```

2. **Access the application**
Open your browser to `http://your-server-ip:8000`

3. **Stop the application**
```bash
docker-compose down
```

4. **View logs**
```bash
docker-compose logs -f portfolio-tracker
```

### Data Persistence

The SQLite database is stored in a Docker volume (`portfolio-data`). Your data persists even after restarting the container.

**To backup your database:**
```bash
docker cp portfolio-tracker:/app/data/portfolio.db ./portfolio-backup.db
```

**To restore a database:**
```bash
docker cp portfolio-backup.db portfolio-tracker:/app/data/portfolio.db
```

## API Endpoints

### Dashboard
- `GET /api/dashboard` - Get current portfolio state with holdings and valuations

### Transactions
- `POST /api/transactions` - Add a new transaction
  ```json
  {
    "date": "2024-01-15T10:30:00",
    "type": "buy_asset",
    "ticker": "BTC",
    "quantity": 0.5,
    "price_per_unit": 42000,
    "total_amount": 21000
  }
  ```

### Asset Prices
- `POST /api/prices` - Update an asset's current price
  ```json
  {
    "ticker": "BTC",
    "current_price": 45000
  }
  ```

### History
- `GET /api/history` - Get historical portfolio values for charting

### Health
- `GET /api/health` - Health check endpoint

## Database Schema

### Transactions
Stores all buy/sell/deposit/withdraw operations
- `id` (PK)
- `date` - Transaction timestamp
- `type` - One of: deposit_fiat, withdraw_fiat, buy_asset, sell_asset
- `ticker` - Asset symbol (nullable for fiat operations)
- `quantity` - Units bought/sold (nullable for fiat operations)
- `price_per_unit` - Unit price (nullable for fiat operations)
- `total_amount` - Total transaction amount in base currency

### Assets Prices
Current price of each asset/ticker
- `ticker` (PK)
- `current_price` - Current market price
- `last_updated` - Last update timestamp

### Bank Deposits
Active bank deposits with interest tracking
- `id` (PK)
- `bank_name` - Name of the bank
- `amount` - Deposit amount
- `start_date` - When deposit started
- `end_date` - When deposit ends
- `apy_percent` - Annual percentage yield
- `expected_profit` - Expected interest earned

### Portfolio History
Daily snapshots of portfolio value
- `date` (PK)
- `total_value` - Total portfolio value on that day
- `daily_change_percent` - Percentage change from previous day

## UI Components

### Summary Cards
Shows at a glance:
- Total portfolio value with unrealized P&L
- Total capital invested
- Total in assets
- Total in bank deposits

### Asset Allocation Table
Displays all holdings with:
- Ticker and quantity
- Current price per unit
- Total value
- Portfolio allocation percentage
- Cost basis and unrealized P&L

### Bank Deposits Table
Shows active deposits with:
- Bank name and deposit amount
- APY percentage
- Expected profit
- End date and days remaining

### Portfolio History Chart
Interactive line chart showing portfolio value over time

## Example Usage

### Adding a deposit
```json
POST /api/transactions
{
  "date": "2024-01-15T10:00:00",
  "type": "deposit_fiat",
  "total_amount": 10000
}
```

### Buying an asset
```json
POST /api/transactions
{
  "date": "2024-01-15T11:00:00",
  "type": "buy_asset",
  "ticker": "BTC",
  "quantity": 0.5,
  "price_per_unit": 42000,
  "total_amount": 21000
}
```

### Updating asset price
```json
POST /api/prices
{
  "ticker": "BTC",
  "current_price": 45000
}
```

## Financial Calculations

### Cost Basis
Sum of all purchase amounts for an asset, minus sale proceeds.

### Unrealized P&L
Current value minus cost basis: `(quantity * current_price) - cost_basis`

### Portfolio Allocation %
Each asset's value divided by total portfolio value.

### Daily Change %
Percentage change from previous day's total portfolio value.

## Deployment on Ubuntu VPS

1. **SSH into your server**
```bash
ssh user@your-server-ip
```

2. **Install Docker and Docker Compose**
```bash
sudo apt update
sudo apt install docker.io docker-compose -y
sudo usermod -aG docker $USER
```

3. **Clone your repository**
```bash
git clone <your-repo-url>
cd portfolio-tracker
```

4. **Start the application**
```bash
docker-compose up -d
```

5. **Set up SSL (recommended with nginx reverse proxy)**
```bash
# Install nginx
sudo apt install nginx -y

# Configure nginx as reverse proxy
# Create /etc/nginx/sites-available/portfolio with:
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

# Enable and start nginx
sudo ln -s /etc/nginx/sites-available/portfolio /etc/nginx/sites-enabled/
sudo systemctl restart nginx

# Get SSL certificate (Let's Encrypt)
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your-domain.com
```

## Troubleshooting

### Database not persisting
Ensure the volume is properly mounted:
```bash
docker volume ls
```

### Port already in use
Change the port in `docker-compose.yml`:
```yaml
ports:
  - "8001:8000"  # Use 8001 instead
```

### Database corruption
The app creates a backup before migrations. Check `/app/data/` in the container.

## Future Enhancements

- [ ] User authentication and multi-user support
- [ ] CSV import/export
- [ ] Tax reporting (capital gains)
- [ ] Dividend tracking
- [ ] Real-time price feeds (CoinGecko, yfinance)
- [ ] Mobile app
- [ ] Email alerts for price thresholds
- [ ] Portfolio performance comparison

## License

MIT

## Support

For issues or questions, create an issue in the repository.
