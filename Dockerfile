FROM python:3.12-slim

ARG VERSION=dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANMON_DATA_DIR=/data \
    LANMON_VERSION=${VERSION}

WORKDIR /src
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install . && rm -rf /src

WORKDIR /data
VOLUME /data
EXPOSE 8765

# Rulează ca root în container, dar compose renunță la toate capabilitățile în afară de NET_RAW
# (necesar pentru ARP și ping prin socket raw).
CMD ["python", "-m", "app"]
