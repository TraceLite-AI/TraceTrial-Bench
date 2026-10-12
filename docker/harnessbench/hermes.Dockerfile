# Use a Docker Hub mirror by default while keeping the source digest pinned.
ARG BASE_IMAGE=docker.1ms.run/nousresearch/hermes-agent:v2026.7.20@sha256:f7b35053268f532f98955195c909f15a230470fbcbdacaa9fdecb95707dad04a
FROM ${BASE_IMAGE}

ARG HARNESSBENCH_CONTEXT_SHA256

USER root

COPY harnessbench /opt/harnessbench-src/harnessbench
COPY harness_tracer /opt/harnessbench-src/harness_tracer

ENV PYTHONPATH=/opt/harnessbench-src \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN python3 -c "import harnessbench.container_worker" \
    && command -v hermes >/dev/null

LABEL org.harnessbench.harness="hermes" \
      org.harnessbench.harness.version="0.19.0" \
      org.harnessbench.context-sha256="${HARNESSBENCH_CONTEXT_SHA256}" \
      org.harnessbench.worker="v1"

WORKDIR /workspace
CMD ["sleep", "infinity"]
