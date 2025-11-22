# Secrets handling

- Encrypt `.env` into `ops/secrets/.env.enc` using sops/age.
- Example age public key is in `ops/.sops.yaml`; replace with your own.
- Decrypt on deploy:

```bash
sops -d ops/secrets/.env.enc > .env
```

- Never commit decrypted `.env`.
