"""Generic dependency health checks, one module per service.

Each module imports its client library only inside itself (`postgres` needs the
`sqlalchemy` extra, `redis` the `redis` extra, `s3` the `s3` extra, `http` the
`http` extra), so importing one check never pulls in another's dependency. None
reads settings: the caller passes an engine, URL or client in. Importing this
package itself imports no check.
"""
