# Customer Churn RAG System - Multi-stage Dockerfile
# Stage 1: Base Python environment with uv
FROM python:3.12-slim AS base

# Install system dependencies and uv
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV UV_CACHE_DIR=/tmp/uv-cache

# 
# File structure in container:
# 
# /app/
# ├── src/
# │   ├── backend/
# │   │   └── api.py  ← API runs from here
# │   ├── core/
# │   ├── agents/
# │   └── ...
# ├── data/
# ├── cache/
# └── notebooks/
# 

# Copy project files for dependency installation
COPY pyproject.toml ./
COPY README.md ./
# Copy uv.lock for reproducible builds (if it exists)
COPY uv.lock* ./

# Install dependencies using uv (much faster than pip)
# Runtime dependencies only. The evaluation, notebook and plotting extras are
# deliberately excluded -- they were ~450 MB of an image that never imports them.
RUN uv sync --no-dev --no-install-project --extra model || uv pip install --system -e .

# Copy source code
COPY src/ ./src/

# Bake the dataset into the image rather than relying on a bind mount.
# docker-compose mounts these from the host, but ECS and any other orchestrator
# without a host filesystem would leave them empty -- and because startup is
# fault-tolerant, the API would boot "healthy" and serve nothing. ~2 MB.
COPY data/ ./data/
COPY golden-masters/ ./golden-masters/

# Written to at runtime, so created rather than copied.
RUN mkdir -p cache notebooks metrics

# Stage 1b: Derived artifacts.
#
# The warehouse and the fitted model are derived from data/, not committed --
# a committed binary drifts from the code that produced it, silently. So they
# are built here instead, which also proves on every image build that both
# rebuild from a clean checkout.
#
# A separate stage on purpose: dbt and the training dependencies are ~200 MB and
# the running service imports neither. Only the two artifacts are copied forward.
FROM base AS artifacts

COPY warehouse/ ./warehouse/
COPY scripts/ ./scripts/

RUN uv sync --no-dev --no-install-project --extra warehouse --extra model \
 && cd warehouse && DBT_PROFILES_DIR=$PWD uv run dbt build \
 && cd /app && uv run python scripts/train_survival_model.py

# Stage 2: FastAPI Backend Service
FROM base AS jupyter

# Jupyter, plotting and evaluation are extras now, so this stage installs them.
# The backend stage deliberately does not.
RUN uv sync --no-dev --no-install-project \
        --extra notebook --extra visualization --extra evaluation \
    || uv pip install --system -e ".[notebook,visualization,evaluation]"

# Expose Jupyter port
EXPOSE 8888

# Create Jupyter config directory
RUN mkdir -p /root/.jupyter

# Configure Jupyter
RUN echo "c.ServerApp.ip = '0.0.0.0'" >> /root/.jupyter/jupyter_lab_config.py \
    && echo "c.ServerApp.port = 8888" >> /root/.jupyter/jupyter_lab_config.py \
    && echo "c.ServerApp.open_browser = False" >> /root/.jupyter/jupyter_lab_config.py \
    && echo "c.ServerApp.allow_root = True" >> /root/.jupyter/jupyter_lab_config.py

# Default command (can be overridden in docker-compose)
CMD ["uv", "run", "jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root", "--NotebookApp.token=''", "--NotebookApp.password=''"]



# Stage order matters. A build with no --target builds the LAST stage, and
# that is what Render and most managed hosts do. With jupyter last, such a
# build produced an image with the data but no model and no warehouse, and
# the service came up reporting 'no model at models/survival.joblib' while
# the correct commit was deployed. Backend is last so the default is right.

FROM base AS backend

# The warehouse feeds the likelihood band and the exposure figures; without it
# /customer/{id}/likelihood and /book/exposure return 503 and the demo loses the
# two things it is actually for.
COPY --from=artifacts /app/warehouse/churnguard.duckdb ./warehouse/churnguard.duckdb
COPY --from=artifacts /app/models/ ./models/

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the full API server with multi-agent RAG system
CMD ["uv", "run", "python", "/app/src/backend/api.py"]

# Stage 3: Jupyter Notebook Server