"""Shared by every check in this package."""

# GET /ready is unauthenticated (a k8s-style readiness probe) -- the real
# exception text (which can embed a DSN host/port/credentials, an S3 endpoint,
# or an OIDC issuer's response body) goes only to the logs, for whoever's on
# call to look up; the HTTP response gets this fixed string regardless of which
# service failed or why.
UNHEALTHY_DETAIL = "dependency check failed; see server logs"
