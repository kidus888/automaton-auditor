FROM python:3.11-slim

# git is required at runtime: RepoInvestigator clones target repos via sandboxed subprocess calls
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv pip install --system --no-cache-dir -e .

COPY . .

ENTRYPOINT ["python", "main.py"]
