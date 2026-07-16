# AgentTable — single-process container (spec §3 deployment target).
# Runs behind an nginx reverse proxy; mount a volume at /app/data for SQLite.
FROM python:3.12-slim

WORKDIR /app

# deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite lives on a mounted volume so data survives container restarts
VOLUME ["/app/data"]
ENV DB_PATH=/app/data/agenttable.db
ENV PORT=8000
EXPOSE 8000

# reload disabled on purpose (keeps WebSocket connections & task loops alive)
CMD ["python", "run.py"]
