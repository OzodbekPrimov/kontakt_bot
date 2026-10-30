FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md requirements.txt ./
COPY app ./app
RUN pip install --no-cache-dir -r requirements.txt .

CMD ["python", "-m", "app.main"]
