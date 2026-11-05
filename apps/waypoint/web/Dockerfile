# Stage 1: Install development dependencies
FROM node:22-slim AS development-dependencies-env
COPY . /app
WORKDIR /app
RUN npm ci

# Stage 2: Install production dependencies only
FROM node:22-slim AS production-dependencies-env
COPY ./package.json package-lock.json /app/
WORKDIR /app
RUN npm ci --omit=dev

# Stage 3: Build the application
FROM node:22-slim AS build-env
ARG BUILD_VERSION=unknown
ENV BUILD_VERSION=${BUILD_VERSION}
COPY . /app/
COPY --from=development-dependencies-env /app/node_modules /app/node_modules
WORKDIR /app
RUN npm run build

# Stage 4: Production runtime
FROM node:22-slim
COPY ./package.json package-lock.json /app/
COPY ./server.js ./private-config.js /app/
COPY --from=production-dependencies-env /app/node_modules /app/node_modules
COPY --from=build-env /app/build /app/build
WORKDIR /app

# Expose port
EXPOSE 3000

# Run the production server
CMD ["npm", "run", "start"]
