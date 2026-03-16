# ── Stage 1: Python app ───────────────────────────────────────────────────────
FROM python:3.12-slim AS app

WORKDIR /app

# System dependencies for Playwright / Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium & driver
    chromium \
    # Playwright system deps
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libcairo2 \
    # Node.js (for Puppeteer engine — optional)
    nodejs \
    npm \
    # Build tools
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Puppeteer extras (optional — skip if Node not needed)
RUN npm install -g puppeteer-extra puppeteer-extra-plugin-stealth 2>/dev/null || true

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium --with-deps

# Copy application source
COPY . .

# Persistent data directory
RUN mkdir -p /data /app/logs

# Environment
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
