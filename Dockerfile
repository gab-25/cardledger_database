FROM ghcr.io/astral-sh/uv:0.9.0-python3.14-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY cardledger_database ./cardledger_database
RUN uv sync --locked --no-dev --no-editable


FROM python:3.14-slim-bookworm

RUN useradd --create-home --uid 1000 cardledger
WORKDIR /app

COPY --from=build /app/.venv /app/.venv
COPY corrections ./corrections

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN mkdir out .cache && chown cardledger:cardledger out .cache
USER cardledger

VOLUME ["/app/out", "/app/.cache"]
ENTRYPOINT ["cardledger-db"]
CMD ["--help"]
