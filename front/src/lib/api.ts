import { authEnabled, getIdToken, userManager } from "@/lib/auth"
import type {
  Meeting,
  MeetingCreate,
  Participant,
  ParticipantCreate,
  User,
  ValidationIssue,
} from "@/types"

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(message: string, status: number, detail: unknown) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
  }

  get issues(): ValidationIssue[] {
    return Array.isArray(this.detail) ? (this.detail as ValidationIssue[]) : []
  }
}

function messageFrom(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail
  if (Array.isArray(detail)) return detail.map((issue: ValidationIssue) => issue.msg).join("; ")
  return `Request failed (${status})`
}

// Backend origin, set at build time (the Lambda function URL on AWS). Empty = same origin,
// where nginx (Docker Compose) or the Vite dev server proxies /api to the backend.
const API_URL = (import.meta.env.VITE_API_URL ?? "").replace(/\/+$/, "")

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await getIdToken()
  const response = await fetch(`${API_URL}/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  })

  // Session gone or rejected: drop it and go back to the signed-out page.
  if (response.status === 401 && authEnabled()) {
    await userManager().removeUser()
    window.location.assign("/")
  }

  if (!response.ok) {
    let detail: unknown = response.statusText
    try {
      detail = (await response.json()).detail
    } catch {
      // Non-JSON error body: keep the status text.
    }
    throw new ApiError(messageFrom(detail, response.status), response.status, detail)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  getMe: () => request<User>("/me"),

  listMeetings: () => request<Meeting[]>("/meetings"),
  createMeeting: (data: MeetingCreate) =>
    request<Meeting>("/meetings", { method: "POST", body: JSON.stringify(data) }),
  updateMeeting: ({ id, data }: { id: string; data: MeetingCreate }) =>
    request<Meeting>(`/meetings/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteMeeting: (id: string) => request<void>(`/meetings/${id}`, { method: "DELETE" }),

  listParticipants: (q = "") =>
    request<Participant[]>(`/participants${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  createParticipant: (data: ParticipantCreate) =>
    request<Participant>("/participants", { method: "POST", body: JSON.stringify(data) }),
}
