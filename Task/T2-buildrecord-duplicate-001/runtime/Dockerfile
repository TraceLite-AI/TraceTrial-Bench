ARG BASE_IMAGE=harnessbench/openclaw:2026.6.9
FROM ${BASE_IMAGE}

USER root

RUN cp /opt/harnessbench-src/tracetrial_bench/container_worker.py \
        /opt/harnessbench-src/tracetrial_bench/container_worker_base.py

COPY T2-buildrecord-duplicate-001/runtime/native_worker.py \
     /opt/harnessbench-src/tracetrial_bench/container_worker.py
COPY T2-buildrecord-duplicate-001/runtime/duplicate_mcp.py \
     /opt/harnessbench-case/duplicate_mcp.py

RUN chmod 0555 /opt/harnessbench-case/duplicate_mcp.py \
    && python3 -m py_compile \
        /opt/harnessbench-src/tracetrial_bench/container_worker.py \
        /opt/harnessbench-case/duplicate_mcp.py

LABEL org.harnessbench.task="T2-buildrecord-duplicate-001" \
      org.harnessbench.execution.protocol="harnessbench_native_v1"
