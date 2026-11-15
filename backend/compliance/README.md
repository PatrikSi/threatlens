# Backend Image Compliance Bundle

This directory mirrors the repository-level release and license artifacts that
must be present inside backend container images.

The backend Docker build context is `backend/`, so files outside that directory
cannot be copied into the image during `docker build -f docker/backend.Dockerfile backend`.
Keep this bundle in sync with:

- repository `LICENSE`
- `docs/licenses/*.txt`

The backend image copies these files into `/usr/share/doc/threatlens/` and also
generates runtime-specific compliance artifacts there during the Docker build,
including:

- `backend-runtime-package-legal/` for wheel-published Python legal files
- `backend-os-packages.txt` for redistributed Debian packages
- `backend-os-package-legal/` for copied Debian package copyright files

The `ANTLR-4.13.2-BSD-3-Clause.txt` supplement preserves the ANTLR Project
copyright and conditions omitted from the `antlr4-python3-runtime==4.13.2`
wheel. It is copied verbatim from the official release tag's
[`LICENSE.txt`](https://github.com/antlr/antlr4/blob/4.13.2/LICENSE.txt),
SHA-256 `3db1fb3ee79a4b4f9918fc4d0f6133bf18a3cf787f126cd22f8aa9b862281c0c`.
The wheel's source headers refer to that root license. The generated wheel
metadata still records its missing published legal file; the supplemental
notice is shipped separately under `/usr/share/doc/threatlens/licenses/`.
