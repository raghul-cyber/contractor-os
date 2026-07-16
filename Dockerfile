FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast python package management (optional, but requested in prompt)
# RUN pip install --no-cache-dir pip setuptools wheel uv
RUN pip install --no-cache-dir pip setuptools wheel

# Copy pyproject.toml
COPY pyproject.toml ./

# Install dependencies including playwright
RUN pip install --no-cache-dir .

# Install playwright browsers
RUN playwright install --with-deps chromium

# Copy application code
COPY . .

# Run uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
