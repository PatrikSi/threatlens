FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN test -f package-lock.json && npm ci

COPY . .
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.27-alpine
ENV THREATLENS_CSP_CONNECT_SRC="'self'"
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
