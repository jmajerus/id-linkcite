"""Who may use the editorial admin listener, matching the authoring suite's servers.

A direct request from a private peer that names this host (no Cloudflare headers) is
trusted: the LAN is the owner's. Anything that came through Cloudflare, or names
another host, needs a Cloudflare Access assertion that verifies against the team's
keys. A missing or invalid assertion fails closed. Cloudflare Access and its Managed
OAuth decide who signs in; this check makes sure a request really passed through them.
"""
import ipaddress
from threading import Lock
from urllib.parse import urlsplit

import jwt

PROXY_HEADERS = ("cf-ray", "cf-connecting-ip", "cdn-loop", "cf-visitor", "cf-access-jwt-assertion")
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "127.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10"))


class AccessError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def private_peer(address):
    try:
        ip = ipaddress.ip_address((address or "").strip("[]").lower())
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in PRIVATE_NETWORKS)


def request_host(raw):
    """The host name a request names, or AccessError for a malformed Host header."""
    if not raw or any(c in raw for c in " \t\r\n/@?#\\"):
        raise AccessError(400, "Invalid Host header.")
    try:
        url = urlsplit("http://" + raw)
        url.port  # raises ValueError for a bad port
        host = url.hostname
    except ValueError:
        host = None
    if not host:
        raise AccessError(400, "Invalid Host header.")
    return host.lower()


def proxied(headers):
    return any(h in headers for h in PROXY_HEADERS)


class Access:
    def __init__(self, team_domain, lan_hosts, keys=None):
        self.lan_hosts = {h.strip().lower() for h in lan_hosts if h.strip()}
        self.issuer = None
        if team_domain:
            # A malformed value leaves the issuer unset: LAN access still works, and
            # requests through Cloudflare are refused (503) until it is corrected.
            try:
                url = urlsplit(team_domain.strip())
                valid = (url.scheme == "https" and url.hostname and not url.username and not url.password
                         and url.path in ("", "/") and not url.query and not url.fragment)
                url.port  # raises ValueError for a bad port
            except ValueError:
                valid = False
            if valid:
                self.issuer = f"https://{url.netloc}"
        self._keys = keys  # a callable token -> key, for tests
        self._client = None
        self._lock = Lock()

    def signing_key(self, token):
        if self._keys:
            return self._keys(token)
        with self._lock:
            if self._client is None:
                self._client = jwt.PyJWKClient(self.issuer + "/cdn-cgi/access/certs",
                                               cache_keys=True, lifespan=300, timeout=5)
        return self._client.get_signing_key_from_jwt(token).key

    def authorize(self, headers, peer):
        """Return {'via': 'lan'} or {'via': 'access', 'subject', 'email'?}; raise AccessError."""
        host = request_host(headers.get("Host"))
        local_host = host == "localhost" or private_peer(host) or host in self.lan_hosts
        if not proxied(headers) and local_host and private_peer(peer):
            return {"via": "lan"}
        tokens = headers.get_all("Cf-Access-Jwt-Assertion") or []
        if not tokens:
            raise AccessError(401, "Cloudflare Access authentication is required.")
        # The audience is not checked: every Access application in the team admits only
        # the owner, and a pinned audience broke sign-in whenever one was recreated.
        if not self.issuer:
            raise AccessError(503, "Cloudflare Access is not configured.")
        try:
            if len(tokens) != 1 or not tokens[0]:
                raise ValueError("one assertion required")
            token = tokens[0]
            claims = jwt.decode(token, self.signing_key(token), algorithms=["RS256"],
                                issuer=self.issuer,
                                options={"require": ["sub", "exp", "iat"], "verify_aud": False})
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject.strip():
                raise ValueError("no subject")
        except Exception:
            raise AccessError(403, "Cloudflare Access token is invalid for this route.") from None
        identity = {"via": "access", "subject": subject}
        if isinstance(claims.get("email"), str):
            identity["email"] = claims["email"]
        return identity
