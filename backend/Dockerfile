# Сборка из корня репозитория: docker compose build api  (context: ., dockerfile: backend/Dockerfile)
FROM python:3.12-slim

# Один поток BLAS: расчёты небольшие, а лишние потоки только мешают друг другу и дают скачки задержки
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

RUN pip install --no-cache-dir uv

# backend лежит в /srv, данные и отчёты — в /data и /reports (см. app/config.py)
WORKDIR /srv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
ENV PATH="/srv/.venv/bin:$PATH"

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/tests/fixtures ./tests/fixtures
COPY reports /reports

EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
