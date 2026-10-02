FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Create non-root system user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/sh -m appuser

# Copy pre-exported requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and migrations
COPY app /app/app
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini

# Set permissions and switch user
RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000