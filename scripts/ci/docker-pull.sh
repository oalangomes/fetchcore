#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 <image@sha256:digest>" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
image="$1"

if [[ ! "$image" =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "container image must be pinned by sha256 digest: $image" >&2
  exit 2
fi

docker_bin="${DOCKER_BIN:-docker}"
max_attempts="${FETCHCORE_DOCKER_PULL_MAX_ATTEMPTS:-3}"
base_delay="${FETCHCORE_DOCKER_PULL_BASE_DELAY_SECONDS:-5}"

case "$max_attempts" in
  *[!0-9]*|"") echo "FETCHCORE_DOCKER_PULL_MAX_ATTEMPTS must be numeric" >&2; exit 2 ;;
esac
case "$base_delay" in
  *[!0-9]*|"") echo "FETCHCORE_DOCKER_PULL_BASE_DELAY_SECONDS must be numeric" >&2; exit 2 ;;
esac
(( max_attempts >= 1 && max_attempts <= 10 )) || {
  echo "FETCHCORE_DOCKER_PULL_MAX_ATTEMPTS must be between 1 and 10" >&2
  exit 2
}

is_transient() {
  local message="${1,,}"
  [[ "$message" =~ timeout|timed[[:space:]]out|context[[:space:]]deadline[[:space:]]exceeded ]] ||
    [[ "$message" =~ tls[[:space:]]handshake|connection[[:space:]]reset|connection[[:space:]]refused ]] ||
    [[ "$message" =~ temporary[[:space:]]failure|unexpected[[:space:]]eof ]] ||
    [[ "$message" =~ (^|[^0-9])(429|500|502|503|504)([^0-9]|$) ]] ||
    [[ "$message" =~ too[[:space:]]many[[:space:]]requests|service[[:space:]]unavailable|gateway[[:space:]]timeout ]]
}

attempt=1
while (( attempt <= max_attempts )); do
  echo "pulling pinned container image (attempt $attempt/$max_attempts): $image"
  set +e
  output="$("$docker_bin" pull "$image" 2>&1)"
  status=$?
  set -e
  if (( status == 0 )); then
    printf '%s\n' "$output"
    exit 0
  fi
  printf '%s\n' "$output" >&2

  if ! is_transient "$output" || (( attempt == max_attempts )); then
    echo "container image pull failed without further retry: $image" >&2
    exit "$status"
  fi

  delay=$((base_delay * (1 << (attempt - 1))))
  jitter=0
  if (( delay > 0 )); then
    jitter=$((RANDOM % (base_delay + 1)))
  fi
  delay=$((delay + jitter))
  echo "transient registry failure; retrying in ${delay}s" >&2
  sleep "$delay"
  attempt=$((attempt + 1))
done
