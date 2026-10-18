export interface Participant {
  id: string
  name: string
  email: string
}

export interface User {
  id: string
  email: string
  name: string | null
}

export interface Meeting {
  id: string
  title: string
  description: string | null
  call_link: string | null
  place: string | null
  starts_at: string
  ends_at: string
  owner_id: string | null
  participants: Participant[]
  agenda: Agenda | null
  created_at: string
}

export interface AgendaItem {
  topic: string
  owner: string | null
  minutes: number | null
}

export interface ActionItem {
  task: string
  owner: string | null
  /** ISO date, e.g. 2026-10-23. */
  due: string | null
}

export interface Agenda {
  items: AgendaItem[]
  action_items: ActionItem[]
}

/** What the agenda parser returns (back/app/ai/agenda.py `ParsedAgenda`). */
export interface ParsedAgenda extends Agenda {
  title: string | null
  starts_at: string | null
  duration_minutes: number | null
  language: string
}

export interface AiSuggestion {
  agenda: ParsedAgenda
  model: string
  prompt_version: string
}

export interface AgendaSave extends Agenda {
  ai_suggestion: AiSuggestion | null
}

export interface MeetingCreate {
  title: string
  description: string | null
  call_link: string | null
  place: string | null
  starts_at: string
  ends_at: string
  participant_ids: string[]
}

export interface ParticipantCreate {
  name: string
  email: string
}

/** One entry of FastAPI's 422 `detail` list. */
export interface ValidationIssue {
  loc: (string | number)[]
  msg: string
  type: string
}
