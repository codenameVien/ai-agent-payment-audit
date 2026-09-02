FROM node:20-bookworm-slim

WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/dashboard/package.json ./apps/dashboard/package.json
COPY services/commerce-gateway/package.json ./services/commerce-gateway/package.json
COPY services/seller-service/package.json ./services/seller-service/package.json
RUN npm ci --workspace @pbl/dashboard --include-workspace-root
COPY apps/dashboard ./apps/dashboard
RUN npm run build --workspace @pbl/dashboard

ENV NODE_ENV=production
EXPOSE 3000
CMD ["npm", "run", "start", "--workspace", "@pbl/dashboard", "--", "-H", "0.0.0.0"]
