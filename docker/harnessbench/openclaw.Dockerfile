ARG BASE_IMAGE=node:22-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3
FROM ${BASE_IMAGE}

ARG OPENCLAW_VERSION=2026.6.9
ARG DEBIAN_MIRROR
ARG DEBIAN_SECURITY_MIRROR
ARG NPM_REGISTRY

# The slim base has no CA bundle; Debian signatures protect this HTTP bootstrap.
RUN if [ -n "${DEBIAN_MIRROR}" ] || [ -n "${DEBIAN_SECURITY_MIRROR}" ]; then \
        test -n "${DEBIAN_MIRROR}" \
        && test -n "${DEBIAN_SECURITY_MIRROR}" \
        && debian_bootstrap_mirror="http://${DEBIAN_MIRROR#*://}" \
        && security_bootstrap_mirror="http://${DEBIAN_SECURITY_MIRROR#*://}" \
        && sed -i \
            -e "s|http://deb.debian.org/debian-security|${security_bootstrap_mirror}|g" \
            -e "s|https://deb.debian.org/debian-security|${security_bootstrap_mirror}|g" \
            -e "s|http://deb.debian.org/debian|${debian_bootstrap_mirror}|g" \
            -e "s|https://deb.debian.org/debian|${debian_bootstrap_mirror}|g" \
            /etc/apt/sources.list.d/debian.sources \
        && apt-get update \
        && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates \
        && rm -rf /var/lib/apt/lists/* \
        && sed -i \
            -e "s|${security_bootstrap_mirror}|${DEBIAN_SECURITY_MIRROR}|g" \
            -e "s|${debian_bootstrap_mirror}|${DEBIAN_MIRROR}|g" \
            /etc/apt/sources.list.d/debian.sources; \
    fi

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        bash ca-certificates curl git jq python3 sqlite3 \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global --registry "${NPM_REGISTRY:-https://registry.npmjs.org}" \
        "openclaw@${OPENCLAW_VERSION}" \
    && npm cache clean --force \
    && openclaw --version

ARG HARNESSBENCH_CONTEXT_SHA256

COPY harnessbench /opt/harnessbench-src/harnessbench
COPY harness_tracer /opt/harnessbench-src/harness_tracer

ENV PYTHONPATH=/opt/harnessbench-src \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN python3 -c "import harnessbench.container_worker"

LABEL org.harnessbench.harness="openclaw" \
      org.harnessbench.harness.version="2026.6.9" \
      org.harnessbench.context-sha256="${HARNESSBENCH_CONTEXT_SHA256}" \
      org.harnessbench.worker="v1"

WORKDIR /workspace
CMD ["sleep", "infinity"]
