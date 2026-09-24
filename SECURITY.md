# Security policy — fans-oppai (oppai.fans)

This service follows the workspace human-authentication policy: root
`SECURITY.md` and `manifest/human-authentication-policy.edn` in
`com-junkawasaki/root` (ADR-2609070400, Web3 first). Its entry there is
`:project "fans-oppai"`, status `:migration-gap`.

## Current state (recorded 2026-09-11)

- **There is no human login and no human session.** No route signs anyone in.
- The age gate is a browser `localStorage` flag. It is a notice, not an
  identity and not an authorization.
- Face-reference enrolment (`/api/face/*`) is a per-browser HttpOnly cookie
  pointing at a KV record with a 7-day TTL. It is a consent ceremony bound to
  one browser: it issues no session and grants nothing beyond the enrolled
  frame, and the UI labels it so.

## Rules for any future login

- Methods are the policy's approved ones only: SIWE (ERC-191 / ERC-1271) first,
  then WebAuthn Passkey. The planned first method is `:siwe-erc191` via
  `kotoba-lang/org-chainagnostic-cacao` (`siwe.core` / `siwe.edge`); neither
  method is built yet.
- Email, password, SMS, OAuth / OIDC / SAML / SSO and operator override are
  never an authority for login, bootstrap, step-up or recovery.
- Server-issued single-use nonce, domain, chain and expiry are verified
  server-side; anything that cannot be verified fails closed.
- Login is not authorization: every operation is checked on its own.
- A new login surface is registered in the policy inventory **before** it is
  deployed.
- A future gallery / credits plane binds the face enrolment to that identity,
  never the reverse.

## Reporting a vulnerability

Report privately to the repository owner through GitHub (a private security
advisory on `network-awai/fans-oppai`). Do not open a public issue for a
vulnerability.
