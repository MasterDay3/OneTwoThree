import { useEffect, useRef } from "react"
import { LoaderCircle } from "lucide-react"
import { useAuth } from "react-oidc-context"
import { Navigate } from "react-router"

import { AuthLayout } from "@/components/auth/AuthLayout"
import { useSession } from "@/hooks/useSession"

/** /login/: sends the browser straight to Cognito's managed login (email + password or Google). */
export function LoginPage() {
  const auth = useAuth()
  const { isLoading, isAuthenticated } = useSession()
  // The redirect must start once; StrictMode runs effects twice in development.
  const started = useRef(false)

  useEffect(() => {
    if (isLoading || isAuthenticated || started.current) return
    started.current = true
    void auth.signinRedirect()
  }, [auth, isLoading, isAuthenticated])

  if (isAuthenticated) return <Navigate to="/home" replace />
  return (
    <AuthLayout>
      <p className="flex items-center gap-2 text-muted-foreground">
        <LoaderCircle className="size-5 animate-spin" /> Redirecting to sign-in…
      </p>
    </AuthLayout>
  )
}
