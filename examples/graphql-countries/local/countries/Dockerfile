# syntax=docker/dockerfile:1
# The Countries GraphQL API (github.com/trevorblades/countries, MIT), built from a pinned
# upstream commit. Upstream deploys it as a Cloudflare Worker; here the same GraphQL Yoga
# handler is served by Bun, so the schema and resolvers are upstream's own, unchanged.
FROM oven/bun:1.2.21-alpine@sha256:02564b43c26a0dc156ff4b70d7ccb68ebae5c486771d699ed1520c42e6ac5cb0

WORKDIR /app
ADD --keep-git-dir=false https://github.com/trevorblades/countries.git#5a150acb0ef9fc0f220db3f154896f1a5c37c405 .
RUN bun install --frozen-lockfile --production
COPY serve.ts .

USER bun
EXPOSE 4000
CMD ["bun", "run", "serve.ts"]
