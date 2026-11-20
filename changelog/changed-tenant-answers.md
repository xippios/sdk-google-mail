- `.copier-answers.yml` records three answers of sdk-template v0.4.0 ahead of its adoption:
  `write_server: true`, `tenant_param: account` and
  `tenant_credential_env: GOOGLE_<NAME>_REFRESH_TOKEN`, the shape the package has by hand
  today. The adoption then renders the write server, the accounts' registry and the live
  run's credentials from them, and nothing a caller uses changes here.
  (https://github.com/xippios/sdk-google-mail/issues/52)
