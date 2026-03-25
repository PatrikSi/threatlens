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

    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        netloc = hostname
    elif port:
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

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


def is_fetchable_url(url: str | None, allow_private_network: bool = True) -> bool:
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


def is_runtime_fetchable_url(url: str | None, allow_private_network: bool = True) -> bool:
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
        resolved = resolve_hostname_ips(hostname)
        if not resolved:
            return False
        return all(_is_ip_allowed(entry, allow_private_network) for entry in resolved)

    return _is_ip_allowed(ip, allow_private_network)


def ensure_runtime_fetchable_url(url: str, allow_private_network: bool = True) -> None:
    if not is_runtime_fetchable_url(url, allow_private_network=allow_private_network):
        raise ValueError("URL is not allowed for outbound fetch")
