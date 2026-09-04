FROM python:3.12-slim

WORKDIR /app

RUN pip install flask

COPY app.py /app/

ENV PORT=5000
EXPOSE 5000

CMD ["python", "app.py"]
