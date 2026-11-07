FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --gid 10001 syncworker && useradd --uid 10001 --gid 10001 --no-create-home syncworker && mkdir /data && chown 10001:10001 /data
COPY pyproject.toml ./
COPY src ./src
RUN pip install .
USER 10001:10001
CMD ["calendar-sync"]
