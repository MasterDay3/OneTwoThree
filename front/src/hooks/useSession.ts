import { useAuth } from "react-oidc-context"

import { authEnabled } from "@/lib/auth"

/** Who is signed in; with auth disabled (local development) the app always acts as signed in. */
export function useSession() {
  const auth = useAuth()
  if (!authEnabled()) return { isLoading: false, isAuthenticated: true, email: undefined }
  return {
    isLoading: auth.isLoading,
    isAuthenticated: auth.isAuthenticated,
    email: auth.user?.profile.email,
  }
}
