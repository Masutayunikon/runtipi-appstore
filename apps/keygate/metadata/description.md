# Keygate

Open source software license management platform — a self-hosted alternative to Keygen, Cryptlex and LicenseSpring.

## Features

- Subscriptions, perpetual licenses, trials and floating licenses
- Machine activations with a self-service customer portal
- Offline-verifiable signed license tokens (Ed25519)
- Stripe billing end to end: the customer pays, the license is created automatically
- Usage metering, team seats and entitlements
- Server-to-server admin API and HMAC-signed webhooks

## Before you start

- **Expose the app on a public HTTPS domain.** Stripe must be able to reach the webhook, and login sessions need HTTPS.
- **Admin emails**: only these addresses can complete the first-run setup.
- **Without SMTP**, the login codes are printed in the app logs (Runtipi → Keygate → Logs).
- The database password, JWT secret, license signing key and encryption key are generated automatically. Back up the app's `app-data` folder: losing the license signing key invalidates every license already issued.

## License

AGPL v3. The "Powered by Keygate" attribution must stay in the UI.
