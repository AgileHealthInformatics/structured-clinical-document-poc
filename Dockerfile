# EHDS Structured Clinical Document PoC - synthetic data only.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCDPOC_HOME=/app \
    SCDPOC_DATA_DIR=/data \
    SCDPOC_BASE_URL=http://127.0.0.1:8080

WORKDIR /app
COPY pyproject.toml requirements.lock README.md LICENSE NOTICE.md ./
COPY src ./src
COPY config ./config
COPY fixtures ./fixtures
RUN pip install --no-cache-dir -r requirements.lock . \
 && useradd --system --uid 10001 --home /app scdpoc \
 && mkdir -p /data && chown scdpoc /data

USER scdpoc
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"
CMD ["scdpoc", "serve", "--host", "0.0.0.0", "--port", "8080"]
