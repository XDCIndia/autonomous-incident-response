FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir flask==3.1.*

COPY app.py .

EXPOSE 5000

# Python-based healthcheck (no curl dependency)
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

CMD ["python", "app.py"]
