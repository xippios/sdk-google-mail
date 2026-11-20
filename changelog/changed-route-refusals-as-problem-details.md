- A route answers an argument that its parameter's type refuses, or one that is missing,
  as problem details before the task runs: status 422, title `InvalidRequestError` and
  the detail `<argument>: <message>`, as the MCP tools word it. FastAPI's own 422 answered
  a `detail` list that echoed the value sent. (https://github.com/xippios/sdk-core/issues/66)
