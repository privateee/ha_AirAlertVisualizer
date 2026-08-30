# Run DroneVisualizer anywhere as a container (like Node-RED).
#   docker build -t dronevis .
#   docker run -p 8750:8750 -v dronevis-data:/data dronevis
# Configure with DRONEVIS_* env vars (see config.py) or mount a config.yaml.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    DRONEVIS_HOST=0.0.0.0 \
    DRONEVIS_PORT=8750 \
    DRONEVIS_DB_PATH=/data/dronevis.db

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY dronevis ./dronevis
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8750

CMD ["python", "-m", "dronevis", "run"]
