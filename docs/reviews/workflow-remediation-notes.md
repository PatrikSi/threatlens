# Workflow remediation

## Article previews

Original-article previews block remote images, stylesheets, fonts, and media by
default. The source document is still retrieved by the backend. Analysts can
enable **Load external resources for this preview** when needed; this allows
publishers and third parties to observe the browser's IP address and viewing
activity. The choice resets for each article and can be turned off again.
Scripts, forms, embedded frames, and direct script connections remain blocked.

The preview API accepts `external_resources=true` to opt in to this behavior.
Existing item permissions and handling-label access checks apply to both modes.
