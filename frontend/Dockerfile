FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* ./

# Install including devDependencies for build (Vite, etc.)
RUN npm ci --include=dev

COPY . .

RUN npm run build

# Runtime can stay production after build
ENV NODE_ENV=production

EXPOSE 4173

CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "4173"]

