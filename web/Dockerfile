FROM node:22.20.0-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN test -f package-lock.json && npm ci

COPY . .
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
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

FROM nginx:1.27-alpine
ARG BUILD_DATE=unknown
ARG VCS_REF=unknown
ENV THREATLENS_CSP_CONNECT_SRC="'self'" \
    THREATLENS_CSP_FRAME_SRC="'self'"
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
COPY --from=build /app/dist /usr/share/nginx/html
LABEL org.opencontainers.image.title="ThreatLens Web" \
      org.opencontainers.image.description="ThreatLens React/nginx frontend image" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="ThreatLens contributors" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
