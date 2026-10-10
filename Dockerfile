FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# shapely / pyproj / numpy ship manylinux wheels that bundle GEOS and PROJ,
# so no apt packages or compiler are needed.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY sample_data ./sample_data

# Run as an unprivileged user; only the data folders are writable.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/uploads /app/data_store \
    && chown -R appuser:appuser /app/uploads /app/data_store
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
