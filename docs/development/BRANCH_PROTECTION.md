# Branch protection intent

Protect `master` as the integration branch. Repository settings are managed in
GitHub and are not changed by this document.

- Require pull requests before merging; disable direct pushes where repository
  access settings allow it.
- Require the `tests` workflow to pass before merge. Do not merge while its status
  is failing or pending.
- Keep the required check tied to the complete offline suite in
  `.github/workflows/tests.yml`.
