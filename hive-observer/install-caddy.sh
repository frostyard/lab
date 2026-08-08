#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

source_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
for file in Caddyfile caddy-hive-observer.conf hive-observer.env; do
  if [[ ! -f "${source_dir}/${file}" ]]; then
    echo "Missing staged file: ${source_dir}/${file}" >&2
    exit 1
  fi
done

install -m 0644 "${source_dir}/Caddyfile" /etc/caddy/hive-observer.caddy
install -m 0600 "${source_dir}/hive-observer.env" /etc/caddy/hive-observer.env
install -d -m 0755 /etc/systemd/system/caddy.service.d
install -m 0644 "${source_dir}/caddy-hive-observer.conf" \
  /etc/systemd/system/caddy.service.d/hive-observer.conf

candidate=$(mktemp /tmp/Caddyfile.hive-observer.XXXXXX)
trap 'rm -f "${candidate}"' EXIT
cp /etc/caddy/Caddyfile "${candidate}"
if ! grep -Fxq 'import /etc/caddy/hive-observer.caddy' "${candidate}"; then
  printf '\nimport /etc/caddy/hive-observer.caddy\n' >>"${candidate}"
fi

# sudo does not preserve the Caddy service's Cloudflare token. The imported
# bespoke routes need it during validation, so recover that one named value
# from the running unit without printing the service environment.
if [[ -z ${CLOUDFLARE_API_TOKEN:-} ]]; then
  service_environment=$(systemctl show caddy --property=Environment --value)
  if [[ ${service_environment} =~ CLOUDFLARE_API_TOKEN=([A-Za-z0-9_-]+) ]]; then
    export CLOUDFLARE_API_TOKEN=${BASH_REMATCH[1]}
  fi
fi
if [[ -z ${CLOUDFLARE_API_TOKEN:-} ]]; then
  echo "Caddy's CLOUDFLARE_API_TOKEN is unavailable for validation." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source /etc/caddy/hive-observer.env
set +a
caddy validate --config "${candidate}" --adapter caddyfile

backup="/etc/caddy/Caddyfile.pre-hive-observer.$(date -u +%Y%m%dT%H%M%SZ)"
install -m 0644 /etc/caddy/Caddyfile "${backup}"
install -m 0644 "${candidate}" /etc/caddy/Caddyfile

systemctl daemon-reload
if ! systemctl restart caddy; then
  echo "Caddy restart failed; restoring ${backup}." >&2
  install -m 0644 "${backup}" /etc/caddy/Caddyfile
  systemctl restart caddy
  exit 1
fi
rm -f "${source_dir}/hive-observer.env"

echo "Installed hive.frostyard.org; previous Caddyfile: ${backup}"
