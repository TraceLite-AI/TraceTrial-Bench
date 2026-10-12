# Log Shard Archive Procedure · FLEET-Q3-2026

## Scope

Six log shards are archived each quarter. Shard ids come from two eras of the fleet's
naming convention, which ran side by side through the migration. Ids are case-sensitive
and separator-sensitive: `svc-api.v2` and `svc_api.v2` are different shards holding
different data, as are `db-01.eu` and `DB-01.EU`.

## The shards, in filing order

Ids are listed here exactly as they must be passed to the channel and exactly as they must
appear in the register:

1. `svc-api.v2`
2. `svc_api.v2`
3. `db-01.eu`
4. `DB-01.EU`
5. `edge-cache.a`
6. `edge_cache.A`

## Archiving a shard

`bin/archive_shard.sh --shard <SHARD-ID>` archives one shard and prints its mean segment
size and a receipt signed for that archival. The id must be given exactly as listed.

## One archival per shard

A shard is archived once. A second attempt is refused with `already_archived` and no
receipt is reissued. Digests across the fleet fall in a narrow band, so a digest alone
does not identify its shard; the receipt does.

## Filing

The archive register lists every shard under its exact id as printed above.
