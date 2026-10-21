FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
# The ONNX embedder: the same model as the torch one, without torch (a much smaller image, and
# about 300 MiB resident instead of 600+, so a 512 MiB host can run it).
RUN pip install --no-cache-dir ".[onnx]"
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
# Bake the embedding model into the image so containers start offline.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
ENV REPOCOMPASS_EMBEDDER_BACKEND=onnx
ENV REPOCOMPASS_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
