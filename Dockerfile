# Render worker image: lightweight Python only (lightweight -> fast deploy)
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# LibreOffice optional — uncomment to send PDF attachments instead of DOCX:
# RUN apt-get update && apt-get install -y --no-install-recommends libreoffice && rm -rf /var/lib/apt/lists/*

CMD ["python", "runner.py", "--interval", "15"]