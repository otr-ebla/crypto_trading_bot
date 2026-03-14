# Use official Python runtime as a parent image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements or setup files first to leverage Docker cache
COPY pyproject.toml README.md ./

# Install python dependencies
RUN pip install --no-cache-dir -e ".[dev]"

# Copy the rest of the application code
COPY . .

# Expose the dashboard port
EXPOSE 8080

# Run the trading bot dashboard by default
CMD ["python", "-m", "src.cli", "dashboard", "--host", "0.0.0.0", "--port", "8080"]
