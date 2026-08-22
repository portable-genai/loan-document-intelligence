# B5 Loan / Mortgage Document Intelligence : API service image.
#
# Builds the FastAPI service with the managed-stack extra ([gcp]) installed, so the deployed
# container talks to Document AI / Gemini / Model Armor / DLP / Cloud Logging in
# asia-southeast1. The image is region-agnostic at build time; residency is enforced at
# runtime via config/settings.yaml (region pinned) and the deploy environment.

# --------------------------------------------------------------------------- #
# Builder : install dependencies into a venv we can copy into a slim runtime.
# --------------------------------------------------------------------------- #
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only what the build needs first, for better layer caching.
COPY pyproject.toml README.md ./
COPY requirements-gcp.lock ./
COPY src ./src
COPY config ./config

# Install the package WITH the managed-stack extra.
RUN pip install --upgrade pip \
 && pip install -r requirements-gcp.lock && pip install --no-deps .

# --------------------------------------------------------------------------- #
# Runtime : slim, non-root, venv copied from builder.
# --------------------------------------------------------------------------- #
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    LOAN_DOC_PROFILE=gcp \
    LOAN_DOC_SETTINGS=/app/config/settings.yaml \
    PORT=8092

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv
COPY src ./src
COPY config ./config

USER appuser
EXPOSE 8092

# The API exposes /healthz for liveness/readiness.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8092')+'/healthz')" || exit 1

# Use the shell form so $PORT is expanded at container start.
CMD exec uvicorn loan_doc_intel.api.app:app --host 0.0.0.0 --port ${PORT}
