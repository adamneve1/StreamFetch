FROM node:22-slim AS frontend
WORKDIR /frontend
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY scripts/build-frontend.mjs ./scripts/build-frontend.mjs
RUN npm run build

FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        unzip \
        ca-certificates \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Deno where the non-root runtime user can execute it.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

RUN pip install --no-cache-dir --upgrade "yt-dlp[default,curl-cffi]"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --from=frontend /frontend/app/static/vendor ./app/static/vendor

RUN mkdir -p /downloads

CMD ["python", "-u", "app/worker.py"]
