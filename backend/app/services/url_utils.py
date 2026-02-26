import ipaddress
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
}


def _is_tracking_param(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith("utm_") or lowered in TRACKING_PARAMS


def normalize_url(url: str | None) -> str:
    if not url:
        return ""

    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "http").lower()
    hostname = (parts.hostname or "").lower()

    port = parts.port
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


def is_fetchable_url(url: str | None, allow_private_network: bool = False) -> bool:
    if not url:
        return False

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False

    if parts.scheme.lower() not in {"http", "https"}:
        return False

    hostname = (parts.hostname or "").strip().lower()
    if not hostname:
        return False

    if hostname in BLOCKED_HOSTNAMES:
        return allow_private_network

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return True

    if allow_private_network:
        return True

    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return False

    return True
