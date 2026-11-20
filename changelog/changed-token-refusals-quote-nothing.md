- A token endpoint answer other than `invalid_grant` and `invalid_client` is an error that
  names its status, the request and the reason phrase, `503 from POST /token: Service
  Unavailable`, and quotes nothing of the answer. Its message and its `body` quoted the
  answer's text before, which a token endpoint may write from the credentials it was sent.
  (https://github.com/xippios/sdk-core/issues/156)
