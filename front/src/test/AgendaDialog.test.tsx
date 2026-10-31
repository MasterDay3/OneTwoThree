import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { AgendaDialog } from "@/components/AgendaDialog"
import { mockFetch, renderWithQuery, sampleMeeting } from "@/test/utils"
import type { ParsedAgenda } from "@/types"

const PARSER = "https://parser.lambda-url.us-east-1.on.aws/"
const PASTED =
  "Пн 10:00, синк по прайсингу, 45 хв. 1) нові тарифи Pro — Олена, 15 хв 2) фідбек від клієнтів — Taras"

const PARSED: ParsedAgenda = {
  title: "Синк по прайсингу",
  starts_at: "2026-10-19T10:00:00+03:00",
  duration_minutes: 45,
  items: [
    { topic: "Нові тарифи Pro", owner: "Олена", minutes: 15 },
    { topic: "Фідбек від клієнтів", owner: "Taras", minutes: null },
  ],
  action_items: [{ task: "Порівняння з конкурентами", owner: "Taras", due: "2026-10-23" }],
  language: "uk",
}
const PARSE_RESULT = {
  agenda: PARSED,
  model: "anthropic.claude-opus-5-5",
  prompt_version: "agenda-v1",
}

function renderDialog(meeting = sampleMeeting) {
  renderWithQuery(<AgendaDialog meeting={meeting} onOpenChange={() => {}} />)
}

const putCall = (fetchMock: ReturnType<typeof mockFetch>) =>
  fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")

describe("AgendaDialog without a parser", () => {
  it("hides the paste box and saves an agenda typed by hand", async () => {
    const fetchMock = mockFetch(() => ({ body: sampleMeeting }))
    renderDialog()
    expect(screen.queryByLabelText("Paste an agenda")).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "Add topic" }))
    await userEvent.type(screen.getByLabelText("Topic 1"), "Retro")
    await userEvent.type(screen.getByLabelText("Minutes 1"), "20")
    await userEvent.click(screen.getByRole("button", { name: "Save agenda" }))

    await waitFor(() => expect(putCall(fetchMock)).toBeDefined())
    const put = putCall(fetchMock)!
    expect(put[0]).toBe(`/api/meetings/${sampleMeeting.id}/agenda`)
    expect(JSON.parse(String(put[1]?.body))).toEqual({
      items: [{ topic: "Retro", owner: null, minutes: 20 }],
      action_items: [],
      ai_suggestion: null,
    })
  })

  it("validates rows before saving", async () => {
    const fetchMock = mockFetch(() => ({ body: sampleMeeting }))
    renderDialog()
    await userEvent.click(screen.getByRole("button", { name: "Add topic" }))
    await userEvent.type(screen.getByLabelText("Minutes 1"), "0")
    await userEvent.click(screen.getByRole("button", { name: "Save agenda" }))

    expect(await screen.findByText("Topic is required")).toBeInTheDocument()
    expect(screen.getByText("1–480 minutes")).toBeInTheDocument()
    expect(putCall(fetchMock)).toBeUndefined()
  })
})

describe("AgendaDialog with the parser", () => {
  beforeEach(() => vi.stubEnv("VITE_PARSER_URL", PARSER))
  afterEach(() => vi.unstubAllEnvs())

  it("parses, pre-fills, marks suggestions until touched, and saves with the suggestion", async () => {
    const fetchMock = mockFetch((url) =>
      url === PARSER.replace(/\/$/, "") ? { body: PARSE_RESULT } : { body: sampleMeeting },
    )
    renderDialog()

    await userEvent.type(screen.getByLabelText("Paste an agenda"), PASTED)
    await userEvent.click(screen.getByRole("button", { name: "Parse" }))

    expect(await screen.findByDisplayValue("Нові тарифи Pro")).toBeInTheDocument()
    const parseCall = fetchMock.mock.calls.find(([url]) =>
      String(url).startsWith(PARSER.slice(0, 20)),
    )!
    expect(parseCall[1]?.method).toBe("POST")
    const sent = JSON.parse(String(parseCall[1]?.body))
    expect(sent.text).toBe(PASTED)
    expect(sent.today).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(sent.tz).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone)

    // 2 topics (topic+owner, topic+owner+minutes) + 1 action item (task, owner, due); empty stays empty.
    expect(screen.getAllByText("suggested")).toHaveLength(8)
    expect(screen.getByLabelText("Minutes 2")).toHaveValue(null)
    expect(screen.getByText(/The text also says: title “Синк по прайсингу”/)).toBeInTheDocument()

    await userEvent.clear(screen.getByLabelText("Owner 2"))
    await userEvent.type(screen.getByLabelText("Owner 2"), "Тарас")
    expect(screen.getAllByText("suggested")).toHaveLength(7)

    await userEvent.click(screen.getByRole("button", { name: "Save agenda" }))
    await waitFor(() => expect(putCall(fetchMock)).toBeDefined())
    expect(JSON.parse(String(putCall(fetchMock)![1]?.body))).toEqual({
      items: [
        { topic: "Нові тарифи Pro", owner: "Олена", minutes: 15 },
        { topic: "Фідбек від клієнтів", owner: "Тарас", minutes: null },
      ],
      action_items: [{ task: "Порівняння з конкурентами", owner: "Taras", due: "2026-10-23" }],
      ai_suggestion: PARSE_RESULT,
    })
  })

  it.each([
    [422, "Could not find an agenda in this text"],
    [429, "The parser is busy right now"],
    [413, "The text is too long"],
    [401, "Your session has expired"],
    [503, "The parser is not available right now"],
    [500, "Something went wrong"],
  ])("explains a %i from the parser and keeps the form usable", async (status, message) => {
    mockFetch(() => ({ status, body: { error: "x" } }))
    renderDialog()
    await userEvent.type(screen.getByLabelText("Paste an agenda"), "shopping: milk, bread")
    await userEvent.click(screen.getByRole("button", { name: "Parse" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(message)
    expect(screen.getByRole("button", { name: "Add topic" })).toBeEnabled()
  })

  it("explains a network failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"))
    renderDialog()
    await userEvent.type(screen.getByLabelText("Paste an agenda"), "text")
    await userEvent.click(screen.getByRole("button", { name: "Parse" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the parser")
  })

  it("shows progress while waiting and cancels the request", async () => {
    let signal: AbortSignal | undefined
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          signal = init?.signal ?? undefined
          signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")))
        }),
    )
    renderDialog()
    await userEvent.type(screen.getByLabelText("Paste an agenda"), "text")
    await userEvent.click(screen.getByRole("button", { name: "Parse" }))

    expect(await screen.findByRole("status")).toHaveTextContent("Reading the agenda")
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }))

    expect(signal?.aborted).toBe(true)
    expect(screen.getByRole("alert")).toHaveTextContent("Cancelled")
    expect(screen.getByRole("button", { name: "Parse" })).toBeEnabled()
  })

  it("refuses text over 10,000 characters without calling the parser", async () => {
    const fetchMock = mockFetch(() => ({ body: PARSE_RESULT }))
    renderDialog()
    const box = screen.getByLabelText("Paste an agenda")
    await userEvent.click(box)
    await userEvent.paste("x".repeat(10_001))

    expect(screen.getByRole("button", { name: "Parse" })).toBeDisabled()
    expect(screen.getByRole("alert")).toHaveTextContent("The text is too long")
    expect(fetchMock).not.toHaveBeenCalledWith(expect.stringContaining("parser"), expect.anything())
  })

  it("starts from the saved agenda", () => {
    mockFetch(() => ({ body: [] }))
    renderDialog({
      ...sampleMeeting,
      agenda: { items: [{ topic: "Demo", owner: null, minutes: 10 }], action_items: [] },
    })
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByDisplayValue("Demo")).toBeInTheDocument()
    expect(within(dialog).queryByText("suggested")).not.toBeInTheDocument()
  })
})
