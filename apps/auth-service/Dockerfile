FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src ./src
RUN npm run build && npm prune --omit=dev

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production KEYS_DIR=/keys
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY package.json ./
COPY migrations ./migrations
# Pre-create the key dir so a named volume mounted here is writable by the node user.
RUN mkdir /keys && chown node:node /keys
USER node
EXPOSE 4000
CMD ["node", "dist/index.js"]
