FROM python:3.11-slim-bookworm AS base
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

# Shared OCR dependencies; this stage also supports offline evaluation.
FROM base AS ocr
USER root
ENV PIP_DEFAULT_TIMEOUT=120
# Resolve compatible FastAPI and pre-1.0 LangChain dependencies together.
RUN python -m pip install --no-cache-dir "numpy==1.26.4" "paddlepaddle==3.0.0" \
    "paddleocr==3.0.3" "paddlex[ocr]==3.0.3" "fastapi==0.115.12" \
    "langchain==0.3.25" "langchain-community==0.3.24" "langchain-openai==0.3.18" "langgraph==0.4.8" \
    && python -m pip check
ENV HOME=/cache GUIVIN_STATE_DIR=/tmp/ocr-evaluation GUIVIN_USERS_FILE="" \
    GUIVIN_WARMUP_MODELS=0 PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
USER 10001:10001
ENTRYPOINT ["python", "/app/tools/compare_paddleocr.py"]

FROM ocr AS comparison
USER root
RUN python -m pip install 'fast-alpr[onnx]==0.4.0' 'fast-plate-ocr==1.1.0' \
    'open-image-models==0.6.0' 'onnxruntime==1.30.0' 'opencv-python-headless==4.10.0.84' \
    && python -m pip check
USER 10001:10001

# Serving stage built from the dependency stages above.
FROM comparison AS runtime
USER root
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*
COPY backend/app /app/backend/app
COPY deploy/container_entrypoint.py /app/deploy/container_entrypoint.py
ENV HOME=/home/guivin GUIVIN_STATE_DIR=/var/lib/guivin \
    GUIVIN_USERS_FILE=/run/secrets/guivin_users GUIVIN_WARMUP_MODELS=1 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    PADDLE_PDX_LOCAL_FONT_FILE_PATH=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
USER 10001:10001
ENTRYPOINT ["python", "/app/deploy/container_entrypoint.py"]
