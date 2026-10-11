# Upgrading

Notes for installations that predate the single-server layout and the environment-variable configuration.

## From the multi-service layout

Earlier versions ran the API, Akountz, Angula and the gateway as separate containers, with
identity in a second database. The server is one process with one database now (Studio is still
its own service):

- Run the new image against the **platform** database. `migrate` applies one new migration
  (`0014_identity`) that creates the identity tables beside the platform ones.
- Identity data lived in the separate `akountz` database. Copy its tables (`akz_*`, `permissions`,
  `perm_*`, `user_permissions`, `jwt_tokens`, `token_blacklist`) into the platform database before
  first use; the names and columns are unchanged. `PAWABASE_AKOUNTZ_DATABASE_URL` is no longer read.
- `PAWABASE_API_URL`, `PAWABASE_AKOUNTZ_URL`, `PAWABASE_ANGULA_URL`, `PAWABASE_GATEWAY_URL` and
  `PAWABASE_STUDIO_URL` are gone, as are `PAWABASE_PROCESSES` and `PAWABASE_STUDIO=off`. Studio
  finds the server through `PAWABASE_SERVER_URL`. `docker-compose.hosted.yml` and
  `docker-compose.cloud.yml` are replaced by `docker-compose.yml` and
  `docker-compose.external.yml`.


## From stored infrastructure settings

Stored `infra.database_url` and `infra.storage` still work and are reported
as deprecated; `python -m pawabase_server.environments.export_config` prints them as variables. **Mail is the one
break:** `infra.mail` is now ignored, so set `PAWABASE_MAIL_*` (or `<ENV>_MAIL_*`) before
upgrading or mail is only logged. Secrets are now bound to their environment; existing
ciphertext still opens, and `python -m pawabase_server.environments.reseal` rebinds it.
