FROM python:3.13

# For pyppeteer
RUN apt-get update && apt-get install -y chromium && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.7.8 /uv /uvx /bin/
COPY pyproject.toml uv.lock .
RUN uv sync --frozen --no-install-project --no-dev
ENV PATH="/app/.venv/bin:$PATH"

RUN uv run pyppeteer-install

COPY . /app
RUN uv sync --frozen --no-dev

ARG GIT_COMMIT_HASH
ENV GIT_COMMIT_HASH=$GIT_COMMIT_HASH

CMD ["python", "bot.py"]
