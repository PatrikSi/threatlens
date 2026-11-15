# Web Image Compliance Bundle

This directory mirrors the repository-level release and license artifacts that
must be present inside web container images.

The web Docker build context is `web/`, so files outside that directory cannot
be copied into the image during `docker build -f docker/web.Dockerfile web`.
Keep this bundle in sync with:

- repository `LICENSE`
- `docs/licenses/*.txt`

The web image copies these files into `/usr/share/doc/threatlens/` and also
generates runtime-specific compliance artifacts there during the Docker build,
including:

- `frontend-runtime-package-legal/` for package-published frontend legal files
- `frontend-os-packages.txt` for redistributed Alpine packages
- `frontend-os-package-metadata.tsv` for the shipped Alpine package metadata set
- `frontend-os-package-legal/` for per-package Alpine `APK-INFO` records plus any runtime `/usr/share/licenses/<package>/` trees
- `web/compliance/generate_frontend_os_package_artifacts.sh` is the shared generator for the frontend OS package inventory, metadata, and legal bundle

Supplemental exact notices cover runtime code emitted from development tools and
upstream runtime copyrights that a generic license text cannot replace:

- `Vite-7.3.8-LICENSE.txt`: Vite preload code and its published bundled notices.
- `Rollup-4.68.3-LICENSE.txt`: emitted Rollup namespace/interop helpers.
- `TailwindCSS-4.3.5-MIT.txt`: emitted theme, preflight and utility CSS.
- `NGINX-1.31.7-BSD-2-Clause.txt`: the exact nginx source copyright and conditions.

The release-process documentation records authoritative versions and hashes.
Keep all three license mirrors synchronized when their source versions change.
