#!/bin/sh
# Registers the default Temporal namespace, then keeps running so the Temporal CLI is at hand
# (compose service temporal-admin-tools):
#
#   docker compose -f deploy/compose/docker-compose.dev.yaml exec temporal-admin-tools \
#       temporal workflow list
#
# The temporal CLI reads the server address from TEMPORAL_ADDRESS. The service turns healthy
# once the namespace can be described (its compose healthcheck). Based on Temporal's official
# compose setup (temporalio/samples-server, compose/scripts/create-namespace.sh).
set -eu

NAMESPACE=default
RETENTION=72h
MAX_ATTEMPTS=60

attempt=1
until temporal operator namespace describe --namespace "$NAMESPACE" >/dev/null 2>&1; do
    if [ "$attempt" -gt "$MAX_ATTEMPTS" ]; then
        echo "Namespace '$NAMESPACE' is not available after $MAX_ATTEMPTS attempts." >&2
        exit 1
    fi
    # Fails while the server is still starting; the next attempt retries.
    temporal operator namespace create --namespace "$NAMESPACE" --retention "$RETENTION" || true
    attempt=$((attempt + 1))
    sleep 2
done
echo "Namespace '$NAMESPACE' is registered."

# Stay up for `docker compose exec`; exit at once on `docker compose stop`.
trap 'exit 0' INT TERM
sleep infinity &
wait
