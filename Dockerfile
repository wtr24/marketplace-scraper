FROM python:3.12-slim
WORKDIR /app

# System libraries needed by Playwright's bundled Chromium + runtime tools.
# curl is needed for the HEALTHCHECK.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Optional: Puppeteer stealth plugin (Node engine)
RUN npm install -g puppeteer-extra puppeteer-extra-plugin-stealth 2>/dev/null || true

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's bundled Chromium.
# Run apt-get update HERE (fresh) so --with-deps can install its own system libs.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update \
    && playwright install chromium --with-deps \
    && rm -rf /var/lib/apt/lists/*

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
