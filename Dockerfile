FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY sentinel_ops ./sentinel_ops
COPY scripts ./scripts
RUN python -m pip install --no-cache-dir .
EXPOSE 8931 8932
CMD ["python", "-m", "sentinel_ops"]
