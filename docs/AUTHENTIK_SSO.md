# Authentik owner sign-in

Tauric can authenticate its existing owner using an explicitly configured OpenID Connect
provider. Password sign-in remains available. The SSO identity is bound to one exact
issuer and stable subject; email and username are not used to grant owner access. There
is no public account registration or automatic user provisioning.

This document describes the implementation and installation procedure. It does not by
itself establish that a deployment or live browser sign-in has been verified.

## Provider and host configuration

Create a dedicated Authentik OAuth2/OpenID provider and application. Use a public client
with the authorization-code grant, PKCE S256, an asymmetric RS256 signing key, the
`openid` scope, a per-provider issuer, a stable user UUID subject, and exactly the
workstation's `/api/v1/sso/callback` as the allowed redirect URI. Restrict the
application to the intended Authentik owner. Its dashboard launch URL may point to
`https://<workstation>/api/v1/sso/start`.

After a verified backup and checked source-bound release, configure the following
values in the private server environment. The subject must be the intended user's
stable Authentik UUID, not their email address. Keep the environment file private.

```text
TAURIC_SSO_ISSUER=https://<authentik-host>/application/o/<application-slug>/
TAURIC_SSO_CLIENT_ID=<public-client-id>
TAURIC_SSO_SUBJECT=<owner-user-uuid>
TAURIC_SSO_CALLBACK_URI=https://<workstation>/api/v1/sso/callback
```

All four values must be supplied together. Tauric requires HTTPS, secure cookies, and
the exact existing workstation origin. It validates provider metadata and requires the
provider's authorization, token and signing-key endpoints to use the configured SSO
host over HTTPS. The identity token must have a valid RS256 signature, expected issuer,
audience, nonce, expiry and owner subject. Sign-in attempts use a short-lived encrypted,
browser-bound cookie, one-time authorization code and PKCE. Provider tokens are not
stored. The existing credential-key file encrypts the temporary cookie and supports its
normal key rotation.

The API and engineering images install the separately pinned SSO dependency; the
research runtime's dependency lock is unchanged. Reuse the normal release process,
which verifies the source and images, backs up and restores the existing state, retains
financial policy and owner pauses, and verifies service readiness. Do not print client
credentials, temporary authorization values, identity tokens or cookies.

## Verification and recovery

Verify that the Authentik dashboard application starts the actual Tauric sign-in flow,
the intended SSO identity creates the existing owner session, a different identity is
rejected, unauthenticated API access remains rejected, and the existing password login
continues to work. Check readiness and the original service set after installation.
Document the checked source revision and the actual browser result separately.

Signing out of Authentik does not revoke a Tauric session already issued; Tauric's
normal session expiry and sign-out apply. To disable new SSO sign-ins, remove all four
configuration values through the controlled deployment process and disable the
Authentik application. Existing sessions need explicit revocation if immediate
invalidation is required. Preserve the owner password as a recovery path.
