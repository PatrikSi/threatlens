# Backend Image Compliance Bundle

This directory mirrors the repository-level release and license artifacts that
must be present inside backend container images.

The backend Docker build context is `backend/`, so files outside that directory
cannot be copied into the image during `docker build -f backend/Dockerfile backend`.
Keep this bundle in sync with:

- repository `LICENSE`
- repository `THIRD_PARTY_NOTICES.md`
- `docs/licenses/*.txt`
- `python scripts/sync_compliance_bundle.py`

The backend image copies these files into `/usr/share/doc/threatlens/`.
