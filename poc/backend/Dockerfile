FROM python:3.11-slim AS builder

# spacy-transformers (pulled in by the en_core_web_trf model below) depends on torch. PyPI's
# default linux torch wheel bundles the full CUDA toolkit (several GB) even though this
# container only ever runs on CPU — this extra index carries CPU-only builds, and pip prefers
# them because "+cpu" sorts as a higher local version than the bare CUDA build (PEP 440).
ENV PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e . \
    && python -m spacy download en_core_web_trf

COPY . .


FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

# The scrape tool (app/agents/tools/web_scraper.py) drives a real headless browser so
# JS-rendered pages load fully before scraping — `--with-deps` pulls in the system libraries
# Chromium needs (fonts, codecs, etc.) that aren't part of the slim base image.
RUN playwright install --with-deps chromium

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
