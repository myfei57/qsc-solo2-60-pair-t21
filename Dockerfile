FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml /app/pyproject.toml
COPY waterplant /app/waterplant

RUN python -m compileall -q /app/waterplant

EXPOSE 8080

CMD ["python", "-m", "waterplant", "--store", "/data/state.json", "--addr", "0.0.0.0:8080"]
