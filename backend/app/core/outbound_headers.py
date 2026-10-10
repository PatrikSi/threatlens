"""Transport-owned headers cannot be configured as outbound credentials."""

BLOCKED_REQUEST_HEADERS = frozenset({
    "connection", "content-length", "expect", "host", "proxy-authenticate",
    "proxy-authorization", "proxy-connection", "te", "trailer",
    "transfer-encoding", "upgrade",
})
