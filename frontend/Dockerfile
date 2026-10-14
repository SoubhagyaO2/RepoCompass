FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Next resolves rewrites at build time, so the API address is a build argument.
ARG REPOCOMPASS_API_URL=http://backend:8000
ENV REPOCOMPASS_API_URL=$REPOCOMPASS_API_URL
RUN npm run build
EXPOSE 3000
CMD ["npm", "run", "start"]
