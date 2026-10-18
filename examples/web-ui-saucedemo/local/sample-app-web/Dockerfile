# syntax=docker/dockerfile:1
# Sauce Labs' sample-app-web (github.com/saucelabs/sample-app-web, MIT), the source of the
# public Sauce Demo shop, built from a pinned upstream commit and served by nginx.
FROM node:24.10.0-alpine@sha256:775ba24d35a13e74dedce1d2af4ad510337b68d8e22be89e0ce2ccc299329083 AS build

WORKDIR /src
ADD --keep-git-dir=false https://github.com/saucelabs/sample-app-web.git#e0948dd03f0042ad24f5e9ca324657f13193c19f .
# --ignore-scripts: the only install script is a git hook installer, which needs a .git.
RUN npm ci --ignore-scripts --no-audit --no-fund && npx vite build

FROM nginx:1.28.0-alpine@sha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/build /usr/share/nginx/html
