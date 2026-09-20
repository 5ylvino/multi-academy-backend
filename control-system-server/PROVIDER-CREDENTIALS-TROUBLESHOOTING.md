# Provider credentials troubleshooting

## Symptom

The provider **Test** action returns:

```text
No active credentials stored — configure secrets before live ping.
```

## Cause

Provider credentials are not read directly from environment variables. They are
stored in the control database in the `provider_secrets` table as encrypted,
versioned records attached to a `provider_configs` row.

The message means that the API found no active credentials it could decrypt. It
can happen when:

- credentials were never entered for the production provider configuration;
- the credentials were saved against a different provider configuration;
- `SECRETS_ENCRYPTION_KEY` is missing in production;
- the encryption key changed after credentials were saved; or
- the production key is not a valid Fernet key, causing decryption to fail.

Development behaves differently: when `ENVIRONMENT=development` and no
encryption key is configured, the API derives a deterministic key from
`JWT_SECRET`. This makes local credentials survive restarts without extra
configuration. Production does not use this fallback.

## Production setup

Generate a valid Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the generated value as `SECRETS_ENCRYPTION_KEY` in the production
environment. Fernet keys are URL-safe base64 strings representing 32 bytes;
an arbitrary hexadecimal string is not a valid Fernet key.

Then:

1. Restart the control API so it loads the key.
2. Open the production control dashboard.
3. Select the provider configuration and enter its credentials again.
4. Save the credentials.
5. Click **Test**.

If the encryption key was changed, previously stored credentials cannot be
recovered. Re-enter or rotate them after setting the correct key.

## Diagnostics

Check the API logs while saving and testing credentials. Decryption failures
are logged as:

```text
Failed to decrypt secret <key> for config <id>
```

Also confirm that:

- the provider configuration is enabled and has the expected capability and
  provider ID;
- the credential records are active;
- the API is connected to the intended production database; and
- the production deployment received the latest environment variables.

Never log or commit plaintext provider credentials, JWT secrets, database
passwords, webhook secrets, or encryption keys. Rotate any credentials that
have been exposed in source files, screenshots, logs, or chat.
