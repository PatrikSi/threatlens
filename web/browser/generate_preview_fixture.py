"""Regenerate the offline browser preview fixture from the real sanitizer and CSP."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.article_preview import (  # noqa: E402
    article_preview_response_headers,
    sanitize_article_preview_html,
)

source = """<!doctype html><html><head>
<link rel="stylesheet" href="https://tracking.example.test/style.css">
<link rel="preconnect" href="https://tracking.example.test">
</head><body><p>Offline publisher fixture</p>
<img src="https://tracking.example.test/pixel.png">
<script>fetch('https://tracking.example.test/script')</script>
</body></html>"""
fixture = {
    "html": sanitize_article_preview_html(source, final_url="https://publisher.example.test/story"),
    "blockedHeaders": article_preview_response_headers(),
    "allowedHeaders": article_preview_response_headers(external_resources=True),
}
Path(__file__).with_name("preview-response.json").write_text(json.dumps(fixture, indent=2) + "\n")
