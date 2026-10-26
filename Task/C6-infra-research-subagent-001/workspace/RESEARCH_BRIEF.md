# Platform Baseline Brief OPS-2026-Q3

The platform team is standardizing the baseline configuration for next quarter's fleet rebuild and needs a verifiable fact report on shipped upstream defaults. The following five research angles are independent.

Every fact must include: (1) the fact value, (2) a verbatim quotation from the source document, and (3) the source URL.

## A1 · Redis server configuration defaults

Canonical source (use this exact URL only): `https://raw.githubusercontent.com/redis/redis/7.2.4/redis.conf`

- `A1-1` What value does the shipped configuration set for the `timeout` directive?
- `A1-2` What value does the shipped configuration set for the `tcp-keepalive` directive?
- `A1-3` What value does the shipped configuration set for the `databases` directive?
- `A1-4` What value does the shipped configuration set for the `appendfsync` directive?
- `A1-5` What value does the shipped configuration set for the `hz` directive?

## A2 · PostgreSQL server configuration defaults

Canonical source (use this exact URL only): `https://raw.githubusercontent.com/postgres/postgres/REL_16_1/src/backend/utils/misc/postgresql.conf.sample`

- `A2-1` What is the commented-out default value for `max_connections`?
- `A2-2` What is the commented-out default value for `shared_buffers`?
- `A2-3` What is the commented-out default value for `work_mem`?
- `A2-4` What is the commented-out default value for `checkpoint_timeout`?
- `A2-5` What is the commented-out default value for `max_wal_size`?

## A3 · PHP production INI limits

Canonical source (use this exact URL only): `https://raw.githubusercontent.com/php/php-src/php-8.2.15/php.ini-production`

- `A3-1` What value does the production INI set for `memory_limit`?
- `A3-2` What value does the production INI set for `max_execution_time`?
- `A3-3` What value does the production INI set for `post_max_size`?
- `A3-4` What value does the production INI set for `upload_max_filesize`?
- `A3-5` What value does the production INI set for `default_socket_timeout`?

## A4 · Kernel sysctl defaults under /proc/sys/kernel

Canonical source (use this exact URL only): `https://raw.githubusercontent.com/torvalds/linux/v6.6/Documentation/admin-guide/sysctl/kernel.rst`

- `A4-1` What is the documented default value of the `oops_limit` sysctl?
- `A4-2` What is the documented default value of the `perf_event_paranoid` sysctl?
- `A4-3` What is the documented default value of the `perf_event_max_stack` sysctl?
- `A4-4` What is the documented default value of the `perf_event_max_contexts_per_stack` sysctl?
- `A4-5` What is the documented default value of the `warn_limit` sysctl?

## A5 · Kernel command-line parameter defaults

Canonical source (use this exact URL only): `https://docs.kernel.org/admin-guide/kernel-parameters.html`

- `A5-1` What is the documented default of the `audit_backlog_limit` boot parameter?
- `A5-2` What is the documented default of the `consoleblank` boot parameter?
- `A5-3` What is the documented default of the `cpu_init_udelay` boot parameter?
- `A5-4` What is the documented default of the `cmo_free_hint` boot parameter?
- `A5-5` What is the documented default of the `hibernate.compressor` boot parameter?
