# MinIO withdrew its public registry image. Its community edition is now
# source-only, so build the final community release from its archived source.
# This preserves MinIO's on-disk format for existing Pawabase installations.
FROM golang:1.24-alpine AS build

ARG MINIO_VERSION=RELEASE.2025-10-15T17-29-55Z

RUN apk add --no-cache git
WORKDIR /src
RUN git clone --depth 1 --branch "${MINIO_VERSION}" https://github.com/minio/minio.git . \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/minio .

FROM alpine:3.21

RUN apk add --no-cache ca-certificates
COPY --from=build /out/minio /usr/local/bin/minio

VOLUME ["/data"]
ENTRYPOINT ["minio"]
