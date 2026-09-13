FROM --platform=$BUILDPLATFORM node:22.23.1-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN test -f package-lock.json && npm ci

COPY . .
ARG APP_VERSION=2.0.0
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_APP_VERSION=${APP_VERSION}
RUN node - <<'NODE' > /tmp/frontend-runtime-dependencies.txt
const fs = require('fs');
const lock = JSON.parse(fs.readFileSync('package-lock.json', 'utf8'));
const packages = lock.packages || {};
const rows = [];
for (const [packagePath, packageMeta] of Object.entries(packages)) {
  if (!packagePath.startsWith('node_modules/')) continue;
  if (packageMeta.dev) continue;
  const fallbackName = packagePath.slice('node_modules/'.length);
  const name = (packageMeta.name || fallbackName).trim();
  const version = (packageMeta.version || '').trim();
  if (!name || !version) continue;
  rows.push(`${name}==${version}`);
}
rows.sort((a, b) => a.localeCompare(b));
process.stdout.write(
  [
    '# ThreatLens frontend runtime dependency inventory',
    '# Generated during web image build from web/package-lock.json',
    '',
    ...rows,
    '',
  ].join('\n'),
);
NODE
RUN mkdir -p /tmp/frontend-docs \
    && node ./scripts/generate_runtime_package_metadata.mjs \
      --output /tmp/frontend-docs/frontend-runtime-package-metadata.json \
      --legal-output-dir /tmp/frontend-docs/frontend-runtime-package-legal
RUN npm run build

FROM nginx:1.31.3-alpine
RUN apk upgrade --no-cache libcrypto3 libssl3 libexpat libuuid
ARG BUILD_DATE=unknown
ARG APP_VERSION=2.0.0
ARG VCS_REF=unknown
ENV THREATLENS_CSP_CONNECT_SRC="'self'" \
    THREATLENS_CSP_FRAME_SRC="'self'" \
    APP_VERSION=${APP_VERSION}
COPY compliance/generate_frontend_os_package_artifacts.sh /tmp/generate_frontend_os_package_artifacts.sh
RUN chmod +x /tmp/generate_frontend_os_package_artifacts.sh \
    && mkdir -p /usr/share/doc/threatlens/licenses \
    && /tmp/generate_frontend_os_package_artifacts.sh \
      /usr/share/doc/threatlens/frontend-os-packages.txt \
      /usr/share/doc/threatlens/frontend-os-package-metadata.tsv \
      /usr/share/doc/threatlens/frontend-os-package-legal \
    && rm -f /tmp/generate_frontend_os_package_artifacts.sh
COPY compliance/LICENSE /usr/share/doc/threatlens/LICENSE
COPY compliance/README.md /usr/share/doc/threatlens/README.md
COPY compliance/licenses/ /usr/share/doc/threatlens/licenses/
COPY --from=build /tmp/frontend-runtime-dependencies.txt /usr/share/doc/threatlens/frontend-runtime-dependencies.txt
COPY --from=build /tmp/frontend-docs/frontend-runtime-package-metadata.json /usr/share/doc/threatlens/frontend-runtime-package-metadata.json
COPY --from=build /tmp/frontend-docs/frontend-runtime-package-legal /usr/share/doc/threatlens/frontend-runtime-package-legal
COPY package-lock.json /usr/share/doc/threatlens/frontend-package-lock.json
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html
# Run unprivileged with an immutable root filesystem. Only rendered config and
# nginx's bounded temporary files are writable at deployment time.
# Remove the base image's root-owned config so envsubst also works on older
# writable-root deployments without the Compose config tmpfs.
RUN mkdir -p /etc/nginx/conf.d \
    && rm -f /etc/nginx/conf.d/default.conf \
    && chown nginx:nginx /etc/nginx/conf.d \
    && chmod 0644 /etc/nginx/nginx.conf /etc/nginx/templates/default.conf.template \
    && chmod -R a+rX /usr/share/nginx/html /usr/share/doc/threatlens
USER nginx
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:3000/ || exit 1
LABEL org.opencontainers.image.title="ThreatLens Web" \
      org.opencontainers.image.description="ThreatLens React/nginx frontend image" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="ThreatLens contributors" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
