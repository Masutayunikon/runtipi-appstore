FROM node:20-slim AS base
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable

RUN apt-get update && apt-get install -y git

ENV GIT_REPO=${GIT_REPO}

# log GIT_REPO env var
RUN echo "Cloning from ${GIT_REPO}"

WORKDIR /app

RUN git clone ${GIT_REPO}



FROM base AS prod-deps
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --prod --frozen-lockfile

FROM base AS build
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile
RUN pnpm run build

FROM base
COPY --from=prod-deps /app/node_modules /app/node_modules
COPY --from=build /app/.output /app/dist
EXPOSE 3000

# node .output/server/index.mjs
CMD ["node", "dist/server/index.mjs"]
