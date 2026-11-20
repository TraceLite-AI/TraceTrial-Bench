ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG OPENHANDS_VERSION=1.16.0
ARG DEBIAN_MIRROR
ARG DEBIAN_SECURITY_MIRROR

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
        bash ca-certificates curl git jq sqlite3 tmux procps \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/openhands \
    && /opt/openhands/bin/pip install --no-cache-dir \
        "openhands==${OPENHANDS_VERSION}" \
    && /opt/openhands/bin/openhands --version

COPY vendor-sdk /tmp/vendor-sdk
RUN if [ -d /tmp/vendor-sdk/openhands-sdk ] && [ -d /tmp/vendor-sdk/openhands-tools ]; then \
        /opt/openhands/bin/pip install --no-cache-dir --no-deps \
            /tmp/vendor-sdk/openhands-sdk \
            /tmp/vendor-sdk/openhands-tools; \
    fi \
    && rm -rf /tmp/vendor-sdk

ARG HARNESSBENCH_CONTEXT_SHA256

COPY tracetrial_bench /opt/harnessbench-src/tracetrial_bench
COPY harness_tracer /opt/harnessbench-src/harness_tracer

ENV PATH="/opt/openhands/bin:${PATH}" \
    PYTHONPATH=/opt/harnessbench-src \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    OPENHANDS_SUPPRESS_BANNER=1

RUN python3 -c "import tracetrial_bench.container_worker" \
    && command -v openhands >/dev/null

LABEL org.harnessbench.harness="openhands" \
      org.harnessbench.harness.version="${OPENHANDS_VERSION}" \
      org.harnessbench.context-sha256="${HARNESSBENCH_CONTEXT_SHA256}" \
      org.harnessbench.worker="v1"

WORKDIR /workspace
CMD ["sleep", "infinity"]
