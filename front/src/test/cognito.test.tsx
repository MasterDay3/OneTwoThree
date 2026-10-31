import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { User, UserManager } from "oidc-client-ts"
import { beforeEach, describe, expect, it, vi } from "vitest"

import App from "@/App"
import { api } from "@/lib/api"
import { logoutUrl, userManager } from "@/lib/auth"
import { mockFetch, renderWithQuery } from "@/test/utils"

const POOL = "us-east-1_abc123"
const CLIENT = "client-123"
const DOMAIN = "meetings.auth.us-east-1.amazoncognito.com"

/** A signed-in user in localStorage, as oidc-client-ts stores it after the code exchange. */
function storeUser() {
  const user = new User({
    id_token: "id-token",
    access_token: "access-token",
    refresh_token: "refresh-token",
    token_type: "Bearer",
    scope: "openid email profile",
    profile: {
      sub: "sub-1",
      iss: `https://cognito-idp.us-east-1.amazonaws.com/${POOL}`,
      aud: CLIENT,
      exp: 0,
      iat: 0,
      email: "anna@example.com",
    },
    expires_at: Math.floor(Date.now() / 1000) + 3600,
  })
  localStorage.setItem(
    `oidc.user:https://cognito-idp.us-east-1.amazonaws.com/${POOL}:${CLIENT}`,
    user.toStorageString(),
  )
}

describe("with Cognito configured", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_COGNITO_REGION", "us-east-1")
    vi.stubEnv("VITE_COGNITO_USER_POOL_ID", POOL)
    vi.stubEnv("VITE_COGNITO_CLIENT_ID", CLIENT)
    vi.stubEnv("VITE_COGNITO_DOMAIN", DOMAIN)
  })

  it("configures the OIDC client for the user pool", () => {
    const { settings } = userManager()
    expect(settings.authority).toBe(`https://cognito-idp.us-east-1.amazonaws.com/${POOL}`)
    expect(settings.client_id).toBe(CLIENT)
    expect(settings.redirect_uri).toBe(`${window.location.origin}/auth/callback`)
    expect(settings.scope).toBe("openid email profile")
    expect(settings.response_type).toBe("code")
  })

  it("shows a Sign in button in the header when signed out", async () => {
    mockFetch(() => ({ body: [] }))
    renderWithQuery(<App />, "/home")
    expect(await screen.findByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login")
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument()
  })

  it("starts the redirect to Cognito as soon as /login/ loads", async () => {
    const redirect = vi.spyOn(UserManager.prototype, "signinRedirect").mockResolvedValue()
    mockFetch(() => ({ body: [] }))
    renderWithQuery(<App />, "/login/")
    await waitFor(() => expect(redirect).toHaveBeenCalledTimes(1))
  })

  it("shows the signed-in email and sends the ID token to the API", async () => {
    storeUser()
    const fetchMock = mockFetch(() => ({ body: [] }))
    renderWithQuery(<App />, "/home")
    expect(await screen.findByText("anna@example.com")).toBeInTheDocument()

    await api.listMeetings()
    const apiCall = fetchMock.mock.calls.find(([url]) => url === "/api/meetings")
    expect((apiCall?.[1]?.headers as Record<string, string>).Authorization).toBe("Bearer id-token")
  })

  it("signs out locally, then through Cognito's logout endpoint", async () => {
    storeUser()
    const remove = vi.spyOn(UserManager.prototype, "removeUser")
    mockFetch(() => ({ body: [] }))
    renderWithQuery(<App />, "/home")
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }))
    await waitFor(() => expect(remove).toHaveBeenCalled())
    expect(await screen.findByRole("link", { name: "Sign in" })).toBeInTheDocument()
    expect(logoutUrl()).toBe(
      `https://${DOMAIN}/logout?client_id=${CLIENT}&logout_uri=${encodeURIComponent(`${window.location.origin}/`)}`,
    )
  })

  it("shows an error when the callback has no code", async () => {
    mockFetch(() => ({ body: [] }))
    renderWithQuery(<App />, "/auth/callback")
    expect(await screen.findByRole("alert")).toHaveTextContent("Sign-in failed")
  })
})
