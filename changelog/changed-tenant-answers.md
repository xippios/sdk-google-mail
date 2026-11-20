- `.copier-answers.yml` records four answers of sdk-template v0.4.0 ahead of its adoption:
  `write_server: true`, `tenant_param: account`,
  `tenant_credential_env: GOOGLE_<NAME>_REFRESH_TOKEN` and `has_reference_data: false`. The
  first three are the shape the package has by hand today: the adoption then renders the two
  servers' entry points, their binding to one account and the live run's credentials from
  them. The fourth has the adoption drop the reference scaffold, since the package packages
  no list. Nothing a caller uses changes here.
  (https://github.com/xippios/sdk-google-mail/issues/52)
