ARG BASE_IMAGE=node:22-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3
FROM ${BASE_IMAGE}

ARG DEEPSEEK_HARNESS_VERSION=0.1.0-rc.6
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
        bash bubblewrap build-essential ca-certificates curl git jq python3 ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --prefix /opt/deepseek-runtime \
        --registry "${NPM_REGISTRY:-https://registry.npmjs.org}" \
        --no-audit --no-fund \
        "@deepseek-ai/dsh@${DEEPSEEK_HARNESS_VERSION}" \
        "@deepseek-ai/dsh-sdk-client@${DEEPSEEK_HARNESS_VERSION}" \
        "@deepseek-ai/dsh-sdk-jsonrpc-demo@${DEEPSEEK_HARNESS_VERSION}" \
        "@deepseek-ai/dsh-sdk-jsonrpc-server@${DEEPSEEK_HARNESS_VERSION}" \
        "@deepseek-ai/dsh-agent-spine-demo@${DEEPSEEK_HARNESS_VERSION}" \
        "@deepseek-ai/dsh-tool-terminal@${DEEPSEEK_HARNESS_VERSION}" \
        "@deepseek-ai/dsh-mcp-client@${DEEPSEEK_HARNESS_VERSION}" \
    && npm cache clean --force \
    && test -x /opt/deepseek-runtime/node_modules/.bin/dsh \
    && test -x /opt/deepseek-runtime/node_modules/.bin/dsh-jsonrpc-agent \
    && node -e "Promise.all([import('/opt/deepseek-runtime/node_modules/@deepseek-ai/dsh-sdk-client/lib/index.js'), import('/opt/deepseek-runtime/node_modules/@deepseek-ai/dsh-tool-terminal/lib/index.js')])"

ARG HARNESSBENCH_CONTEXT_SHA256

COPY harnessbench /opt/harnessbench-src/harnessbench
COPY harness_tracer /opt/harnessbench-src/harness_tracer

ENV PATH="/opt/deepseek-runtime/node_modules/.bin:${PATH}" \
    PYTHONPATH=/opt/harnessbench-src \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1

RUN python3 -c "import harnessbench.container_worker" \
    && node --check /opt/harnessbench-src/harnessbench/deepseek_bridge.mjs

LABEL org.harnessbench.harness="deepseek" \
      org.harnessbench.harness.version="0.1.0-rc.6" \
      org.harnessbench.source="https://github.com/deepseek-ai/deepseek-harness" \
      org.harnessbench.context-sha256="${HARNESSBENCH_CONTEXT_SHA256}" \
      org.harnessbench.worker="v1"

WORKDIR /workspace
CMD ["sleep", "infinity"]
