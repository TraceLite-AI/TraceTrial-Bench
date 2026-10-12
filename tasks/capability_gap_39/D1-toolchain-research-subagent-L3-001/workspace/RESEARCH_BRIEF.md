# Toolchain Baseline Brief BLD-2026-Q3

The build-platform team is standardizing the default toolchain configuration for next quarter's monorepo migration and needs a verifiable fact report on shipped upstream defaults. The following five research angles are independent.

Every fact must include: (1) the fact value, (2) a verbatim quotation from the source document, and (3) the source URL.

## A1 · Jest configuration defaults

Canonical source (use this exact URL only): `https://raw.githubusercontent.com/jestjs/jest/v29.7.0/docs/Configuration.md`

- `A1-1` What is the documented default of the Jest configuration option `automock`?
- `A1-2` What is the documented default of the Jest configuration option `bail`?
- `A1-3` What is the documented default of the Jest configuration option `clearMocks`?
- `A1-4` What is the documented default of the Jest configuration option `collectCoverage`?
- `A1-5` What is the documented default of the Jest configuration option `errorOnDeprecated`?

## A2 · Prettier option defaults

Canonical source (use this exact URL only): `https://raw.githubusercontent.com/prettier/prettier/3.1.1/docs/options.md`

- `A2-1` What is the documented default of the Prettier option `printWidth`?
- `A2-2` What is the documented default of the Prettier option `tabWidth`?
- `A2-3` What is the documented default of the Prettier option `useTabs`?
- `A2-4` What is the documented default of the Prettier option `semi`?
- `A2-5` What is the documented default of the Prettier option `singleQuote`?

## A3 · Rollup configuration-option defaults

Canonical source (use this exact URL only): `https://raw.githubusercontent.com/rollup/rollup/v4.9.1/docs/configuration-options/index.md`

- `A3-1` What is the documented default of the Rollup option `cache`?
- `A3-2` What is the documented default of the Rollup option `logLevel`?
- `A3-3` What is the documented default of the Rollup option `output.compact`?
- `A3-4` What is the documented default of the Rollup option `output.entryFileNames`?
- `A3-5` What is the documented default of the Rollup option `output.extend`?

## A4 · Cargo configuration defaults

Canonical source (use this exact URL only): `https://raw.githubusercontent.com/rust-lang/cargo/0.75.0/src/doc/src/reference/config.md`

- `A4-1` What is the documented default of the Cargo configuration key `build.jobs`?
- `A4-2` What is the documented default of the Cargo configuration key `build.rustc`?
- `A4-3` What is the documented default of the Cargo configuration key `build.rustdoc`?
- `A4-4` What is the documented default of the Cargo configuration key `build.target-dir`?
- `A4-5` What is the documented default of the Cargo configuration key `build.incremental`?

## A5 · Rust release dates

Canonical source (use this exact URL only): `https://raw.githubusercontent.com/rust-lang/rust/1.40.0/RELEASES.md`

- `A5-1` What was the release date of Rust `1.39.0`?
- `A5-2` What was the release date of Rust `1.38.0`?
- `A5-3` What was the release date of Rust `1.37.0`?
- `A5-4` What was the release date of Rust `1.36.0`?
- `A5-5` What was the release date of Rust `1.35.0`?
