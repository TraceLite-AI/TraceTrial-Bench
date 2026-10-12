ARG BASE_IMAGE=node:22-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3
FROM ${BASE_IMAGE}

ARG CODEX_VERSION=0.145.0
ARG LITELLM_VERSION=1.93.0
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
        bash ca-certificates curl git jq python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/litellm \
    && /opt/litellm/bin/python -m pip install --no-cache-dir \
        "litellm[proxy]==${LITELLM_VERSION}" \
    && npm install --global --registry "${NPM_REGISTRY:-https://registry.npmjs.org}" \
        "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force \
    && codex --version \
    && /opt/litellm/bin/litellm --version

ARG HARNESSBENCH_CONTEXT_SHA256

COPY harnessbench /opt/harnessbench-src/harnessbench
COPY harness_tracer /opt/harnessbench-src/harness_tracer

ENV PATH="/opt/litellm/bin:${PATH}" \
    PYTHONPATH=/opt/harnessbench-src \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1

RUN python3 -c "import harnessbench.container_worker"

LABEL org.harnessbench.harness="codex" \
      org.harnessbench.harness.version="0.145.0" \
      org.harnessbench.gateway="litellm-1.93.0" \
      org.harnessbench.context-sha256="${HARNESSBENCH_CONTEXT_SHA256}" \
      org.harnessbench.worker="v1"

WORKDIR /workspace
CMD ["sleep", "infinity"]
