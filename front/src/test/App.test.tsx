import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import App from "@/App"
import { formatLongDay, layoutDay } from "@/lib/calendar"
import { parseNewParticipant } from "@/lib/participants"
import { mockFetch, renderWithQuery, sampleMeeting, todayAt } from "@/test/utils"

describe("Home page", () => {
  it("shows meetings in the week calendar with details on click", async () => {
    mockFetch(() => ({ body: [sampleMeeting] }))
    renderWithQuery(<App />, "/home")

    const today = await screen.findByRole("region", { name: formatLongDay(new Date()) })
    const block = within(today).getByRole("button", { name: /Sprint planning/ })
    expect(block).toHaveTextContent("10:00 – 11:00")
    expect(screen.getAllByRole("region")).toHaveLength(7)

    await userEvent.click(block)
    const details = await screen.findByRole("dialog")
    expect(within(details).getByText("Anna")).toHaveAttribute("data-slot", "badge")
    expect(within(details).getByRole("link", { name: /join call/i })).toHaveAttribute(
      "href",
      sampleMeeting.call_link,
    )
  })

  it("switches between week and day views", async () => {
    mockFetch(() => ({ body: [sampleMeeting] }))
    renderWithQuery(<App />, "/home")

    await screen.findByRole("region", { name: formatLongDay(new Date()) })
    await userEvent.click(screen.getByRole("button", { name: "Day" }))
    expect(screen.getByRole("button", { name: "Day" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getAllByRole("region")).toHaveLength(1)
    expect(screen.getByRole("button", { name: /Sprint planning/ })).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "Next day" }))
    expect(screen.queryByRole("button", { name: /Sprint planning/ })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "Today" }))
    expect(screen.getByRole("button", { name: /Sprint planning/ })).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "Week" }))
    expect(screen.getAllByRole("region")).toHaveLength(7)
  })

  it("opens the form at the clicked time slot", async () => {
    mockFetch(() => ({ body: [] }))
    renderWithQuery(<App />, "/home")

    const label = `New meeting on ${formatLongDay(new Date())} at 14:00`
    await userEvent.click(await screen.findByRole("button", { name: label }))

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByLabelText("Start")).toHaveValue("14:00")
    expect(within(dialog).getByLabelText("End")).toHaveValue("15:00")
  })

  it("edits a meeting from its details", async () => {
    const fetchMock = mockFetch((_url, init) =>
      init?.method === "PUT"
        ? { body: { ...sampleMeeting, title: "Sprint review" } }
        : { body: [sampleMeeting] },
    )
    renderWithQuery(<App />, "/home")

    await userEvent.click(await screen.findByRole("button", { name: /Sprint planning/ }))
    await userEvent.click(await screen.findByRole("button", { name: "Edit Sprint planning" }))

    const dialog = await screen.findByRole("dialog", { name: "Edit meeting" })
    const title = within(dialog).getByLabelText("Title")
    expect(title).toHaveValue("Sprint planning")
    expect(within(dialog).getByLabelText("Start")).toHaveValue("10:00")
    expect(within(dialog).getByLabelText("Place")).toHaveValue("Room 204")

    await userEvent.clear(title)
    await userEvent.type(title, "Sprint review")
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }))

    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")
    expect(put?.[0]).toBe(`/api/meetings/${sampleMeeting.id}`)
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({
      title: "Sprint review",
      description: sampleMeeting.description,
      call_link: sampleMeeting.call_link,
      place: sampleMeeting.place,
      starts_at: sampleMeeting.starts_at,
      ends_at: sampleMeeting.ends_at,
      participant_ids: sampleMeeting.participants.map((p) => p.id),
    })
    await waitFor(() => expect(dialog).not.toBeInTheDocument())
  })

  it("asks for confirmation before deleting", async () => {
    const fetchMock = mockFetch((_url, init) =>
      init?.method === "DELETE" ? { status: 204 } : { body: [sampleMeeting] },
    )
    renderWithQuery(<App />, "/home")

    await userEvent.click(await screen.findByRole("button", { name: /Sprint planning/ }))
    await userEvent.click(await screen.findByRole("button", { name: "Delete Sprint planning" }))

    const dialog = await screen.findByRole("alertdialog")
    expect(dialog).toHaveTextContent("Delete Sprint planning?")
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ method: "DELETE" }),
    )

    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }))
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/meetings/${sampleMeeting.id}`,
      expect.objectContaining({ method: "DELETE" }),
    )
  })
})

describe("layoutDay", () => {
  const meeting = (id: string, from: number, to: number) => ({
    ...sampleMeeting,
    id,
    starts_at: todayAt(from).toISOString(),
    ends_at: todayAt(to).toISOString(),
  })

  it("places overlapping meetings side by side", () => {
    const segments = layoutDay(
      [meeting("a", 9, 11), meeting("b", 10, 12), meeting("c", 13, 14)],
      new Date(),
    )
    expect(segments.map(({ meeting, column, columns }) => [meeting.id, column, columns])).toEqual([
      ["a", 0, 2],
      ["b", 1, 2],
      ["c", 0, 1],
    ])
  })

  it("clips a meeting that runs past midnight", () => {
    const late = { ...meeting("late", 23, 23), ends_at: todayAt(26).toISOString() }
    expect(layoutDay([late], new Date())[0]).toMatchObject({ startMin: 23 * 60, endMin: 24 * 60 })
  })
})

describe("parseNewParticipant", () => {
  it("splits name and email", () => {
    expect(parseNewParticipant("Anna Kovalenko Anna@Example.com")).toEqual({
      name: "Anna Kovalenko",
      email: "anna@example.com",
    })
  })

  it("needs both a name and an email", () => {
    expect(parseNewParticipant("anna@example.com")).toBeNull()
    expect(parseNewParticipant("Anna")).toBeNull()
  })
})
