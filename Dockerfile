FROM python:3.12-slim

# Install system dependencies needed for building C extensions / asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copy dependency specifications first for Docker layer caching
COPY pyproject.toml poetry.lock* /app/

# Install dependencies without creating a virtual environment inside container
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --no-interaction --no-ansi

# Copy project source code
COPY . /app/