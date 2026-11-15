# Release Process

ThreatLens treats the checked-in API and dependency artifacts as part of the shipped release contract.

## Public Release Gates

Before publishing a public tag, image, or source release:

1. Update the checked-in app version with `./scripts/set-version.sh X.Y.Z`.
2. Regenerate the API and dependency artifacts described below.
3. Verify that the OpenAPI contract anchor in `docs/reference/openapi.json` (`info.x-threatlens-contract-sha256`) is the one expected for the release. The tag workflow copies it into the generated GitHub release notes.
4. Verify that bundled license texts and package legal inventories still match the shipped runtime stack and assets.
5. Refresh the image build-context mirrors under `backend/compliance/` and `web/compliance/`.
   Verify the checked-in inventories and legal files against the built native
   image pair with `python3 scripts/verify_image_dependency_artifacts.py
   --backend-image IMAGE --web-image IMAGE`. The reference OS inventory is
   qualified on amd64; each image also ships its own platform's inventory.
6. For capacity-sensitive changes, run the manual **Capacity release comparison**
   workflow against two committed refs with the same measurement contract, or
   run both refs sequentially on the target host. Keep the result artifacts and
   review outcome counts, queue/deadline/lock observations, and memory alongside
   latency. Insufficient successful samples are inconclusive. See the
   [capacity runbook](capacity-baseline.md#release-trend-workflow).

## Version Metadata

The repository root `VERSION` file is the release version source for automation.

Run this before opening the release commit:

```bash
./scripts/set-version.sh 1.0.0
```

The helper keeps these derived files aligned:

- `VERSION`
- `backend/app/version.py`
- `docker/backend.Dockerfile`
- `docker/web.Dockerfile`
- `docker-compose.build.yml`
- `web/package.json`
- `web/package-lock.json`

Public release tags must use `vX.Y.Z` and must match the checked-in `VERSION` value. For example, `VERSION=1.0.0` must be released with tag `v1.0.0`.

## Supported Code Lines

- Public releases should use immutable tags in `vX.Y.Z` format.
- Once the repository is public, `main` is the active public development line and receives best-effort community support.
- Only the latest published tag is considered a supported release line once tags exist.
- If no public tag exists yet, record the deployed commit SHA, published image tags, and checked-in OpenAPI contract anchor together as the current release reference.

## Container Image Publishing

`.github/workflows/publish-images.yml` publishes multi-architecture Linux images to GitHub Container Registry on pushes to `main`, tags matching `v*.*.*`, and manual workflow runs. It first pushes untagged per-platform digests, scans every digest, and smoke-tests the exact backend/web pair for both supported architectures (using QEMU for arm64 on hosted runners). A single promotion job assembles those same digests into multi-architecture manifests and assigns public tags only after all checks pass.

The pre-merge quality workflow also builds, scans and starts both amd64 and
arm64 application stacks. ARM64 uses QEMU on the disposable CI runner; shared
local hosts do not need emulation installed. The amd64 check compares all
checked-in runtime/OS inventories and bundled legal files with the actual image
pair, so dependency additions cannot silently leave release notices stale.
These source-build checks supplement the publication workflow's exact-digest
checks; they do not replace them.

External workflow actions use reviewed immutable commit references with release
version comments. Dependabot checks those actions weekly and scans both `/` and
`/docker` for container dependencies. Keep the Trivy CLI version explicit in
addition to the scanner action reference; the action's default CLI can lag
security fixes. Registry attestation steps explicitly disable optional artifact
storage records, preserving their existing permissions and registry behavior.

JavaScript actions require a Node 24-capable runner, including Actions runner
2.327.1 or newer as documented by [Checkout](https://github.com/actions/checkout/blob/v7.0.1/README.md).
The self-hosted capacity runner must satisfy that existing requirement; a local
configuration review does not validate its installed runner version.

Published images:

- `ghcr.io/patriksi/threatlens-backend`
- `ghcr.io/patriksi/threatlens-web`

Tag behavior:

- `main` branch builds publish `:latest`, `:main`, and `:sha-<commit>`.
- Version tags such as `v1.0.0` publish `:v1.0.0`, `:1.0.0`, `:1.0`, `:latest`, and `:sha-<commit>`.
- Both images are built for `linux/amd64` and `linux/arm64`.
- Immutable `:sha-<full-commit>` manifests are created for the backend and web before mutable channel aliases are moved.
- Built images receive `org.opencontainers.image.version`, `org.opencontainers.image.created`, and `org.opencontainers.image.revision` labels.
- Tag builds publish or update a GitHub Release with the image tags, image digests, and OpenAPI contract anchor.

After the first package publish, verify in GitHub Packages that both container packages are public if the release is intended for unauthenticated installs.

## Release Flow

```bash
./scripts/set-version.sh 1.0.0
./backend/.venv/bin/python backend/scripts/generate_api_reference.py
./backend/.venv/bin/python backend/scripts/generate_runtime_lockfile.py --upgrade
cd backend && ./.venv/bin/python -m pytest
cd ../web && npm test && npm run lint && npm run build
cd ..
git add .
git commit -m "Release version 1.0.0"
git tag -a v1.0.0 -m "ThreatLens v1.0.0"
git push origin main v1.0.0
```

Only push the tag after the release commit is on the intended commit. The image workflow rejects a release tag whose `vX.Y.Z` value does not match `VERSION`.

## Contract Artifact Workflow

When a change affects the published API contract:

```bash
./backend/.venv/bin/python backend/scripts/generate_api_reference.py
```

Any canonical `/v1` route addition, removal, method/path change, rename, endpoint
move, or data-policy dependency change must also update
`backend/app/core/data_policy_route_manifest.py` with its reviewed governance
class. Validate the exact mounted-route attestation before regenerating the
contract:

```bash
(cd backend && ./.venv/bin/python -m pytest \
  tests/unit/test_data_policy_route_manifest.py)
```

Application startup rejects manifest drift, but keeping the manifest and its
test in the same commit makes the security-sensitive route classification
reviewable before deployment.

That command refreshes both `docs/reference/api.md` and `docs/reference/openapi.json`. The generated schema now carries an immutable contract digest at `info.x-threatlens-contract-sha256`; public release notes should record that digest alongside the eventual `vX.Y.Z` tag.

The generator always reads the checked-in `VERSION` file for source artifacts. An exported runtime `APP_VERSION` does not change the generated contract version.

When a change affects shipped runtime dependencies, bundled assets, or redistribution guidance:

```bash
./backend/.venv/bin/python backend/scripts/generate_runtime_lockfile.py --upgrade
export BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export THREATLENS_BUILD_VERSION="$(cat VERSION)"
export VCS_REF="$(git rev-parse HEAD)"
BACKEND_IMAGE=$(docker build \
  --build-arg APP_VERSION="$THREATLENS_BUILD_VERSION" \
  --build-arg BUILD_DATE="$BUILD_DATE" \
  --build-arg VCS_REF="$VCS_REF" \
  -q -f docker/backend.Dockerfile backend)
docker run --rm -v "$PWD":/src -w /src "$BACKEND_IMAGE" sh -lc '
  rm -rf /src/docs/reference/backend-runtime-package-legal /src/docs/reference/backend-os-package-legal &&
  cp /usr/share/doc/threatlens/backend-runtime-dependencies.txt /src/docs/reference/backend-runtime-dependencies.txt &&
  cp /usr/share/doc/threatlens/backend-runtime-package-metadata.json /src/docs/reference/backend-runtime-package-metadata.json &&
  cp -R /usr/share/doc/threatlens/backend-runtime-package-legal /src/docs/reference/backend-runtime-package-legal &&
  cp /usr/share/doc/threatlens/backend-os-packages.txt /src/docs/reference/backend-os-packages.txt &&
  cp -R /usr/share/doc/threatlens/backend-os-package-legal /src/docs/reference/backend-os-package-legal'
WEB_IMAGE=$(docker build \
  --build-arg APP_VERSION="$THREATLENS_BUILD_VERSION" \
  --build-arg BUILD_DATE="$BUILD_DATE" \
  --build-arg VCS_REF="$VCS_REF" \
  -q -f docker/web.Dockerfile web)
docker run --rm -v "$PWD":/src -w /src "$WEB_IMAGE" sh -lc '
  rm -rf /src/docs/reference/frontend-runtime-package-legal /src/docs/reference/frontend-os-package-legal &&
  cp /usr/share/doc/threatlens/frontend-runtime-dependencies.txt /src/docs/reference/frontend-runtime-dependencies.txt &&
  cp /usr/share/doc/threatlens/frontend-runtime-package-metadata.json /src/docs/reference/frontend-runtime-package-metadata.json &&
  cp -R /usr/share/doc/threatlens/frontend-runtime-package-legal /src/docs/reference/frontend-runtime-package-legal &&
  cp /usr/share/doc/threatlens/frontend-os-packages.txt /src/docs/reference/frontend-os-packages.txt &&
  cp /usr/share/doc/threatlens/frontend-os-package-metadata.tsv /src/docs/reference/frontend-os-package-metadata.tsv &&
  cp -R /usr/share/doc/threatlens/frontend-os-package-legal /src/docs/reference/frontend-os-package-legal'
```

The lockfile command resolves dependencies without consulting installed distributions. Omit `--upgrade` to validate that the existing lock is a complete resolution of `backend/requirements.txt`; normal validation reproduces the checked-in lock byte-for-byte. Use `--upgrade` only when intentionally refreshing dependency pins.

That sequence intentionally refreshes the checked-in backend runtime lockfile, builds the backend and web images, and copies the packaged compliance artifacts back into `docs/reference/`. Before building, keep the mirrored `backend/compliance/` and `web/compliance/` bundles aligned with the repository `LICENSE` and `docs/licenses/*.txt`. Those artifacts cover both the application dependency layers and the redistributed OS package layers shipped by the repository Dockerfiles. The `docker-compose.build.yml` source-build override forwards exported `THREATLENS_BUILD_VERSION`, `BUILD_DATE`, and `VCS_REF` values into every built ThreatLens image, so local compose builds and the explicit compliance rebuild commands carry matching OCI version and provenance labels when those values are set.

The ANTLR Python runtime 4.13.2 wheel omits its standalone BSD notice.
`docs/licenses/ANTLR-4.13.2-BSD-3-Clause.txt` and both compliance mirrors preserve
the exact [official release-tag license](https://github.com/antlr/antlr4/blob/4.13.2/LICENSE.txt)
separately from the generated wheel inventory. Keep that supplement while this
distribution is shipped; its SHA-256 is recorded in `backend/compliance/README.md`.

The frontend also emits runtime code from development tools. Preserve these
exact supplemental files in `docs/licenses/` and both compliance mirrors:

| File | Authoritative distribution | SHA-256 |
| --- | --- | --- |
| `Vite-7.3.8-LICENSE.txt` | [vite 7.3.8](https://registry.npmjs.org/vite/-/vite-7.3.8.tgz) | `a77a1c089806b39ad339535bdf3677f636c91d96693e8ad7b11fe733f650ea64` |
| `TailwindCSS-4.3.5-MIT.txt` | [tailwindcss 4.3.5](https://registry.npmjs.org/tailwindcss/-/tailwindcss-4.3.5.tgz) | `60e0b68c0f35c078eef3a5d29419d0b03ff84ec1df9c3f9d6e39a519a5ae7985` |
| `Rollup-4.68.3-LICENSE.txt` | [rollup 4.68.3](https://registry.npmjs.org/rollup/-/rollup-4.68.3.tgz) | `fa1bd040c5bdeefe65b3821cebf474f2733ce65df13089bd151dda1778e62fe8` |
| `NGINX-1.31.7-BSD-2-Clause.txt` | [nginx 1.31.7](https://nginx.org/download/nginx-1.31.7.tar.gz) | `08845fe39e06b51dad7685c28140ab49577a86e947523e16b536a46caf89ad5c` |

Vite preload code, Rollup namespace/interop helpers and Tailwind CSS can appear in
the production bundle even though their packages are build dependencies excluded
from the generated runtime graph. nginx's exact copyright notice also accompanies
the generic BSD text. Preserve the entire upstream payload, including bundled
third-party notices; recheck the source archive, lock integrity and resulting
bundle whenever these versions change. These notices supplement the actual
package/OS inventories and do not constitute legal clearance.

The four generated `*-package-legal/` trees preserve upstream legal files byte-for-byte. Repository diff hygiene therefore excludes those trees from whitespace-style and conflict-marker heuristics while continuing to check first-party compliance files, dependency manifests, package inventories, metadata, and source code.

The backend image installs its Python application dependency layer from the checked-in `backend/requirements-lock.txt` file, and the frontend image resolves its application dependency layer from `web/package-lock.json`. The Dockerfiles and Compose base images use explicit version tags. The backend installs font packages and upgrades inherited PCRE2 and Perl from apt, enforcing Debian's `10.42-1+deb12u2` and `5.36.0-7+deb12u4` security updates or newer; the web image upgrades all installed Alpine packages from its configured release repositories and requires TIFF `4.7.2-r0` or newer during builds. Refresh base images and package installation layers for security rebuilds (`--pull --no-cache`), then scan the actual image: refreshing apt indexes alone does not upgrade inherited packages. ThreatLens does not claim byte-for-byte rebuild reproducibility: builds depend on external registries, OS package repositories, and lockfile-resolved application packages. Regenerate and review the packaged OS inventories for the actual release images. Both release platform digests must pass the existing fixable HIGH/CRITICAL vulnerability gate. A short upgrade allowlist can leave inherited libraries vulnerable even with a refreshed base tag. See the [Alpine package-manager guidance](https://docs.alpinelinux.org/user-handbook/0.1a/Working/apk.html) for release-branch upgrades.

## Files to Review Before Release

- `README.md`
- `docs/reference/api.md`
- `docs/reference/openapi.json`
- `docs/reference/backend-runtime-dependencies.txt`
- `docs/reference/frontend-runtime-dependencies.txt`
- `docs/reference/backend-runtime-package-metadata.json`
- `docs/reference/frontend-runtime-package-metadata.json`
- `docs/reference/backend-runtime-package-legal/`
- `docs/reference/frontend-runtime-package-legal/`
- `docs/reference/backend-os-packages.txt`
- `docs/reference/backend-os-package-legal/`
- `docs/reference/frontend-os-packages.txt`
- `docs/reference/frontend-os-package-metadata.tsv`
- `docs/reference/frontend-os-package-legal/`
- `docs/licenses/`

## Packaged Backend Image Metadata

Built backend images write release-compliance metadata to:

- `/usr/share/doc/threatlens/LICENSE`
- `/usr/share/doc/threatlens/README.md`
- `/usr/share/doc/threatlens/licenses/`
- `/usr/share/doc/threatlens/backend-requirements.txt`
- `/usr/share/doc/threatlens/backend-requirements-lock.txt`
- `/usr/share/doc/threatlens/backend-runtime-dependencies.txt`
- `/usr/share/doc/threatlens/backend-runtime-package-metadata.json`
- `/usr/share/doc/threatlens/backend-runtime-package-legal/`
- `/usr/share/doc/threatlens/backend-os-packages.txt`
- `/usr/share/doc/threatlens/backend-os-package-legal/`

Built web images write release-compliance metadata to:

- `/usr/share/doc/threatlens/LICENSE`
- `/usr/share/doc/threatlens/README.md`
- `/usr/share/doc/threatlens/licenses/`
- `/usr/share/doc/threatlens/frontend-package-lock.json`
- `/usr/share/doc/threatlens/frontend-runtime-dependencies.txt`
- `/usr/share/doc/threatlens/frontend-runtime-package-metadata.json`
- `/usr/share/doc/threatlens/frontend-runtime-package-legal/`
- `/usr/share/doc/threatlens/frontend-os-packages.txt`
- `/usr/share/doc/threatlens/frontend-os-package-metadata.tsv`
- `/usr/share/doc/threatlens/frontend-os-package-legal/`
