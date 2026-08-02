# Render worker image: lightweight Python only (lightweight -> fast deploy)
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "runner.py", "--interval", "15"]