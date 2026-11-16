#!/bin/sh
# Isolated local fixture only; never reads .env.web or customer Compose files.
set -eu
cd "$(dirname "$0")/.."
project="${LOCAL_WEB_UAT_PROJECT:-calendar-sync-keycloak-local}"
case "$project" in calendar-sync-keycloak-local|calendar-sync-keycloak-local-*) ;; *) echo 'Local fixture project prefix required' >&2; exit 2;; esac
case "$project" in *[!a-z0-9_-]*) echo 'Invalid local fixture project name' >&2; exit 2;; esac
compose() { docker compose -p "$project" -f examples/keycloak/compose.yaml "$@"; }
case "${1:-}" in
  up)
    if [ "${LOCAL_WEB_UAT_SKIP_BUILD:-false}" != true ]; then
      docker build --build-arg VCS_REF="$(git rev-parse HEAD)" -t "${LOCAL_WEB_UAT_IMAGE:-calendar-sync:keycloak-local}" .
    fi
    docker build --build-arg BASE_IMAGE="${LOCAL_WEB_UAT_IMAGE:-calendar-sync:keycloak-local}" \
      -t "${LOCAL_WEB_UAT_FIXTURE_IMAGE:-calendar-sync:keycloak-local-fixture}" -f examples/keycloak/Dockerfile .
    compose up -d --wait --wait-timeout 240 web
    printf '\nApp: https://localhost:8443\nKeycloak: https://localhost:9443\n'
    printf 'Show generated test logins: bash scripts/local_keycloak.sh credentials\n'
    ;;
  test) compose --profile tools run --rm --no-deps tester ;;
  credentials|ca) compose run --rm --no-deps init python /fixture-code/fixture.py "$1" ;;
  status) compose ps ;;
  logs) compose logs --tail=100 ;;
  stop) compose stop web keycloak gateway ;;
  down) compose down --remove-orphans ;;
  reset)
    if [ "${2:-}" != --delete-local-test-volumes ]; then
      echo 'Refused: reset requires --delete-local-test-volumes; only this named local fixture is eligible' >&2; exit 2
    fi
    compose down -v --remove-orphans
    ;;
  *) echo 'Usage: bash scripts/local_keycloak.sh up|test|credentials|ca|status|logs|stop|down|reset --delete-local-test-volumes' >&2; exit 2 ;;
esac
