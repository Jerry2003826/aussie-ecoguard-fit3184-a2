FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=4 PORT=8080 SERVICE_AUTH_MODE=cloud-run
WORKDIR /app
RUN groupadd --gid 10001 ecoguard && useradd --uid 10001 --gid ecoguard --create-home ecoguard
COPY services/inference/requirements.lock.txt ./requirements.lock.txt
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.lock.txt
COPY services/inference/app ./app
COPY config /config
ENV MODEL_MANIFEST_PATH=/config/model.example.json TAXONOMY_PATH=/config/taxonomy.json MODEL_CACHE_DIR=/tmp/ecoguard-models
USER 10001:10001
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --no-access-log"]
