FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 openvpn socat \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir requests urllib3

WORKDIR /app
COPY src/rrk/ ./
COPY docker/entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
