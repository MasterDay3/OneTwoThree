import { getIdToken } from "@/lib/auth"
import { toDateInput } from "@/lib/calendar"
import type { ParsedAgenda } from "@/types"

/** What the agenda parser (infra/ai.yml) answers with: a suggestion, never a saved agenda. */
export interface ParseResult {
  agenda: ParsedAgenda
  model: string
  prompt_version: string
}

export const MAX_PASTE_LENGTH = 10_000

export type ParseErrorKind =
  "too_long" | "no_agenda" | "busy" | "unavailable" | "signed_out" | "network" | "failed"

const MESSAGES: Record<ParseErrorKind, string> = {
  too_long: `The text is too long: paste at most ${MAX_PASTE_LENGTH.toLocaleString("en")} characters.`,
  no_agenda: "Could not find an agenda in this text. Add the items by hand below.",
  busy: "The parser is busy right now. Try again in a minute, or add the items by hand.",
  unavailable: "The parser is not available right now. Add the items by hand below.",
  signed_out: "Your session has expired. Sign in again to use the parser.",
  network: "Could not reach the parser. Check your connection and try again.",
  failed: "Something went wrong while reading the text. Add the items by hand below.",
}

export class ParseError extends Error {
  readonly kind: ParseErrorKind

  constructor(kind: ParseErrorKind) {
    super(MESSAGES[kind])
    this.name = "ParseError"
    this.kind = kind
  }
}

const KIND_BY_STATUS: Record<number, ParseErrorKind> = {
  401: "signed_out",
  413: "too_long",
  422: "no_agenda",
  429: "busy",
  503: "unavailable",
}

// Read at call time (not module load) so tests can stub it.
export const parserUrl = () => (import.meta.env.VITE_PARSER_URL ?? "").replace(/\/+$/, "")

/**
 * Sends pasted text to the parser with today's date and the user's time zone, so "Monday" and
 * "by Friday" mean the user's week, not the Lambda's (UTC). Aborting `signal` cancels the request.
 */
export async function parseAgenda(text: string, signal?: AbortSignal): Promise<ParseResult> {
  if (text.length > MAX_PASTE_LENGTH) throw new ParseError("too_long")
  const token = await getIdToken()
  let response: Response
  try {
    response = await fetch(parserUrl(), {
      method: "POST",
      signal,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        text,
        today: toDateInput(new Date()),
        tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
    })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new ParseError("network")
  }
  if (!response.ok) throw new ParseError(KIND_BY_STATUS[response.status] ?? "failed")
  return (await response.json()) as ParseResult
}
