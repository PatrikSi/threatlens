# Backend Image Compliance Bundle

This directory mirrors the repository-level release and license artifacts that
must be present inside backend container images.

The backend Docker build context is `backend/`, so files outside that directory
cannot be copied into the image during `docker build -f backend/Dockerfile backend`.
Keep this bundle in sync with:

- repository `LICENSE`
- `docs/licenses/*.txt`

The backend image copies these files into `/usr/share/doc/threatlens/` and also
generates runtime-specific compliance artifacts there during the Docker build,
including:

- `backend-runtime-package-legal/` for wheel-published Python legal files
- `backend-os-packages.txt` for redistributed Debian packages
- `backend-os-package-legal/` for copied Debian package copyright files
