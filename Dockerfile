FROM python:3.12-alpine

WORKDIR /app
COPY app /app

RUN pip3 install --no-cache-dir -r requirements.txt

ENV DATA_DIR=/data

EXPOSE 8099

CMD ["python3", "main.py"]
