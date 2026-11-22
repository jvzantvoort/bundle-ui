# Bundle UI

A keyboard-friendly web interface for [bundle-server](https://github.com/jvzantvoort/bundle-server),
built with Django and PostgreSQL and run with podman-compose.

## Start

```sh
cp .env.example .env     # then fill in the secrets and BUNDLE_SERVER_TOKEN
podman-compose up -d --build
```

The UI listens on `http://<host>:6060`. If firewalld is running, open the port once:

```sh
sudo firewall-cmd --permanent --add-port=6060/tcp && sudo firewall-cmd --reload
```

To survive a reboot under rootless podman, enable the restart service and lingering:

```sh
systemctl --user enable --now podman-restart.service
loginctl enable-linger "$USER"
```

To deploy a code change, recreate the containers. `up --build` alone keeps the old ones running.
The database is kept:

```sh
podman-compose down && podman-compose up -d --build
```

## Container image

GitHub Actions builds the image with podman on every push and pull request, after the tests pass
against PostgreSQL, then starts it and tests it. Pushes publish it to
`ghcr.io/jvzantvoort/bundle-ui`; pull requests don't.

| Pushed to | Image tags |
| --- | --- |
| `main` | `latest`, `sha-<commit>` |
| Tag `v1.2.3` | `1.2.3`, `sha-<commit>` |

To run the published image instead of building locally, set this in `.env`:

```sh
BUNDLE_UI_IMAGE=ghcr.io/jvzantvoort/bundle-ui:latest
```

Then pull it and recreate the containers, without `--build`. The image's visibility is set
separately from the repository's. While the image is private, log in first with a token that has
the `read:packages` scope:

```sh
gh auth refresh --scopes read:packages
gh auth token | podman login ghcr.io --username jvzantvoort --password-stdin
podman-compose pull web
podman-compose down && podman-compose up -d
```

## Kubernetes with Helm

The chart in `charts/bundle-ui` runs the same three parts on Kubernetes: the web UI, the sync
worker and PostgreSQL. It pulls the image CI publishes to `ghcr.io/jvzantvoort/bundle-ui`, so a
change reaches the cluster by being pushed to `main`. podman-compose keeps working as before.

| Value | Default |
| --- | --- |
| `ingress.host` | `bundle-ui.apps.vanzantvoort.org`, through Traefik with TLS |
| `ingress.tls.secretName` | empty: Traefik's default self-signed certificate, like the other `*.apps` hosts |
| `config.bundleServer.url` | `http://storage.int.vanzantvoort.org:8080/api/v1` |
| `config.requireLogin` | `true` |
| `postgresql.persistence.pv.hostPath` | `/var/lib/rancher/storage/bundle-ui/postgres` on node `k3s` |

### Storage

PostgreSQL keeps its data in a local-filesystem PersistentVolume that the chart creates on one
node, like `registry-pv`: `hostPath` with `DirectoryOrCreate`, pinned to the node with
`nodeAffinity`, and `Retain`. `helm uninstall` keeps the volume, its claim and the data. To use a
volume you made yourself, set `postgresql.persistence.existingClaim` and `pv.create: false`.

### Install

The secrets live in a Secret you create, so they never pass through helm values or git:

```sh
kubectl create namespace bundle-ui

# Three random values; the bundle-server token is read without echoing it
read -rs -p "bundle-server token: " TOKEN && echo
kubectl -n bundle-ui create secret generic bundle-ui-secrets \
  --from-literal=DJANGO_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 24)" \
  --from-literal=ADMIN_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=BUNDLE_SERVER_TOKEN="$TOKEN"
unset TOKEN

helm upgrade --install bundle-ui charts/bundle-ui -n bundle-ui
```

To change the token later, without it showing up in your shell history:

```sh
read -rs -p "bundle-server token: " TOKEN && echo
kubectl -n bundle-ui patch secret bundle-ui-secrets --type merge \
  -p "{\"stringData\":{\"BUNDLE_SERVER_TOKEN\":\"$TOKEN\"}}"
unset TOKEN
kubectl -n bundle-ui rollout restart deploy/bundle-ui-web deploy/bundle-ui-sync
```

The cluster pulls the image anonymously, so the ghcr.io package must be public. For a private
package, create a pull secret from a token with only the `read:packages` scope and pass
`--set 'imagePullSecrets[0].name=ghcr-pull'`:

```sh
kubectl -n bundle-ui create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io --docker-username=jvzantvoort --docker-password=<token>
```

For a trusted certificate, create a `kubernetes.io/tls` Secret in the namespace and pass
`--set ingress.tls.secretName=<name>`. Read the admin password with
`kubectl -n bundle-ui get secret bundle-ui-secrets -o jsonpath='{.data.ADMIN_PASSWORD}' | base64 -d`.

### Develop

After CI has published a change to `main`, restart the pods to pull the new `latest`:

```sh
kubectl -n bundle-ui rollout restart deploy/bundle-ui-web deploy/bundle-ui-sync
```

To run one exact build, pass `--set image.tag=sha-<commit>` to `helm upgrade`. A change to the
chart itself is applied with the same `helm upgrade --install` command; settings changes restart
the pods by themselves. CI runs `helm lint` and `helm template` on every push.

## Access

With `REQUIRE_LOGIN=false`, anyone who can reach the port can use the UI with no login: they can
change titles and tags on the bundle-server using the configured token, trigger verification, and
add or remove servers. The token itself is never shown, and the server defined in `.env` can't be
edited in the UI. Set `REQUIRE_LOGIN=true` to require the `ADMIN_USERNAME` login, which is created
on first start.

## Features

| Area | What you can do |
| --- | --- |
| Servers | Select, add, edit and remove servers. The one in `.env` is configured at startup. |
| Pools | Select a pool on the selected server. |
| Bundles | Browse, filter, sort, and list files. |
| Images | Click an image in a bundle's file list to view it, then step through all of the bundle's images. Or scroll through them as a grid. |
| Query | Search by metadata, size, date, checksum, or file path across the pool. |
| Metadata | Edit the title on the server, and notes and key/value attributes locally. |
| Tags | Read, add, rename and remove on a bundle, or rename and delete across a pool. |
| Verify | Queue a check and follow it until it finishes, including per-file problems. |
| Health | See service health, token status, capabilities, and storage and integrity per pool. |

Author, creation time and format version are immutable on the server, so **the title is the only
server-side metadata you can change**. Notes and attributes stay in the local database.

Rename isn't an API operation, so it adds the new tag and then removes the old one. If the second
step fails, the bundle has both tags and nothing is lost. A pool-wide change reports any bundles
that failed.

The UI fetches images from the bundle-server itself, so the API token never reaches the browser.
JPEG (including MPO), PNG, GIF, WebP, AVIF and BMP files are shown; other files, SVG among them,
are not served. A bundle's contents never change, so browsers keep the images cached.

Bundles scanned with [bundle-python](https://github.com/jvzantvoort/bundle-python) have media
metadata, which bundle-server 1.2.0 and later serve read-only. The bundle page then lists the
devices (cameras, phones) that made the files and shows each file's type, resolution, capture time
and device; the image viewer adds the GPS position, linked to OpenStreetMap, and all EXIF tags.
Without media metadata these parts are left out.

## Keyboard

Press `?` on any page for the shortcuts available there.

| Keys | Action | Keys | Action |
| --- | --- | --- | --- |
| `g s` | Servers | `j` / `k` | Next / previous row |
| `g p` | Pools | `Enter` | Open focused row |
| `g b` | Bundles | `n` / `p` | Next / previous page |
| `g q` | Query | `/` | Focus search |
| `g t` | Tags | `Esc` | Leave a field or close a dialog |
| `g h` | Health | `s` | Sync the cache |
| `e` | Edit title | `v` | Verify bundle |
| `t` | Add tag | `m` | Edit local notes |
| `b` | Back | `r` | Run query / check health again |
| `i` | Images of a bundle | `n` / `p` or `→` / `←` | Next / previous image in the viewer |
| `a` | All images (viewer) | `f` | Image at full size in a new tab |
| `x` | EXIF data of an image | `l` | Location of an image on a map |

Everything also works with `Tab` and `Enter`, including without JavaScript.

## Cache

Bundle pages read from the server and update PostgreSQL as they go. If the server is unavailable,
pools and bundles are served from the cache read-only, with a warning.

List responses from the server don't include tags, so the `sync` container fetches every bundle
at startup and then every `BUNDLE_SYNC_INTERVAL` seconds (900 by default). `s` queues a sync
straight away. A bundle removed from the server is flagged rather than deleted, so its local notes
are kept.

## Containers

| Service | Purpose |
| --- | --- |
| `db` | PostgreSQL 17, with data in the `pgdata` volume |
| `web` | Applies migrations, configures the server from `.env`, runs gunicorn |
| `sync` | Background cache sync |

Without `POSTGRES_HOST`, Django uses SQLite, which is handy for local development.

## API specification

`openapi.yaml` is a copy of the canonical file in
[bundle-server](https://github.com/jvzantvoort/bundle-server/blob/main/openapi.yaml).
Change the API there, then run `scripts/sync-openapi.sh` here to update the copy.
The OpenAPI workflow fails when the copy differs, on every push and pull request
and once a week.

## Tests

The tests run against an in-memory fake of the API, so they never touch a real server:

```sh
pip install -r requirements.txt
python manage.py test catalog
```

For manual testing, `python -m catalog.tests.fake_server 8099` serves the fake at
`http://127.0.0.1:8099/api/v1` with the token `test-token`.
