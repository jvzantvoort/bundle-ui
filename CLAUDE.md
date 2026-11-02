
I'm running a bundle-server
(https://github.com/jvzantvoort/bundle-server) on the following
location:

API Endpoint: 'http://storage.int.vanzantvoort.org:8080/api/v1/pools/default'
Token: BUNDLE_SERVER_TOKEN in .env (not committed)

The API documentation can also be found in "openapi.yaml".

I need a Django web server which can be run from containers that
allows me to:
- select pools
- select servers
- browse the bundles
- update the meta data of a bundles
- verify a bundle
- update the name of a bundle
- update, read, delete tags on a bundle
- query bundles

Allow for a postgresql database back-end to cache and manage tags
etc..

Allow for a page that displays the remote server health and status.

Ensure the webserver is keyboard friendly.

Make sure the containers are podman based using podman-compose to
start the server.
