FROM python:3.12-slim

WORKDIR /app

ARG STERLING_BUILD_SHA=UNKNOWN
ARG STERLING_BUILD_TAG=UNKNOWN

LABEL org.opencontainers.image.revision="${STERLING_BUILD_SHA}" \
      org.opencontainers.image.version="${STERLING_BUILD_TAG}"

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.lock.txt .
RUN python -m pip install --no-cache-dir pip==26.2.0 && \
    python -m pip install --no-cache-dir -r requirements.lock.txt

COPY . .

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Bind the declared source revision to the exact immutable backend bytes copied
# into this image. Runtime verification recomputes the digest; runtime env vars
# are expectations only and cannot manufacture the executing identity.
RUN python -m app.core.build_artifact_identity write \
      --sha "${STERLING_BUILD_SHA}" \
      --tag "${STERLING_BUILD_TAG}" \
      --root /app \
      --output /app/.sterling-build.json

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
