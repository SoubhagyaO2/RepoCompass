FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
# The API address is read when each request arrives, so it can be set at run time.
ENV REPOCOMPASS_API_URL=http://backend:8000
EXPOSE 3000
CMD ["npm", "run", "start"]
