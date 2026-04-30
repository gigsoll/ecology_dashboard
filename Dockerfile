FROM python:3.14-bookworm AS builder

# Install UV
COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /uvx /bin/
# Disable Dev modules
ENV UV_NO_DEV=1

# Optimization env params
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1


# Copy project installation files
WORKDIR /app
COPY ./uv.lock /app
COPY ./pyproject.toml /app

# Install dependencies
RUN uv sync --locked

FROM python:3.14-slim-bookworm

# Create user and app dir
RUN useradd -m -r appuser && \
    mkdir /app && \
    chown -R appuser /app

# Install libraries for MySQL
RUN apt-get update && apt-get install -y \
    libmariadb-dev-compat \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages
COPY --from=builder /app/.venv/lib/python3.14/site-packages/ /usr/local/lib/python3.14/site-packages/

# Give workdir for user and copy the files
WORKDIR /app
COPY --chown=appuser:appuser ./app /app/app
COPY --chown=appuser:appuser ./alembic /app/alembic
COPY --chown=appuser:appuser ./alembic.ini /app/alembic.ini
COPY --chown=appuser:appuser ./main.py /app/main.py
COPY --chown=appuser:appuser ./run_etl.py /app/run_etl.py

# Set environment variables to optimize Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Switch to user
USER appuser

# Expose port 
EXPOSE 8000

# Program entrypoint
CMD [ "fastapi", "dev" ]
