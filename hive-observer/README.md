# Hive observer

The Hive observer publishes one intentionally small, read-only JSON contract
for the public lab site. It talks to Hive on the cluster network, removes
transcripts and other sensitive fields, and accepts public requests only when
Caddy supplies a separate proxy token.

The service is reconciled by the `frostyard-hive-observer` Argo CD Application.
Kustomize packages `observer.py` into a hash-named ConfigMap, so a code change
rolls the Deployment without a custom image build.

## Security boundary

Hive's `X-Hive-Internal` credential is fully trusted by Hive; it is not a
read-only token. The observer therefore has no generic proxy behavior and
contains compile-time mappings for the three upstream GET endpoints it uses.
Only the `dashboard-token` item from `hive-secrets` is projected into the pod;
the GitHub App private key is not mounted.

The public response excludes agent output and errors, prompts, issue and pull
request content, individual sessions, models, cadence, auth state, ACMM,
beads, configuration, audit data, knowledge data, and host capacity.

## Secret and bootstrap

Generate a separate credential for Caddy:

```bash
PROXY_TOKEN="$(openssl rand -hex 32)"
kubectl create secret generic hive-observer-secrets -n hive \
  --from-literal=proxy-token="${PROXY_TOKEN}"
```

Store the same value on the Caddy VM as `HIVE_OBSERVER_TOKEN`, then bootstrap
the Argo CD Application:

```bash
just hive-observer-bootstrap
just hive-observer-status
```

Keep that value in a root-readable environment file consumed by the Caddy
systemd unit, not directly in the Caddyfile or shell history. The
source-controlled [`install-caddy.sh`](install-caddy.sh) installs the route,
environment file, and systemd drop-in without embedding the token in git.

The k3s ServiceLB listens on `10.0.1.200:3003`, limits source addresses to the
Caddy VM at `10.0.1.234`, and still requires the proxy token. Confirm that this
source restriction is effective in the installed k3s version before treating
it as a security boundary.

## Caddy

Caddy configuration is currently managed directly on `10.0.1.234`. The
source-controlled route is [`Caddyfile`](Caddyfile):

```caddyfile
hive.frostyard.org {
	@preflight {
		method OPTIONS
		path /v1/hive/overview
		header Origin https://frostyard.github.io
	}
	handle @preflight {
		header Access-Control-Allow-Origin "https://frostyard.github.io"
		header Access-Control-Allow-Methods "GET, HEAD, OPTIONS"
		header Access-Control-Allow-Headers "Accept"
		header Vary "Origin"
		respond "" 204
	}

	@overview {
		method GET HEAD
		path /v1/hive/overview
	}
	handle @overview {
		reverse_proxy http://10.0.1.200:3003 {
			header_up X-Hive-Observer-Token {$HIVE_OBSERVER_TOKEN}
			header_down Access-Control-Allow-Origin "https://frostyard.github.io"
			header_down +Vary "Origin"
		}
	}

	respond 404
}
```

Stage `Caddyfile`, `caddy-hive-observer.conf`, `install-caddy.sh`, and a
mode-`0600` `hive-observer.env` in one directory on the VM, then run:

```bash
sudo ./install-caddy.sh
```

The installer validates the combined configuration before replacing the live
Caddyfile, keeps a timestamped backup, restarts Caddy so the environment file
is loaded, and removes the staged secret. The observer applies a global
120-request-per-minute limit because rate limiting is not part of a stock
Caddy build.

Set the public API origin for the Pages build:

```bash
gh variable set PUBLIC_HIVE_API_URL \
  --repo frostyard/lab \
  --body "https://hive.frostyard.org"
```

The variable is public configuration, not a credential.

## Validation

Verify the raw LAN listener rejects requests without Caddy's token:

```bash
curl -i http://10.0.1.200:3003/v1/hive/overview
```

It must return `401`. Then verify the public route:

```bash
curl -i https://hive.frostyard.org/v1/hive/overview
curl -i -X POST https://hive.frostyard.org/v1/hive/overview
curl -i https://hive.frostyard.org/api/status
```

The overview must return the versioned sanitized contract; the latter two
requests must not reach Hive.

Rollback starts by removing the public Caddy route, then the site link, and
finally the `frostyard-hive-observer` Argo CD Application. The observer does
not write Hive or PVC state.
