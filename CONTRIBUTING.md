# Contributing to FetchCore

FetchCore turns software repositories into **typed, traceable, backend-neutral evidence**. Contributions should preserve the separation **retrieval ≠ satisfaction ≠ authority**. FetchCore is not an agent framework.

## Start from a clean checkout

Requirements: Python 3.11+ and Git. Docker/Compose are optional for backend and container checks.

```bash
git clone https://github.com/oalangomes/fetchcore.git
cd fetchcore
python -m pip install -e ".[dev]"
fetchcore --help
make check
```

The default checks cover lint, types, tests/coverage, packaging and documentation. Backend-dependent integration tests and the self-dogfood workflow are separate gates. Do not mistake passing service health for a successfully indexed repository. See the [getting-started guide](docs/guides/getting-started.md) and [documentation index](docs/README.md).

## Proposing changes

- Search existing [issues](https://github.com/oalangomes/fetchcore/issues) and describe the problem before a substantial new feature.
- Keep PRs small, include reproducible tests, and link the issue and affected contracts.
- For changes to retrieval or indexing, preserve repository identity, revision, evidence provenance and backend-neutral contracts. Include baseline, controlled comparison, metrics and regressions where relevance, ranking, budgets or performance change.
- Declare optional backend capabilities explicitly; never silently repair incompatible schemas, downgrade retrieval modes, or promote a backend score into task authority.
- Run `make check`; document any optional backend/runtime gate that was not executed, and attach measured evidence when claiming improvement.
- Canonical documentation is English. **Do not edit `README.pt-BR.md` or `docs/pt-BR/` directly**: they are generated from the project localization contract, with source fingerprints and reviewable provenance. See [documentation localization](docs/guides/documentation-localization.md).

## License and supplied material

FetchCore's repository license is [MIT](LICENSE). By contributing, you confirm that you have the rights needed to offer your contribution under MIT; retain third-party copyright and license notices, and identify any reused external material in the PR. Do not commit proprietary code, personal data, secrets, private configuration, internal URLs, or real customer repositories.

The project includes code extracted from historical upstream work, with provenance documented in [historical extraction notes](docs/history/extraction/README.md). Extraction provenance does not itself establish copyright ownership or permissions. Any questions about reuse or upstream attribution must be resolved before redistribution.

## Security reports

Please do not report exploitable security flaws or leaked secrets in public issues. Follow [private vulnerability reporting guidance](SECURITY.md).
