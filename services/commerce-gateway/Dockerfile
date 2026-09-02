FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY services/commerce-gateway/package.json services/commerce-gateway/package.json
RUN npm ci --workspace @pbl/commerce-gateway --include-workspace-root
COPY services/commerce-gateway services/commerce-gateway
RUN npm run build --workspace @pbl/commerce-gateway

FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/services/commerce-gateway/dist ./services/commerce-gateway/dist
COPY --from=build /app/services/commerce-gateway/package.json ./services/commerce-gateway/package.json
CMD ["node", "services/commerce-gateway/dist/src/main.js"]
