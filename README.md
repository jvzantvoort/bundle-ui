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

## Tests

The tests run against an in-memory fake of the API, so they never touch a real server:

```sh
pip install -r requirements.txt
python manage.py test catalog
```

For manual testing, `python -m catalog.tests.fake_server 8099` serves the fake at
`http://127.0.0.1:8099/api/v1` with the token `test-token`.
