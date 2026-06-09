FROM node:22-alpine

WORKDIR /app

COPY package.json .
ENV ELECTRON_SKIP_BINARY_DOWNLOAD=1
RUN npm install

COPY index.html vite.config.js ./
COPY src ./src

EXPOSE 5177

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
