FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY models.py .
COPY database.py .
COPY main.py .
COPY index.html .

# Create a directory for the SQLite database
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Set environment variables
ENV DATABASE_URL=sqlite:////app/data/portfolio.db
ENV PYTHONUNBUFFERED=1

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
