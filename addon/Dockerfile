ARG BUILD_FROM
FROM ${BUILD_FROM}

# set in build.yaml -> point these at your fork
ARG DRONEVIS_REPO=https://github.com/privateee/ha_AirAlertVisualizer
ARG DRONEVIS_REF=main

ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1 PYTHONUTF8=1 PATH="/opt/venv/bin:${PATH}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip git ca-certificates \
 && python3 -m venv /opt/venv \
 && pip install --no-cache-dir "dronevisualizer @ git+${DRONEVIS_REPO}@${DRONEVIS_REF}" \
 && apt-get purge -y git && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
