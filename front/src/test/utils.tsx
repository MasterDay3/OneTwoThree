import type { ReactElement } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { vi } from "vitest"

import type { Meeting, User } from "@/types"

export function renderWithQuery(ui: ReactElement, route = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

type Handler = (url: string, init?: RequestInit) => { status?: number; body?: unknown }

export const sampleUser: User = {
  id: "5d1e0c7a-2b3f-4c8d-9e0f-1a2b3c4d5e04",
  email: "anna@example.com",
  name: "Anna Kovalenko",
}

/** Replaces global fetch; returns the mock so tests can inspect calls. `/api/me` always
 * answers with `sampleUser`. */
export function mockFetch(handler: Handler) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input)
    const { status = 200, body } = url === "/api/me" ? { body: sampleUser } : handler(url, init)
    return new Response(status === 204 ? null : JSON.stringify(body ?? null), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  })
}

/** Today at the given local hour, so the sample shows up in the current week and day. */
export function todayAt(hour: number): Date {
  const date = new Date()
  date.setHours(hour, 0, 0, 0)
  return date
}

export const sampleMeeting: Meeting = {
  id: "9a8b7c6d-1e2f-4a3b-9c4d-5e6f7a8b9c03",
  title: "Sprint planning",
  description: "Plan sprint 12 scope",
  call_link: "https://meet.google.com/abc-defg-hij",
  place: "Room 204",
  starts_at: todayAt(10).toISOString(),
  ends_at: todayAt(11).toISOString(),
  owner_id: "5d1e0c7a-2b3f-4c8d-9e0f-1a2b3c4d5e04",
  created_at: "2026-09-21T10:00:00Z",
  participants: [
    { id: "3f1c2a9e-8b4d-4c1e-9a7f-2d5b6e8c1a01", name: "Anna", email: "anna@example.com" },
    { id: "b7e4d2c1-5a6f-4e3b-8c9d-1f2a3b4c5d02", name: "Oleh", email: "oleh@example.com" },
  ],
}
