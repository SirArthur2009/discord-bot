# Use Python 3.11 slim image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.7.1

# Install system dependencies for building packages like lxml and aiohttp
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
        libffi-dev \
        libssl-dev \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Optional: Create and activate a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip and setuptools
RUN pip install --upgrade pip setuptools wheel

# Copy your requirements file
COPY requirements.txt /app/requirements.txt
WORKDIR /app

# Install Python dependencies
RUN pip install -r requirements.txt

# Copy the rest of your app
COPY . /app

# Default command to run your app
CMD ["python", "main.py"]

