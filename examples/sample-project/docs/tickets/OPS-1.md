# OPS-1: Rotate the image-hosting access key

## Status

owner check (2026-03-06)

## Labels

security, ops

## Scope

Replace the access key the app uses to upload recipe photos. The old key stops working after the switch.

## Done criteria

- The new key is stored in the secret manager, never in the repository.
- Uploading a photo from a test account works with the new key.
- The old key is revoked.

## Review level

high-risk (credentials)

## Evidence at close

Upload from the test account works. Waiting for the owner to confirm and to revoke the old key.
