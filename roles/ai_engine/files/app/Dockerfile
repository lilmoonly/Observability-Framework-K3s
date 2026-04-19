FROM python:3.11.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --disable-pip-version-check -r /app/requirements.txt

COPY main.py /app/main.py

CMD ["python", "/app/main.py"]
