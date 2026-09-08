FROM python:3.12-slim

WORKDIR /app

RUN pip install flask

COPY app.py /app/

ENV PORT=5000
EXPOSE 5000

# Healthcheck mirroring payment/rpc services — without it Docker reports
# health status "none", which the dashboard renders as a bogus "None"/critical
# state for an otherwise running service.
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

CMD ["python", "app.py"]
