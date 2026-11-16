FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --gid 10001 syncworker && useradd --uid 10001 --gid 10001 --no-create-home syncworker && mkdir /data && chown 10001:10001 /data
COPY pyproject.toml requirements.lock ./
COPY src ./src
RUN python -m pip install --requirement requirements.lock && python -m pip install --no-deps . && python -m pip check
COPY scripts/check_runtime_lock.py ./scripts/check_runtime_lock.py
ARG VCS_REF=unknown
LABEL org.opencontainers.image.version="0.2.0.dev0" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/salimsaidhemed/calendar-sync"
USER 10001:10001
CMD ["calendar-sync"]
