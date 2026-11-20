#!/bin/sh
# Runs as root on the LXC, from tools/deploy.py, with RELEASE_ID set and the release
# unpacked in /opt/id-linkcite/releases/$RELEASE_ID. Loads a new store, switches both
# services to it, and checks it answers; on failure the previous release comes back.
set -eu
umask 0022
: "${RELEASE_ID:?}"
RELEASE=/opt/id-linkcite/releases/$RELEASE_ID
STORE=/var/lib/id-linkcite/store-$RELEASE_ID
test -x /usr/local/bin/oxigraph || { echo 'Oxigraph is missing from /usr/local/bin/oxigraph.' >&2; exit 1; }
# Trifid's dependencies need Node.js 22.12+, newer than Debian's, so the official
# Node.js 22 runtime is installed under /opt/id-linkcite/runtime, checksum-verified.
NODE=/opt/id-linkcite/runtime/bin/node
if ! test -x "$NODE" || ! "$NODE" -e 'const [a, b] = process.versions.node.split(".").map(Number); process.exit(a > 22 || (a === 22 && b >= 12) ? 0 : 1)'; then
  dist=https://nodejs.org/dist/latest-v22.x
  tmp=$(mktemp -d)
  curl -fsS "$dist/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
  name=$(grep -oE 'node-v22\.[0-9]+\.[0-9]+-linux-x64\.tar\.xz' "$tmp/SHASUMS256.txt" | head -1)
  curl -fsS "$dist/$name" -o "$tmp/$name"
  (cd "$tmp" && grep " $name\$" SHASUMS256.txt | sha256sum -c --quiet)
  install -d -m 0755 /opt/id-linkcite/runtimes
  tar -xJf "$tmp/$name" -C /opt/id-linkcite/runtimes
  ln -sfn "/opt/id-linkcite/runtimes/${name%.tar.xz}" /opt/id-linkcite/runtime
  rm -rf "$tmp"
  echo "Installed $("$NODE" --version) in /opt/id-linkcite/runtime"
fi
export PATH=/opt/id-linkcite/runtime/bin:$PATH
exec 9>/var/lock/id-linkcite-deploy.lock
flock -n 9 || { echo 'Another deployment is already running.' >&2; exit 1; }

getent group id-linkcite >/dev/null || groupadd --system id-linkcite
id id-linkcite >/dev/null 2>&1 || useradd --system --gid id-linkcite --home-dir /var/lib/id-linkcite --shell /usr/sbin/nologin id-linkcite
install -d -m 0755 -o id-linkcite -g id-linkcite /var/lib/id-linkcite

cd "$RELEASE"
npm ci --omit=dev --no-audit --no-fund
chown -R root:root "$RELEASE"
chmod -R a+rX,go-w "$RELEASE"
rm -rf "$STORE"
runuser -u id-linkcite -- /usr/local/bin/oxigraph load --location "$STORE" --file "$RELEASE/build/public.nq"
runuser -u id-linkcite -- /usr/local/bin/oxigraph optimize --location "$STORE"

# The cloned server's writable Oxigraph held 7878 on every interface; this release takes that port.
if systemctl is-enabled --quiet oxigraph 2>/dev/null || systemctl is-active --quiet oxigraph 2>/dev/null; then
  systemctl disable --now oxigraph
  echo 'Stopped and disabled the old oxigraph service; its data in /var/lib/oxigraph is untouched.'
fi

# Editorial units once read editorial-public.env and editorial-admin.env, each with
# LINKCITE_EDITORIAL_DSN. Build the single editorial.env from them once; keep the
# old files so a rollback to that release still starts.
ENV_DIR=/etc/linkcite
if test ! -e "$ENV_DIR/editorial.env" && { test -f "$ENV_DIR/editorial-public.env" || test -f "$ENV_DIR/editorial-admin.env"; }; then
  (
    umask 0077
    {
      echo '# Generated from editorial-public.env and editorial-admin.env.'
      test ! -f "$ENV_DIR/editorial-public.env" ||
        sed -n 's/^[[:space:]]*LINKCITE_EDITORIAL_DSN=/LINKCITE_EDITORIAL_READER_DSN=/p' "$ENV_DIR/editorial-public.env"
      test ! -f "$ENV_DIR/editorial-admin.env" ||
        sed -n -e 's/^[[:space:]]*LINKCITE_EDITORIAL_DSN=/LINKCITE_EDITORIAL_EDITOR_DSN=/p' \
               -e '/^[[:space:]]*LINKCITE_EDITORIAL_\(USER\|PASSWORD\)=/p' "$ENV_DIR/editorial-admin.env"
    } > "$ENV_DIR/editorial.env.tmp"
    chown root:root "$ENV_DIR/editorial.env.tmp"
    mv "$ENV_DIR/editorial.env.tmp" "$ENV_DIR/editorial.env"
  )
  echo "Created $ENV_DIR/editorial.env from the per-mode editorial environment files (kept for rollback)."
fi

previous=$(readlink /opt/id-linkcite/current || true)
previous_store=$(readlink /var/lib/id-linkcite/store || true)
switch() {  # release store
  ln -sfn "$1" /opt/id-linkcite/current.next && mv -Tf /opt/id-linkcite/current.next /opt/id-linkcite/current
  ln -sfn "$2" /var/lib/id-linkcite/store.next && mv -Tf /var/lib/id-linkcite/store.next /var/lib/id-linkcite/store
  systemctl restart id-linkcite-oxigraph id-linkcite
  # Optional editorial services are provisioned separately; the database is not part of rollback.
  for mode in public admin; do
    unit="id-linkcite-editorial-$mode.service"
    if systemctl is-enabled --quiet "$unit"; then
      if test -f "$1/editorial_service/server.py"; then
        # Units change between releases (e.g. their environment file); install the
        # switched-to release's copy so a rollback also restores the matching unit.
        if test -f "$1/deploy/$unit"; then
          install -m 0644 "$1/deploy/$unit" /etc/systemd/system/
          systemctl daemon-reload
        fi
        systemctl restart "$unit" || echo "Warning: $unit could not restart" >&2
      else
        systemctl stop "$unit" || true
      fi
    fi
  done
}
install -m 0644 "$RELEASE/deploy/id-linkcite-oxigraph.service" "$RELEASE/deploy/id-linkcite.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable id-linkcite-oxigraph id-linkcite
switch "$RELEASE" "$STORE"

home=$(sed -n 's/.*"home": "https:\/\/id\.linkcite\.org\(\/[^"]*\)".*/\1/p' "$RELEASE/build/manifest.json" | head -1)
healthy=0
for attempt in $(seq 1 30); do
  if curl -fsS -o /dev/null -H 'Host: id.linkcite.org' -H 'X-Forwarded-Proto: https' -H 'Accept: text/turtle' "http://127.0.0.1:7878$home"; then healthy=1; break; fi
  sleep 1
done
if test "$healthy" != 1; then
  journalctl -u id-linkcite -u id-linkcite-oxigraph -n 30 --no-pager || true
  if test -n "$previous" && test -n "$previous_store"; then
    switch "$previous" "$previous_store"
    echo "Release $RELEASE_ID did not answer for $home; restored $previous." >&2
  else
    systemctl stop id-linkcite id-linkcite-oxigraph
    echo "Release $RELEASE_ID did not answer for $home; services stopped." >&2
  fi
  exit 1
fi

# Keep the three newest releases and stores, for rollback.
ls -1dt /opt/id-linkcite/releases/* | tail -n +4 | xargs -r rm -rf
ls -1dt /var/lib/id-linkcite/store-* | tail -n +4 | xargs -r rm -rf
echo "Healthy release $RELEASE_ID: $(grep -c '' "$RELEASE/build/public.nq") quads, answering on :7878"
