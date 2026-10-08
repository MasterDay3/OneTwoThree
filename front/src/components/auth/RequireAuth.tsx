import type { ReactNode } from "react"

import { SiteHeader } from "@/components/SiteHeader"
import { useSession } from "@/hooks/useSession"

/** Renders the page only with a session; otherwise the header with its Sign in button. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { isLoading, isAuthenticated } = useSession()
  if (isAuthenticated) return children
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      {!isLoading && (
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-10 sm:px-6">
          <p className="text-muted-foreground">Sign in to see your meetings.</p>
        </main>
      )}
    </div>
  )
}
