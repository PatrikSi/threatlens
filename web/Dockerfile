FROM node:22.20.0-alpine@sha256:dbcedd8aeab47fbc0f4dd4bffa55b7c3c729a707875968d467aaaea42d6225af AS build
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
RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
ENV THREATLENS_CSP_CONNECT_SRC="'self'"
RUN mkdir -p /usr/share/doc/threatlens/licenses
COPY compliance/LICENSE /usr/share/doc/threatlens/LICENSE
COPY compliance/THIRD_PARTY_NOTICES.md /usr/share/doc/threatlens/THIRD_PARTY_NOTICES.md
COPY compliance/licenses/ /usr/share/doc/threatlens/licenses/
COPY --from=build /tmp/frontend-runtime-dependencies.txt /usr/share/doc/threatlens/frontend-runtime-dependencies.txt
COPY package-lock.json /usr/share/doc/threatlens/frontend-package-lock.json
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
