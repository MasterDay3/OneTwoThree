/**
 * Sign-in with Amazon Cognito's managed login through OpenID Connect (react-oidc-context on top of
 * oidc-client-ts): authorization code flow with PKCE. Email + password and Google users both come
 * back with Cognito tokens; the ID token is sent to our API, which verifies it.
 *
 * Without VITE_COGNITO_CLIENT_ID (local development without AWS) auth is disabled: the app acts
 * as signed in and the backend treats every request as one local user.
 */
import { UserManager, WebStorageStateStore } from "oidc-client-ts"

export interface AuthConfig {
  region: string
  userPoolId: string
  clientId: string
  domain: string
}

export function authConfig(): AuthConfig {
  const env = import.meta.env
  return {
    region: env.VITE_COGNITO_REGION || "us-east-1",
    userPoolId: env.VITE_COGNITO_USER_POOL_ID ?? "",
    clientId: env.VITE_COGNITO_CLIENT_ID ?? "",
    domain: env.VITE_COGNITO_DOMAIN ?? "",
  }
}

export const authEnabled = () => Boolean(authConfig().clientId)

export const callbackUrl = () => `${window.location.origin}/auth/callback`

let manager: UserManager | undefined

/** The one UserManager shared by the React provider and the API client. */
export function userManager(): UserManager {
  const { region, userPoolId, clientId } = authConfig()
  const authority = `https://cognito-idp.${region}.amazonaws.com/${userPoolId}`
  if (manager?.settings.authority !== authority || manager.settings.client_id !== clientId) {
    manager = new UserManager({
      authority,
      client_id: clientId,
      redirect_uri: callbackUrl(),
      response_type: "code",
      scope: "openid email profile",
      // localStorage: a reload or a new tab stays signed in.
      userStore: new WebStorageStateStore({ store: window.localStorage }),
    })
  }
  return manager
}

/** A valid ID token for the API, refreshed when expired; null when signed out. */
export async function getIdToken(): Promise<string | null> {
  if (!authEnabled()) return null
  let user = await userManager().getUser()
  if (user?.expired && user.refresh_token)
    user = await userManager()
      .signinSilent()
      .catch(() => null)
  return user && !user.expired ? (user.id_token ?? null) : null
}

/** Cognito has no OIDC end-session endpoint: its own /logout ends the managed login session. */
export function logoutUrl(): string {
  const { domain, clientId } = authConfig()
  const params = new URLSearchParams({
    client_id: clientId,
    logout_uri: `${window.location.origin}/`,
  })
  return `https://${domain}/logout?${params}`
}

/** Clears the local session, then sends the browser to Cognito's logout. */
export async function signOut(): Promise<void> {
  if (!authEnabled()) return
  await userManager().removeUser()
  window.location.assign(logoutUrl())
}
