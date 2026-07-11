FROM node:24-alpine

WORKDIR /app
COPY package.json server.mjs ./
COPY src ./src

USER node
EXPOSE 8097
CMD ["node", "server.mjs"]
