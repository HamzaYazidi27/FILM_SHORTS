FROM python:3.12-slim

RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-inter espeak-ng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY shortsengine ./shortsengine
COPY research ./research
COPY assets ./assets
RUN pip install --no-cache-dir .

ENV SHORTS_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000
# dashboard + background worker (scheduler/queue); state persists in /data
CMD ["python", "-m", "shortsengine", "serve", "--host", "0.0.0.0", "--port", "8000"]
