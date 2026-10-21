FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py analysis.py storage.py history_source.py official_source.py collector.py gunicorn.conf.py ./
COPY templates ./templates
COPY static ./static

ENV PORT=5000
EXPOSE 5000
VOLUME ["/app/data"]

CMD ["gunicorn", "--config", "gunicorn.conf.py", "--bind", "0.0.0.0:5000", "app:app"]
