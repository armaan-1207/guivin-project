FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --extra-index-url https://download.pytorch.org/whl/cpu -r backend/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 guivin && useradd --uid 10001 --gid guivin --create-home guivin \
    && mkdir -p /var/lib/guivin /app/tmp && chown -R guivin:guivin /var/lib/guivin /app/tmp
COPY backend/app /app/backend/app
COPY backend/data /app/backend/data
COPY frontend /app/frontend
COPY deploy/container_entrypoint.py /app/deploy/container_entrypoint.py
USER 10001:10001
ENV GUIVIN_STATE_DIR=/var/lib/guivin GUIVIN_USERS_FILE=/run/secrets/guivin_users \
    YOLO_MODEL=/models/yolov8n.pt EASYOCR_MODULE_PATH=/models/easyocr \
    YOLO_CONFIG_DIR=/tmp/yolo MPLCONFIGDIR=/tmp/matplotlib \
    OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;tcp"
EXPOSE 8000
ENTRYPOINT ["python", "/app/deploy/container_entrypoint.py"]
