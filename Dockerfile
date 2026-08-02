# Render worker image: Python + LibreOffice (for docx -> pdf)
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-core libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "runner.py", "--interval", "15"]