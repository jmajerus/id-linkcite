# LinkCite editorial administration

RDF remains authoritative for definitions, classifications, relationships and
identifiers. PostgreSQL stores supplementary Markdown keyed by exact public IRI,
including individual hash resources. Editing does not commit to Git, rebuild the
semantic graph or deploy the site.

## Editing

Open the private editor through the SSH tunnel described below. Search the
current public resource registry by label or IRI. Select a resource, then use
the Public content draft or Private notes tab. Both have Markdown formatting
controls and a live server-rendered preview. The preview uses the same sanitizer
as the reader service and suppresses external images and active links for privacy.

- Save draft and notes: record changed text, privately.
- Publish public draft: save both working fields and atomically copy only the
  public draft to published Markdown. The reader renders and sanitizes the new
  public text on a cache miss. Private notes do not enter the published fields.
  Changes appear on the next successful reader request.
- Unpublish: remove published Markdown, retain editable draft, notes and history.
- Revision history: inspect previous snapshots, including published Markdown at
  that revision. Restore draft and notes makes a new revision; it does not change
  existing published content until you explicitly publish. No-op saves do not
  generate revisions. Revision notes are optional.

Markdown supports headings, tables, code, lists, blockquotes, links and images.
Public images can be linked by URL, including existing /assets/ paths; an image
upload manager and true rich-text WYSIWYG mode are not part of this first version.
Do not put manuscript material into the public draft unless it is intended to
be published. This service is separate from the RDF publication filter.
The database retains content for retired IRIs, but the public reader only
inserts content into current resource shells, and the editor lists current IRIs.
Reassign or archive retired resources administratively after reviewing identity.

## One-time database provisioning

The database is `id-linkcite` on postgresql.localdomain. In an administrative
psql session connected to it, create a non-login owner and the two login roles
(role names are fixed by the migration):

```sql
CREATE ROLE linkcite_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE linkcite_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE linkcite_editor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
ALTER DATABASE "id-linkcite" OWNER TO linkcite_owner;
REVOKE ALL ON DATABASE "id-linkcite" FROM PUBLIC;
GRANT CONNECT ON DATABASE "id-linkcite" TO linkcite_reader, linkcite_editor;
```

Use psql's interactive `\password linkcite_reader` and
`\password linkcite_editor` to set different strong passwords rather than
committing or sharing credentials; `\password` hashes them client-side, so the
plaintext never reaches server logs. Then apply the migration as the owner role:

```sh
psql "$ADMIN_URL" -X -v ON_ERROR_STOP=1 -c 'SET ROLE linkcite_owner' -f sql/001-editorial.sql
```

Never apply it directly as a superuser or as a superuser-owned database's
owner: `editorial.change_resource` is SECURITY DEFINER and runs with its
owner's privileges. Revoking ALL from PUBLIC also removes TEMP, so neither
application role can create temporary objects. Limit PostgreSQL network access to the LXC and authorized
administrators using firewall and pg_hba.conf. Use TLS with certificate
verification where available (sslmode=verify-full with a trusted CA).
The public role must not be a member of the editor or owner roles.

The schema includes current state, append-only application revision history,
and a restricted public view exposing only the IRI, its document IRI and
published Markdown. The history records snapshots of the draft, notes
and published Markdown. Neither application role has direct table-write
permissions. The editor can execute only the owner-controlled transition
function for writes; row locking, revision checks and history snapshots are
inside that function. It cannot move an IRI, inject stored HTML, or rewrite
a revision. The reader sanitizes all public Markdown before inserting HTML.
The function's actor label is supplied by the authenticated admin service;
it is not a tamper-proof identity audit against compromise of editor credentials.
This initial schema replaces the earlier unmerged draft. If the earlier draft
was already provisioned, do not drop or recreate its tables; arrange a reviewed
migration that preserves existing editorial data first.
Back up the database independently of website releases; include private notes
and history in the protected backup policy.

## Build and validate

From id-linkcite, with all required sibling vocabularies available:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests
python scripts/build.py
node --check trifid/plugins/pages.js
node --check editorial_service/admin.js
```

For database integration tests, create a disposable database with the migration
applied and set LINKCITE_TEST_EDITOR_DSN and LINKCITE_TEST_READER_DSN locally.
Do not use production DSNs. The tests check automatic snapshots, no-op saves,
optimistic concurrency, restoration, denial of direct writes by the editor,
and reader-role denial of private tables and the write function. Cache tests
also check sanitization and eviction.

Review the build's public.nq and generated pages; verify full manuscript and
private RDF editorial properties are not published. The old --definitions flag
is unnecessary. Deploy with the normal `python tools/deploy.py` command.

## One-time service provisioning on the id-linkcite LXC

The public reader service and the private admin service bind to loopback only,
ports 7880 and 7881 respectively. Trifid has no PostgreSQL credentials. It calls
the public service, which uses the restricted reader role; the editor runs as a
different Unix user with separate database credentials. Do not add either
service to the public Cloudflare tunnel.

After the release containing editorial_service has been deployed, run as root:

```sh
for mode in public admin; do
  id "id-linkcite-$mode" >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin "id-linkcite-$mode"
done
install -d -m 0700 /etc/linkcite
python3 -m venv /opt/id-linkcite/editorial-venv
/opt/id-linkcite/editorial-venv/bin/pip install -r /opt/id-linkcite/current/requirements.txt
```

Create one mode-0600 root-owned systemd environment file using a local editor
(not in Git). Example format, with placeholders only:

```text
# /etc/linkcite/editorial.env
LINKCITE_EDITORIAL_READER_DSN="postgresql://linkcite_reader:REPLACE@postgresql.localdomain:5432/id-linkcite?sslmode=verify-full"
LINKCITE_EDITORIAL_EDITOR_DSN="postgresql://linkcite_editor:REPLACE@postgresql.localdomain:5432/id-linkcite?sslmode=verify-full"
LINKCITE_EDITORIAL_USER="editor"
LINKCITE_EDITORIAL_PASSWORD="REPLACE_WITH_A_SEPARATE_STRONG_PASSWORD_AT_LEAST_20_CHARACTERS"
```

systemd reads the file as root before dropping privileges, so the service
users never read it directly. Each unit's `UnsetEnvironment=` removes the
other role's secrets before start: the public reader process receives only
the reader DSN, and the admin process never receives it. Each mode reads
only its own variable. Do not add the administrative superuser URL to this
file; the units also unset `AUTHORING_DATABASE_URL` in case it is copied in.
URL-encode any reserved characters in passwords.

Adjust the DSN TLS options to the actual server certificate configuration.
Do not put secrets on command lines or in application logs. Install and start:

```sh
chmod 0600 /etc/linkcite/editorial.env
for mode in public admin; do
  install -m 0644 "/opt/id-linkcite/current/deploy/id-linkcite-editorial-$mode.service" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now id-linkcite-editorial-public id-linkcite-editorial-admin
```

From the workstation, create a secure port forward using an authorized SSH
account:

```sh
ssh -N -L 7881:127.0.0.1:7881 root@id-linkcite.localdomain
```

Visit http://127.0.0.1:7881/ and authenticate with the separate editor login.
The SSH tunnel protects the HTTP connection; the admin service rejects
non-loopback Host headers, requires authentication, and checks same-origin
JSON requests. Keep the admin service private. A Cloudflare Access-protected
admin hostname, multiple named users and SSO would be separate enhancements.

## Performance and failure handling

Reader shells are generated at vocabulary build time. Trifid uses a bounded
32 MiB lazy HTML cache, requests the published HTML from the local public
service in parallel, and inserts it into the matching IRI markers. The public
service uses a persistent PostgreSQL pool and one indexed query per document,
including any hash fragments. The database stores Markdown, not trusted HTML.
The reader sanitizes and renders published Markdown on a cache miss and uses
an additional bounded 32 MiB LRU cache (up to 1024 entries) keyed by the exact
published text. Warm requests do not parse Markdown; new or evicted text has
a rendering cost. Draft and private text are never inputs to the reader cache.
Unpublishing or changing the database text takes effect on the next lookup.
The public request has a 100 ms fetch deadline and
falls back to canonical RDF reader HTML on error. RDF content negotiation
does not contact PostgreSQL.

Use Server-Timing headers (editorial and origin) and browser Navigation Timing
to measure p95 origin and click-to-render time. X-LinkCite-Editorial: unavailable
indicates a degraded request. Reader HTML uses Cache-Control: no-store to make
published corrections immediately visible; make sure Cloudflare does not override
this for reader routes. Static CSS and images can still be cached. The latency
targets are not measured benchmarks; validate on the actual tunnel and LAN.

Website deployments restart configured editorial services but do not reset,
migrate or roll back the database. If the database is down, reader navigation
and RDF responses must continue working.
