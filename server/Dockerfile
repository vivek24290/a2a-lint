# Built from the repo root (compose sets build.context: .) so the image can
# pip-install the a2a_lint package the server depends on.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY a2a_lint/ a2a_lint/
RUN pip install --no-cache-dir . "fastapi>=0.110" "uvicorn>=0.29"
COPY server/main.py server/store.py server/monitor.py ./
COPY server/static/ static/
EXPOSE 8090
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
