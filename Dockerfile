# Readpath: one-command self-hosting.
#   docker compose up -d        then open http://localhost:8000
#
# Stages: build the web app, fetch veraPDF from Maven Central, then assemble
# a Python image with a Java runtime (for veraPDF) and OCRmyPDF (for scans).

# ---- 1. Web app ---------------------------------------------------------------
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- 2. veraPDF (PDF/UA validator) ----------------------------------------------
FROM maven:3.9-eclipse-temurin-21 AS verapdf
WORKDIR /build
COPY docker/verapdf/pom.xml .
RUN mvn -q -B dependency:copy-dependencies -DoutputDirectory=/opt/verapdf/lib

# ---- 3. Runtime -------------------------------------------------------------------
FROM python:3.12-slim-bookworm

ARG WITH_OCR=true
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    JAVA_HOME=/opt/java/openjdk \
    PATH=/opt/java/openjdk/bin:$PATH \
    READPATH_DATA=/data \
    READPATH_WEB_DIR=/app/web/dist \
    READPATH_HOST=0.0.0.0 \
    READPATH_PORT=8000

# Liberation fonts: exact-width substitutes embedded for Helvetica/Times/Courier.
# OCR for scanned PDFs (English and Spanish packs; add more tesseract-ocr-* as needed).
RUN apt-get update && apt-get install -y --no-install-recommends fonts-liberation2 && \
    if [ "$WITH_OCR" = "true" ]; then \
      apt-get install -y --no-install-recommends ocrmypdf tesseract-ocr-eng tesseract-ocr-spa ghostscript; \
    fi && \
    rm -rf /var/lib/apt/lists/*

# Java runtime + veraPDF
COPY --from=eclipse-temurin:21-jre /opt/java/openjdk /opt/java/openjdk
COPY --from=verapdf /opt/verapdf/lib /opt/verapdf/lib
COPY docker/verapdf/verapdf /usr/local/bin/verapdf
RUN chmod +x /usr/local/bin/verapdf

WORKDIR /app
COPY core/ /app/core/
COPY server/ /app/server/
RUN pip install /app/core /app/server
COPY --from=web /web/dist /app/web/dist

RUN useradd --system --create-home --uid 10001 readpath && mkdir -p /data && chown readpath /data
USER readpath
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status == 200 else 1)"
CMD ["readpath-server"]
