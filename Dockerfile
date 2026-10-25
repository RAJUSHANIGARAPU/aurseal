FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p reports/logs reports/videos reports/traces reports/screenshots

ENTRYPOINT ["pytest"]