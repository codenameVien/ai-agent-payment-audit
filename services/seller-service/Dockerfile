FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY services/seller-service/package.json services/seller-service/package.json
RUN npm ci --workspace @pbl/seller-service --include-workspace-root
COPY services/seller-service services/seller-service
RUN npm run build --workspace @pbl/seller-service

FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/services/seller-service/dist ./services/seller-service/dist
COPY --from=build /app/services/seller-service/package.json ./services/seller-service/package.json
CMD ["node", "services/seller-service/dist/src/main.js"]
