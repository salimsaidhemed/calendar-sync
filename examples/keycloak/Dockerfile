ARG BASE_IMAGE=calendar-sync:keycloak-local
FROM ${BASE_IMAGE}
USER 0:0
COPY examples/keycloak /fixture-code
RUN chmod 0755 /fixture-code && chmod 0644 /fixture-code/*
USER 10001:10001
