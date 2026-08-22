# syntax=docker/dockerfile:1
FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG UI_BUILD_BASE_PATH=/apps/doc5
ENV NEXT_PUBLIC_BASE_PATH=${UI_BUILD_BASE_PATH} \
    NEXT_PUBLIC_API_BASE=${UI_BUILD_BASE_PATH}/api \
    NEXT_PUBLIC_EMBED=1 \
    NEXT_PUBLIC_FRAME_ANCESTORS="'self'" \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build
RUN npm prune --omit=dev

FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_BASE_PATH=/apps/doc5 \
    NEXT_PUBLIC_API_BASE=/apps/doc5/api \
    NEXT_PUBLIC_EMBED=1 \
    NEXT_PUBLIC_FRAME_ANCESTORS="'self'" \
    PORT=3000
WORKDIR /app
COPY --from=builder --chown=node:node /app/package.json /app/package-lock.json ./
COPY --from=builder --chown=node:node /app/node_modules ./node_modules
COPY --from=builder --chown=node:node /app/.next ./.next
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:'+process.env.PORT+'/apps/doc5').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0"]
