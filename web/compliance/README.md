# Web Image Compliance Bundle

This directory mirrors the repository-level release and license artifacts that
must be present inside web container images.

The web Docker build context is `web/`, so files outside that directory cannot
be copied into the image during `docker build -f web/Dockerfile web`.
Keep this bundle in sync with:

- repository `LICENSE`
- repository `THIRD_PARTY_NOTICES.md`
- `docs/licenses/*.txt`
- `python scripts/sync_compliance_bundle.py`

The web image copies these files into `/usr/share/doc/threatlens/` and also
generates runtime-specific compliance artifacts there during the Docker build,
including:

- `frontend-runtime-package-legal/` for package-published frontend legal files
- `frontend-os-packages.txt` for redistributed Alpine packages
- `frontend-os-package-metadata.tsv` for the shipped Alpine package metadata set
