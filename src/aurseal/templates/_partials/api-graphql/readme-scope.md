**In scope:** _(the queries and mutations this suite covers on {{ api_base_url }})_

**Out of scope**, and the risk each leaves:

- Load, query cost and depth limits: _(an expensive query that times out under load goes undetected)_
- Authorisation per field: _(data visible to the wrong caller goes undetected)_
- Subscriptions: _(real-time updates are not exercised)_
