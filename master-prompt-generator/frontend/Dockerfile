# syntax=docker/dockerfile:1
FROM node:22-alpine AS build

WORKDIR /app

# The lockfile has to come with package.json: every dependency here is on a
# caret range, so `npm install` from package.json alone re-resolves all 32 to
# whatever is newest at build time. That makes an image built today different
# from one built last week, and turns any upstream minor release into a build
# that breaks on a machine where nothing changed. `npm ci` installs the
# locked tree exactly, and fails loudly if the two files disagree.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json tsconfig.app.json tsconfig.node.json Vite.config.ts tailwind.config.js index.html ./
COPY src ./src

ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/ >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
