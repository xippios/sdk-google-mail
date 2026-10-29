Converted by `scripts/refresh_upstream.py` from https://developers.google.com/workspace/gmail/api/reference/quota; see `../FETCHED.md`.

# Usage limits

> As of **May 1, 2026**, the usage limits for this API were updated. Google Cloud projects that made
> any use of this API between November 2025 and April 2026 will continue with their previously set usage
> quotas. Cloud projects created on or after May 1, 2026 are subject to the new API quotas.
>
> For more information, including timelines, see [Google Workspace standardized model for agent tools and APIs](https://developers.google.com/workspace/tools-safety).
>
> For the full announcement, see [Agent tools and security updates for Google Workspace developers](http://workspaceupdates.googleblog.com/2026/05/agent-tools-and-security-updates-for-workspace-developers.html).

The Gmail API is subject to usage limits that restrict the rate at which you
can call API methods. Limits are defined in terms of [quota
units](https://developers.google.com/workspace/gmail/api/reference/quota#per-method-quota), an abstract unit of measurement representing
Gmail resource usage.

## Gmail API quotas

The Gmail API enforces two types of quotas:

- **Per minute per project:** This is the number of quota units your
  Google Cloud project can use in one minute.
- **Per minute per user per project:** This is the number of quota units any
  one particular user can use in your Cloud project. This limit helps
  ensure a fair distribution of usage among your users.

The following table details these limits:

| Usage limit type | Limit |
| --- | --- |
| Per minute per project | 1,200,000 quota units |
| Per minute per user per project | 6,000 quota units |

For information on handling limit errors, see
[Resolve errors](https://developers.google.com/workspace/gmail/api/guides/handle-errors).

## Daily billing threshold

This **per day per project** limit defines the maximum number of quota units
your Google Cloud project can use within a 24-hour period before charges apply.

Usage under this threshold doesn't incur extra charges and your Google Cloud
account isn't billed. Google will share full billing details later in 2026 with
at least 90 days' notice before any changes take effect.

You can't request an increase on this daily threshold limit.

The following table details the limit:

| Threshold limit type | Limit |
| --- | --- |
| Per day per project | 80,000,000 quota units |

For more information, see
[Google Workspace standardized model for agent tools and APIs](https://developers.google.com/workspace/tools-safety).

## Per-method quota usage

The number of quota units that each request consumes varies depending on the
method you call and the query parameters. The following table outlines the
per-method quota unit usage:

| Method | Quota units |
| --- | --- |
| `drafts.create` | 10 |
| `drafts.delete` | 10 |
| `drafts.get` | 20 (60 when `format` is `full`, `raw`, or `html`) |
| `drafts.list` | 5 |
| `drafts.send` | 100 |
| `drafts.update` | 15 |
| `getProfile` | 1 |
| `history.list` | 2 |
| `labels.create` | 5 |
| `labels.delete` | 5 |
| `labels.get` | 1 |
| `labels.list` | 1 |
| `labels.update` | 5 |
| `messages.attachments.get` | 20 |
| `messages.batchDelete` | 50 |
| `messages.batchModify` | 50 |
| `messages.delete` | 10 |
| `messages.get` | 20 (60 when `format` is `full`, `raw`, or `html`) |
| `messages.import` | 25 |
| `messages.insert` | 25 |
| `messages.list` | 5 |
| `messages.modify` | 5 |
| `messages.send` | 100 |
| `messages.trash` | 20 |
| `messages.untrash` | 5 |
| `settings.delegates.create` | 100 |
| `settings.delegates.delete` | 5 |
| `settings.delegates.get` | 1 |
| `settings.delegates.list` | 1 |
| `settings.filters.create` | 5 |
| `settings.filters.delete` | 5 |
| `settings.filters.get` | 1 |
| `settings.filters.list` | 1 |
| `settings.forwardingAddresses.create` | 100 |
| `settings.forwardingAddresses.delete` | 5 |
| `settings.forwardingAddresses.get` | 1 |
| `settings.forwardingAddresses.list` | 1 |
| `settings.getAutoForwarding` | 1 |
| `settings.getImap` | 1 |
| `settings.getPop` | 1 |
| `settings.getVacation` | 1 |
| `settings.sendAs.create` | 100 |
| `settings.sendAs.delete` | 5 |
| `settings.sendAs.get` | 1 |
| `settings.sendAs.list` | 1 |
| `settings.sendAs.update` | 100 |
| `settings.sendAs.verify` | 100 |
| `settings.updateAutoForwarding` | 5 |
| `settings.updateImap` | 5 |
| `settings.updatePop` | 100 |
| `settings.updateVacation` | 5 |
| `stop` | 50 |
| `threads.delete` | 20 |
| `threads.get` | 40 (120 when `format` is `full`) |
| `threads.list` | 10 |
| `threads.modify` | 10 |
| `threads.trash` | 20 |
| `threads.untrash` | 10 |
| `watch` | 100 |

When using the Gmail API, there's also a limit of 500 recipients per
email message.

## Resolve time-based quota errors

For all time-based errors (maximum of N requests per X minutes), we recommend
your code catches the exception and uses a *truncated exponential backoff* to make sure your
devices don't generate excessive load.

Exponential backoff is a standard error handling strategy for network applications. An
exponential backoff algorithm retries requests using exponentially increasing wait times
between requests, up to a maximum backoff time. If requests are still unsuccessful, it's
important that the delays between requests increase over time until the request is successful.

### Example algorithm

An exponential backoff algorithm retries requests exponentially, increasing the wait time
between retries up to a maximum backoff time. For example:

1. Make a request to Gmail API.
2. If the request fails, wait 1 + `random_number_milliseconds` and retry
   the request.
3. If the request fails, wait 2 + `random_number_milliseconds` and retry
   the request.
4. If the request fails, wait 4 + `random_number_milliseconds` and retry
   the request.
5. And so on, up to a `maximum_backoff` time.
6. Continue waiting and retrying up to some maximum number of retries, but don't increase the wait
   period between retries.

where:

- The wait time is `min(((2^n)+random_number_milliseconds), maximum_backoff)`,
  with `n` incremented by 1 for each iteration (request).
- `random_number_milliseconds` is a random number of milliseconds less than or
  equal to 1,000. This helps to avoid cases in which many clients are synchronized by
  some situation and all retry at once, sending requests in synchronized
  waves. The value of `random_number_milliseconds` is recalculated after each
  retry request.
- `maximum_backoff` is typically 32 or 64 seconds. The appropriate value
  depends on the use case.

The client can continue retrying after it has reached the `maximum_backoff` time.
Retries after this point don't need to continue increasing backoff time. For
example, if a client uses a `maximum_backoff` time of 64 seconds, then after reaching
this value, the client can retry every 64 seconds. At some point,
clients should be prevented from retrying indefinitely.

The wait time between retries and the number of retries depend on your use case
and network conditions.

## Pricing

All standard use of the Gmail API is available at no additional cost. Exceeding the quota
request limits is planned to incur charges to your Google Cloud billing account later in 2026.
For more information, see [Google Workspace standardized model
for agent tools and APIs](https://developers.google.com/workspace/tools-safety).

## Request a quota increase

Depending on your project's resource usage, you might want to request a quota
adjustment. API calls by a service account are considered to be using a
single account. Applying for an adjusted quota doesn't guarantee approval. Quota adjustment
requests that would significantly increase the quota value can take longer to be approved.

Not all projects have the same quotas. As you increasingly use Google Cloud over
time, your quota values might need to increase. If you expect a notable upcoming
increase in usage, you can proactively
[request quota adjustments](https://docs.cloud.google.com/docs/quotas/view-manage#requesting_higher_quota)
from the [Quotas & System Limits](https://console.cloud.google.com/iam-admin/quotas) page in the Google Cloud console.

To learn more, see the following resources:

- [About quota adjustments](https://docs.cloud.google.com/docs/quotas/overview#about_increase_requests)
- [View your quota usage and limits](https://docs.cloud.google.com/docs/quotas/view-manage#viewing_your_quota_console)
- [Request a higher quota limit](https://docs.cloud.google.com/docs/quotas/view-manage#requesting_higher_quota)

## Gmail MCP server quotas

The Gmail MCP server uses a query cost allocation metric. The
following tables detail the query cost for each Gmail MCP
server method by section:

### Gmail MCP quotas

The server enforces two types of quotas:

- **Per minute per project:** This is the query cost to your
  Cloud project for one minute.
- **Per minute per user per project:** This is the query cost to your
  Cloud project for one minute that any one particular user can use.

The following table details these quotas:

| Usage limit type | Query cost |
| --- | --- |
| Per minute per project | 1,200,000 |
| Per minute per user per project | 6,000 |

### Gmail MCP toolset quotas

The following table details the query cost for each `gmailmcp.googleapis.com`
toolset:

<table>
<tr><th>Endpoint</th><th>Tool</th><th>Query cost</th></tr>
<tr><td rowspan="9"><p><b>/mcp/v1</b></p></td><td><p><code>create_draft</code></p></td><td><p>10</p></td></tr>
<tr><td><p><code>get_thread</code></p></td><td><p>40</p></td></tr>
<tr><td><p><code>label_message</code></p></td><td><p>10</p></td></tr>
<tr><td><p><code>label_thread</code></p></td><td><p>10</p></td></tr>
<tr><td><p><code>list_drafts</code></p></td><td><p>5</p></td></tr>
<tr><td><p><code>list_labels</code></p></td><td><p>1</p></td></tr>
<tr><td><p><code>search_threads</code></p></td><td><p>10</p></td></tr>
<tr><td><p><code>unlabel_message</code></p></td><td><p>10</p></td></tr>
<tr><td><p><code>unlabel_thread</code></p></td><td><p>10</p></td></tr>
</table>

For more information, see the
[Gmail MCP API reference](https://developers.google.com/workspace/gmail/api/reference/mcp).

## Related topics

- [Batch requests](https://developers.google.com/workspace/gmail/api/guides/batch)
- [Performance tips](https://developers.google.com/workspace/gmail/api/guides/performance)
- [Limits for sending & getting mail](https://support.google.com/mail/answer/22839)
- [Gmail sending limits in Google Workspace](https://knowledge.workspace.google.com/admin/gmail/gmail-sending-limits-in-google-workspace)

Except as otherwise noted, the content of this page is licensed under the [Creative Commons Attribution 4.0 License](https://creativecommons.org/licenses/by/4.0/), and code samples are licensed under the [Apache 2.0 License](https://www.apache.org/licenses/LICENSE-2.0). For details, see the [Google Developers Site Policies](https://developers.google.com/site-policies). Java is a registered trademark of Oracle and/or its affiliates.

Last updated 2026-10-16 UTC.
