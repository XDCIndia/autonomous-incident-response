FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir flask==3.0.3

COPY app.py /app/app.py

EXPOSE 5000

# Add a healthcheck based on the /health route
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')"

CMD ["python", "app.py"]
