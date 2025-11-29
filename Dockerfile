FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y \
    wget gnupg curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Playwright browsers
RUN pip install playwright && playwright install chromium

# Set working directory
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENV PORT=8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port=8000"]
