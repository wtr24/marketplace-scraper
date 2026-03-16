# Official Playwright Python image — Ubuntu 22.04 (jammy) with Chromium pre-installed.
# Eliminates all "unsupported OS" apt failures from python:slim-based builds.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# Optional: Puppeteer stealth plugin (Node engine — node/npm included in base image)
# tzdata required for TZ env var (e.g. Europe/London) to work in tzlocal.
# DEBIAN_FRONTEND=noninteractive prevents the interactive timezone prompt.
RUN DEBIAN_FRONTEND=noninteractive TZ=UTC \
    apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g puppeteer-extra puppeteer-extra-plugin-stealth 2>/dev/null || true

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers already installed in base image at /ms-playwright —
# no `playwright install` step needed.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Copy application source
COPY . .

RUN mkdir -p /data /app/logs

ENV DATABASE_URL=sqlite:////data/scraper.db
ENV PORT=3000
ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=info
ENV TZ=Europe/London

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:3000/api/health || exit 1

CMD ["python", "main.py"]
