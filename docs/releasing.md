# Releasing

1. Update the version in `pyproject.toml`, add the release date and notes to `CHANGELOG.md`, and run `uv run ruff check`, `uv run pytest`, and `uv build`.
2. Push a reviewed `vX.Y.Z` tag. The release workflow checks that the tag and package version
   match, checks the wheel and sdist contents, and creates the GitHub release with the changelog
   section and the built files.
3. PyPI publishing is opt-in and currently off. To enable it, add a trusted publisher on pypi.org
   for this repository, workflow `release.yml` and environment `pypi`, then set the repository
   variable `PUBLISH_PYPI` to `true`. The job uses the OIDC `id-token` permission; no API token is
   stored. A published version cannot be reused.
4. Review the generated GitHub release notes and wheel artifacts.
5. Update the Homebrew tap: calculate the source SHA-256, run `brew update-python-resources`, review the formula, and push it to `yulong-ge/homebrew-tap`.

Do not trigger a release from a development checkout.
