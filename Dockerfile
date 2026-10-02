# Stage 1: build the web app
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: pipeline + API, serving the built web app at /
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY evcharge ./evcharge
COPY dbt ./dbt
RUN pip install --no-cache-dir -e ".[serve]"
COPY --from=web /web/dist ./web/dist
EXPOSE 8000
CMD ["uvicorn", "evcharge.api:app", "--host", "0.0.0.0", "--port", "8000"]
