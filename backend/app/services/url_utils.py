import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}

BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.",
}

BLOCKED_HOSTNAME_SUFFIXES = {
    ".local",
    ".localdomain",
    ".internal",
}


def _is_tracking_param(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith("utm_") or lowered in TRACKING_PARAMS


def _normalize_hostname(hostname: str) -> str:
    return hostname.strip().lower().rstrip(".")


def _build_netloc(*, scheme: str, hostname: str, port: int | None, username: str | None = None, password: str | None = None) -> str:
    credentials = ""
    if username:
        credentials = username
        if password is not None:
            credentials = f"{credentials}:{password}"
        credentials = f"{credentials}@"

    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{credentials}{hostname}"
    if port:
        return f"{credentials}{hostname}:{port}"
    return f"{credentials}{hostname}"


def _is_ip_allowed(ip: ipaddress._BaseAddress, allow_private_network: bool) -> bool:
    if allow_private_network:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def normalize_url(url: str | None) -> str:
    if not url:
        return ""

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""

    scheme = (parts.scheme or "http").lower()
    hostname = (parts.hostname or "").lower()
    if not hostname:
        return ""

    try:
        port = parts.port
    except ValueError:
        return ""

    netloc = _build_netloc(scheme=scheme, hostname=hostname, port=port)

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
        if not path:
            path = "/"

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(k)
    ]
    query_pairs.sort(key=lambda kv: (kv[0], kv[1]))
    query = urlencode(query_pairs, doseq=True)

    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_feed_url(url: str | None) -> str:
    if not url:
        return ""

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""

    scheme = (parts.scheme or "http").lower()
    hostname = (parts.hostname or "").lower()
    if not hostname:
        return ""

    try:
        port = parts.port
    except ValueError:
        return ""

    netloc = _build_netloc(
        scheme=scheme,
        hostname=hostname,
        port=port,
        username=parts.username,
        password=parts.password,
    )

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
        if not path:
            path = "/"

    return urlunsplit((scheme, netloc, path, parts.query, ""))


def is_fetchable_url(url: str | None, allow_private_network: bool = False) -> bool:
    if not url:
        return False

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False

    if parts.scheme.lower() not in {"http", "https"}:
        return False

    hostname = _normalize_hostname(parts.hostname or "")
    if not hostname:
        return False

    if hostname in BLOCKED_HOSTNAMES:
        return allow_private_network

    if any(hostname.endswith(suffix) for suffix in BLOCKED_HOSTNAME_SUFFIXES):
        return allow_private_network

    if not allow_private_network and "." not in hostname:
        return False

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return True

    return _is_ip_allowed(ip, allow_private_network)


def resolve_hostname_ips(hostname: str) -> set[ipaddress._BaseAddress]:
    normalized = _normalize_hostname(hostname)
    if not normalized:
        return set()

    try:
        infos = socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return set()
    except OSError:
        return set()

    resolved: set[ipaddress._BaseAddress] = set()
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        if not sockaddr:
            continue
        ip_raw = sockaddr[0]
        try:
            resolved.add(ipaddress.ip_address(ip_raw))
        except ValueError:
            continue

    return resolved


def resolve_runtime_allowed_ips(hostname: str, allow_private_network: bool = False) -> list[str]:
    normalized = _normalize_hostname(hostname)
    if not normalized:
        return []

    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        resolved = resolve_hostname_ips(normalized)
        allowed = [entry for entry in resolved if _is_ip_allowed(entry, allow_private_network)]
        return [str(entry) for entry in sorted(allowed, key=lambda entry: (entry.version, str(entry)))]

    if not _is_ip_allowed(ip, allow_private_network):
        return []
    return [str(ip)]


def is_runtime_fetchable_url(url: str | None, allow_private_network: bool = False) -> bool:
    if not is_fetchable_url(url, allow_private_network=allow_private_network):
        return False

    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return False

    hostname = _normalize_hostname(parts.hostname or "")
    if not hostname:
        return False

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return bool(resolve_runtime_allowed_ips(hostname, allow_private_network=allow_private_network))

    return _is_ip_allowed(ip, allow_private_network)


def ensure_runtime_fetchable_url(url: str, allow_private_network: bool = False) -> None:
    if not is_runtime_fetchable_url(url, allow_private_network=allow_private_network):
        raise ValueError("URL is not allowed for outbound fetch")
