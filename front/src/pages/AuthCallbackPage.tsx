import { LoaderCircle } from "lucide-react"
import { useAuth } from "react-oidc-context"
import { Link, Navigate } from "react-router"

import { AuthLayout } from "@/components/auth/AuthLayout"

/** Where Cognito sends the browser back with ?code=; the AuthProvider exchanges it for tokens. */
export function AuthCallbackPage() {
  const auth = useAuth()

  if (auth.isAuthenticated) return <Navigate to="/home" replace />
  const error = auth.error?.message ?? (auth.isLoading ? null : "Sign-in was cancelled")

  return (
    <AuthLayout>
      {error ? (
        <div role="alert">
          <h1 className="text-2xl font-semibold text-foreground">Sign-in failed</h1>
          <p className="mt-2 text-sm text-muted-foreground">{error}</p>
          <Link
            to="/login"
            className="mt-6 inline-block font-medium text-primary underline-offset-4 hover:underline"
          >
            Try again
          </Link>
        </div>
      ) : (
        <p className="flex items-center gap-2 text-muted-foreground">
          <LoaderCircle className="size-5 animate-spin" /> Signing you in…
        </p>
      )}
    </AuthLayout>
  )
}
