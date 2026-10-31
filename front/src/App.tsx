import { AuthProvider } from "react-oidc-context"
import { Navigate, Route, Routes } from "react-router"

import { RequireAuth } from "@/components/auth/RequireAuth"
import { userManager } from "@/lib/auth"
import { AuthCallbackPage } from "@/pages/AuthCallbackPage"
import { HomePage } from "@/pages/HomePage"
import { LoginPage } from "@/pages/LoginPage"

// After the code exchange, drop ?code=&state= from the address bar.
const removeAuthParams = () =>
  window.history.replaceState({}, document.title, window.location.pathname)

export default function App() {
  return (
    <AuthProvider userManager={userManager()} onSigninCallback={removeAuthParams}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/auth/callback" element={<AuthCallbackPage />} />
        <Route
          path="/home"
          element={
            <RequireAuth>
              <HomePage />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/home" replace />} />
      </Routes>
    </AuthProvider>
  )
}
