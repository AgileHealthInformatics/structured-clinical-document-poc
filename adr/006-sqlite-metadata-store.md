# ADR-006: SQLite metadata store and write-once filesystem repository

- Status: Accepted
- Date: 2026-10-07

## Context

The design specifies PostgreSQL for inspectable metadata and a filesystem
volume for documents. The demonstrator must start from a clean clone with
`docker compose up` and no secrets (AT-12).

## Decision

- Registry metadata, repository index and audit log live in one SQLite file
  (`var/scdpoc.sqlite3`, WAL mode). The `RegistryStore` / `ObjectStore`
  interfaces in `xds/store.py` are small enough to re-implement on PostgreSQL.
- Repository objects are files created with `O_EXCL` and mode `0444`; the store
  never overwrites. Replacement is a new object plus an RPLC association.

## Consequences

- Single-node only; not a performance benchmark.
- Immutability is enforced by the application, not by WORM storage.
