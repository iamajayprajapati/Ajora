FROM node:24-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG AJORA_API_URL=http://api:8000
ENV AJORA_API_URL=$AJORA_API_URL
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
