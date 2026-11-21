# LinkCite editorial administration

RDF remains authoritative for definitions, classifications, relationships and
identifiers. PostgreSQL stores supplementary Markdown keyed by exact public IRI,
including individual hash resources. Editing does not commit to Git, rebuild the
semantic graph or deploy the site.

## Editing

Open the editor at https://id-editorial.linkcite.org/admin (Cloudflare Access), or
at http://id-linkcite.localdomain:8000/admin on the LAN. Search the
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

### From a reader page

Each term on a reader page carries an **Edit** link, hidden from visitors, that
opens the editor on that exact IRI (`/admin?iri=…`). Opening the editor at
id-editorial.linkcite.org sets a `linkcite_editor` hint cookie on `.linkcite.org`
(for 400 days), and reader pages on id.linkcite.org show their Edit links when
it is present. The cookie is not a credential: the links lead to the editor,
which Access protects. Opening the editor on the LAN hostname cannot set it, so
visit id-editorial.linkcite.org once in each browser you edit from; delete the
cookie to hide the links again. In the editor, **View page** opens the term's
public page, and the address bar keeps the open term, so a reload or bookmark
returns to it.

An agent needs no search either: a term's IRI is its page's URL, so a page
address can be passed to `get_content` and `save_draft` as it is.

Markdown supports headings, tables, code, lists, blockquotes, links and images.
Public images can be linked by URL, including existing /assets/ paths; an image
upload manager and true rich-text WYSIWYG mode are not part of this first version.
Do not put manuscript material into the public draft unless it is intended to
be published. This service is separate from the RDF publication filter.
The database retains content for retired IRIs, but the public reader only
inserts content into current resource shells, and the editor lists current IRIs.
Reassign or archive retired resources administratively after reviewing identity.

## Editing with an agent (MCP)

The same service answers MCP at `/mcp`, like the authoring suite's other servers:
`http://id-linkcite.localdomain:8000/mcp` on the LAN, and
`https://id-editorial.linkcite.org/mcp` through Cloudflare, where Access and its
Managed OAuth sign the client in. The tools:

| Tool | Does |
|---|---|
| `search_resources` | Finds current IRIs by label or IRI words; filters to published, unpublished, or pending (draft differs from published) |
| `get_content` | The term's canonical RDF text (shown above the Markdown on its page), its draft, published Markdown and revision number; private notes only if asked |
| `list_revisions`, `get_revision` | History, as in the editor |
| `preview_markdown` | Renders through the reader's sanitizer; saves nothing |
| `save_draft` | Saves the public draft, keeping private notes unless given; needs the revision number it read |

An agent can only save drafts. Publishing, unpublishing and restoring stay in
`/admin`, so a saved draft is a proposal until you publish it. The history records
agent saves as `mcp (<who>)`: the Access email through Cloudflare, or
`LINKCITE_EDITORIAL_USER` on the LAN. A save against a stale revision fails and
changes nothing. The service is stateless (one JSON answer per POST, no sessions).

The canonical text comes from `build/editorial-canonical.json`, written by the
build beside `editorial-resources.json` from the same public data.

## One-time database provisioning

The database is `id-linkcite` on postgresql.localdomain. Connect an
administrative psql session to the server's maintenance database (for example
`psql "postgresql://ADMIN@postgresql.localdomain/postgres?sslmode=require"`),
then create a non-login owner, the two login roles (names are fixed by the
migration) and the database:

```sql
CREATE ROLE linkcite_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE linkcite_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE linkcite_editor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE DATABASE "id-linkcite" OWNER linkcite_owner
    TEMPLATE template0 ENCODING 'UTF8' LOCALE_PROVIDER builtin BUILTIN_LOCALE 'C.UTF-8';
REVOKE ALL ON DATABASE "id-linkcite" FROM PUBLIC;
GRANT CONNECT ON DATABASE "id-linkcite" TO linkcite_reader, linkcite_editor;
```

If the database was already created by an administrator, replace the
`CREATE DATABASE` statement with `ALTER DATABASE "id-linkcite" OWNER TO linkcite_owner;`.
The `builtin` locale provider requires PostgreSQL 17 or later; on older servers
use `LOCALE 'C.UTF-8'` instead. The schema does not depend on collation.

Use psql's interactive `\password linkcite_reader` and
`\password linkcite_editor` to set different strong passwords rather than
committing or sharing credentials; `\password` hashes them client-side, so the
plaintext never reaches server logs. Then connect to `id-linkcite` itself and
apply the migration as the owner role:

```sh
psql "postgresql://ADMIN@postgresql.localdomain/id-linkcite?sslmode=require" \
  -X -v ON_ERROR_STOP=1 -c 'SET ROLE linkcite_owner' -f sql/001-editorial.sql
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

The public reader service binds to loopback port 7880; only Trifid calls it,
and it must never be added to the Cloudflare tunnel. Trifid has no PostgreSQL
credentials. The reader service uses the restricted reader role. The admin
service (editor, API and MCP) listens on port 8000 on every interface, as the
authoring suite's servers do, runs as a different Unix user, and has separate
database credentials. Direct requests from a private address naming this host
(`id-linkcite.localdomain`, `id-linkcite`, `localhost` or an IP; override with
`LINKCITE_LAN_HOSTS`) are trusted. Any request that came through Cloudflare, or
names another host, needs a Cloudflare Access assertion that verifies against
`ACCESS_TEAM_DOMAIN`'s keys; a missing or invalid one is refused.

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
ACCESS_TEAM_DOMAIN="https://REPLACE.cloudflareaccess.com"
```

`LINKCITE_EDITORIAL_USER` is the name LAN edits are recorded under. Releases
before the MCP service also required `LINKCITE_EDITORIAL_PASSWORD`; it is now
ignored and can be removed once you no longer need to roll back to one.

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

Deployments keep the editorial venv current: `install.sh` runs `pip install -r
requirements.txt` in `/opt/id-linkcite/editorial-venv` when it exists, and
reinstalls each enabled editorial unit from the release it switches to.

### Cloudflare

Configured in Cloudflare only, as for the suite's other servers:

- **Tunnel route:** on the same tunnel as `id.linkcite.org` (which stays →
  `http://id-linkcite.localdomain:7878`, public), add the public hostname
  `id-editorial.linkcite.org` → `http://id-linkcite.localdomain:8000`. Keeping the
  editor on its own hostname leaves `/mcp`, `/admin` and `/api` out of the
  identifier namespace, and lets Access cover the whole hostname.
- **Access application** for `id-editorial.linkcite.org`, admitting only the owner,
  with Managed OAuth for MCP clients.
- **MCP server definition:** `https://id-editorial.linkcite.org/mcp`.

The origin checks the assertion's signature, issuer, expiry and subject, not its
audience, so recreating the Access application needs no change here. The admin
pages also check that browser writes are same-origin JSON, and `/mcp` refuses
non-JSON or cross-origin POSTs, so a web page cannot write drafts through a
LAN browser.

### Authoring suite

Register the service in authoring-ops-mcp's `suites/authoring.json`, with
`health` and `mcp` at `http://id-linkcite.localdomain:8000`. `/health` follows
the suite's health contract: the version from `package.json`, what was deployed
(`deployed.json`, written by `tools/deploy.py`) and PostgreSQL as a dependency.

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
