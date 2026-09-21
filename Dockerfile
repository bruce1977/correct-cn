# correct-cn API image
# Python 3.11 slim image keeps the base layer small.
FROM python:3.12-slim

# Runtime environment. HF/Transformers are forced offline so the baked-in model
# is used and no network call is made at startup.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false \
    DATA_DIR=/data \
    CORRECTOR_MODE=model \
    OLLAMA_BASE_URL=http://host.docker.internal:11434 \
    OLLAMA_MODEL=qwen3.5:9b

WORKDIR /app

# Install Python dependencies first to leverage Docker layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source.
COPY app ./app

# Ensure /data directory exists for volume mount.
RUN mkdir -p /data

# Bake the pre-downloaded correction model into the offline HF cache so the
# container works without internet access.
COPY models/huggingface /root/.cache/huggingface

# Mounted at runtime: user-editable config + sensitive-word dictionaries.
# On first boot the service seeds it with the built-in defaults.
VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
