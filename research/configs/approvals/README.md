# Approvals

`paper_approval.json` is created ONLY by the user, locally, with `python -m tradelab.execution.approve` and a secret key
(`CC_APPROVAL_KEY`) that agents and this repository never hold. No approval record exists in this repository and none may
be created by an agent. A record binds account id, instrument conIds, strategy id+version, capital allocation, the sha256
of the risk policy file, and issue/expiry times; any change or expiry makes the record fail verification.
Do not commit a real record (it contains an account id; the repository is public).
LIVE has no approval path at all.
