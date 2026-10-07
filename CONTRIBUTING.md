# Contributing

Thank you for helping improve this demonstrator.

## Ground rules

1. **Synthetic data only.** Never add real or pseudonymised patient data, real identifiers, or real organisation identifiers. New fixtures must set `"synthetic": true`, use the `SYN-` prefix and pass `safety.py`.
2. **No conformance claims.** Do not describe the project as conformant with, certified for, or an implementation of EHDS, MyHealth@EU, NCPeH, XDS, MHD, sIPS or IPS. Describe what is demonstrated and how it is evidenced.
3. **Evidence over screens.** A behavioural change should come with a test. Changes affecting an acceptance invariant (AT-01..AT-12) must keep or extend the corresponding test in `tests/acceptance/`.
4. **Record decisions.** Architectural changes need a new or updated ADR in `adr/`.
5. **Licensing.** Do not paste ISO or IHE text, or terminology release content. Link to sources instead.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock -e ".[dev]"
ruff check src tests
pytest
```

External validators (optional locally, mandatory in CI):

```bash
make validate-ips    # needs Java 17+, downloads validator_cli.jar
make validate-pdfa   # needs veraPDF CLI (SCDPOC_VERAPDF_CLI)
```

If you change IPS composition for the fixtures, regenerate `fixtures/expected/digests.json` deliberately and explain why in the pull request.

## Changing the pinned standards

Update `config/ips-package.yml`, regenerate `config/ips-profile-digest.json` with `scripts/build_profile_digest.py <package.tgz>`, review `config/ehds-readiness.yml`, and note the change in `CHANGELOG.md`.
