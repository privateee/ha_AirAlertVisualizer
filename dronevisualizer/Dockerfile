ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

# point these at your fork if you fork the repo (also set in build.yaml)
ARG DRONEVIS_REPO=https://github.com/privateee/ha_AirAlertVisualizer
ARG DRONEVIS_REF=main

ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1 PYTHONUTF8=1

# git to fetch the app; build-base only if a musl wheel is missing for an arch
RUN apk add --no-cache git \
 && apk add --no-cache --virtual .build gcc musl-dev libffi-dev python3-dev \
 && pip install --no-cache-dir "dronevisualizer @ git+${DRONEVIS_REPO}@${DRONEVIS_REF}" \
 && apk del .build git \
 && rm -rf /root/.cache

# s6 service (rootfs/etc/services.d/dronevisualizer/run)
COPY rootfs /
RUN chmod a+x /etc/services.d/dronevisualizer/run
