FROM python:3.11-slim

WORKDIR /app

# System deps for pyodbc (MSSQL/Oracle may need extra drivers; add as needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc unixodbc-dev curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install base + selected drivers; comment out what you don't need.
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
