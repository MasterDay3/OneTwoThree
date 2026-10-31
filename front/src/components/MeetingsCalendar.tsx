import { useEffect, useLayoutEffect, useRef, useState } from "react"
import {
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  ListChecks,
  MapPin,
  Pencil,
  Trash2,
  Users,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  type CalendarView,
  type DaySegment,
  formatHour,
  formatLongDay,
  formatPeriod,
  formatTime,
  formatWeekday,
  isSameDay,
  layoutDay,
  minutesSinceMidnight,
  shiftAnchor,
  visibleDays,
} from "@/lib/calendar"
import { cn } from "@/lib/utils"
import type { Meeting } from "@/types"

const HOUR_HEIGHT = 48
const MIN_EVENT_HEIGHT = 22
/** The grid opens scrolled to 07:30; earlier hours stay reachable by scrolling up. */
const FIRST_VISIBLE_HOUR = 7.5
const HOURS = Array.from({ length: 24 }, (_, hour) => hour)
const VIEWS: { value: CalendarView; label: string }[] = [
  { value: "week", label: "Week" },
  { value: "day", label: "Day" },
]

function initialView(): CalendarView {
  // Seven columns do not fit on a phone.
  return window.matchMedia?.("(max-width: 639px)").matches ? "day" : "week"
}

/** Re-renders every minute so the "now" line moves. */
function useNow(): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 60_000)
    return () => clearInterval(timer)
  }, [])
  return now
}

interface MeetingActions {
  onEdit: (meeting: Meeting) => void
  onDelete: (meeting: Meeting) => void
  onAgenda: (meeting: Meeting) => void
}

function MeetingDetails({
  meeting,
  onEdit,
  onDelete,
  onAgenda,
}: { meeting: Meeting } & MeetingActions) {
  const start = new Date(meeting.starts_at)
  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-base leading-snug font-semibold">{meeting.title}</h4>
        <div className="-mt-1 -mr-2 flex shrink-0">
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Agenda of ${meeting.title}`}
            onClick={() => onAgenda(meeting)}
          >
            <ListChecks />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Edit ${meeting.title}`}
            onClick={() => onEdit(meeting)}
          >
            <Pencil />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Delete ${meeting.title}`}
            onClick={() => onDelete(meeting)}
          >
            <Trash2 className="text-destructive" />
          </Button>
        </div>
      </div>
      <p className="flex items-center gap-2 text-muted-foreground">
        <Clock className="size-4 shrink-0" />
        {formatLongDay(start)}, {formatTime(meeting.starts_at)} – {formatTime(meeting.ends_at)}
      </p>
      {meeting.description && <p className="whitespace-pre-line">{meeting.description}</p>}
      {meeting.place && (
        <p className="flex items-center gap-2">
          <MapPin className="size-4 shrink-0 text-muted-foreground" /> {meeting.place}
        </p>
      )}
      {meeting.call_link && (
        <a
          href={meeting.call_link}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 font-medium text-primary underline-offset-4 hover:underline"
        >
          Join call <ExternalLink className="size-3.5" />
        </a>
      )}
      {meeting.agenda && meeting.agenda.items.length > 0 && (
        <ol className="list-decimal space-y-0.5 pl-5" aria-label="Agenda">
          {meeting.agenda.items.map((item, index) => (
            <li key={index}>
              {item.topic}
              {(item.owner || item.minutes) && (
                <span className="text-muted-foreground">
                  {" "}
                  — {[item.owner, item.minutes && `${item.minutes} min`].filter(Boolean).join(", ")}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
      {meeting.participants.length > 0 && (
        <div className="flex items-start gap-2">
          <Users className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="flex flex-wrap gap-1">
            {meeting.participants.map((p) => (
              <Badge key={p.id} variant="secondary" title={p.email}>
                {p.name}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function MeetingBlock({
  segment,
  onEdit,
  onDelete,
  onAgenda,
}: { segment: DaySegment } & MeetingActions) {
  const [open, setOpen] = useState(false)
  const { meeting, startMin, endMin, column, columns } = segment
  // Close the details before handing over to the edit, delete or agenda dialog.
  const closeThen = (action: (meeting: Meeting) => void) => (m: Meeting) => {
    setOpen(false)
    action(m)
  }
  const height = Math.max(((endMin - startMin) / 60) * HOUR_HEIGHT - 2, MIN_EVENT_HEIGHT)
  const compact = height < 40
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="absolute z-10 overflow-hidden rounded-sm border-l-4 border-brand bg-secondary px-1.5 py-0.5 text-left text-xs text-secondary-foreground shadow-xs transition-colors hover:bg-[#ecdcdc] focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none data-[state=open]:bg-[#ecdcdc]"
          style={{
            top: (startMin / 60) * HOUR_HEIGHT + 1,
            height,
            left: `calc(${(column / columns) * 100}% + 2px)`,
            width: `calc(${100 / columns}% - 4px)`,
          }}
        >
          <span className={cn("block truncate font-semibold", compact && "inline")}>
            {meeting.title}
          </span>
          <span className={cn("block truncate", compact && "ml-1 inline")}>
            {formatTime(meeting.starts_at)} – {formatTime(meeting.ends_at)}
          </span>
          {height >= 64 && meeting.place && (
            <span className="block truncate text-muted-foreground">{meeting.place}</span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-80" align="start">
        <MeetingDetails
          meeting={meeting}
          onEdit={closeThen(onEdit)}
          onDelete={closeThen(onDelete)}
          onAgenda={closeThen(onAgenda)}
        />
      </PopoverContent>
    </Popover>
  )
}

interface MeetingsCalendarProps extends MeetingActions {
  meetings: Meeting[]
  /** Called with the start of the empty hour slot the user clicked. */
  onCreateAt: (start: Date) => void
}

export function MeetingsCalendar({
  meetings,
  onEdit,
  onDelete,
  onAgenda,
  onCreateAt,
}: MeetingsCalendarProps) {
  const [view, setView] = useState<CalendarView>(initialView)
  const [anchor, setAnchor] = useState(() => new Date())
  const now = useNow()
  const scrollRef = useRef<HTMLDivElement>(null)
  const days = visibleDays(view, anchor)
  const gridColumns = { gridTemplateColumns: `3.5rem repeat(${days.length}, minmax(0, 1fr))` }

  // Before paint, so the grid never flashes at 00:00.
  useLayoutEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = FIRST_VISIBLE_HOUR * HOUR_HEIGHT
  }, [view])

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={() => setAnchor(new Date())}>
          Today
        </Button>
        <div className="flex">
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Previous ${view}`}
            onClick={() => setAnchor(shiftAnchor(view, anchor, -1))}
          >
            <ChevronLeft />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Next ${view}`}
            onClick={() => setAnchor(shiftAnchor(view, anchor, 1))}
          >
            <ChevronRight />
          </Button>
        </div>
        <h3 className="text-base font-semibold text-foreground sm:text-lg" aria-live="polite">
          {formatPeriod(view, anchor)}
        </h3>
        <div
          role="group"
          aria-label="Calendar view"
          className="ml-auto inline-flex rounded-sm border bg-muted p-0.5"
        >
          {VIEWS.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              aria-pressed={view === value}
              onClick={() => setView(value)}
              className={cn(
                "rounded-[3px] px-3 py-1 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground",
                view === value && "bg-card text-brand shadow-xs",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Fills the window below the header and toolbar, so the page itself barely scrolls. */}
      <div
        ref={scrollRef}
        className="h-[calc(100dvh-10.5rem)] min-h-[420px] overflow-auto rounded-sm border"
      >
        <div className={cn(view === "week" && "min-w-[720px]")}>
          <div className="sticky top-0 z-20 grid border-b bg-card" style={gridColumns}>
            <div />
            {days.map((day) => {
              const today = isSameDay(day, now)
              return (
                <div
                  key={day.toISOString()}
                  className="flex items-center justify-center gap-1.5 border-l py-1.5"
                  aria-current={today ? "date" : undefined}
                >
                  <span
                    className={cn(
                      "text-xs font-medium tracking-wide text-muted-foreground uppercase",
                      today && "text-brand",
                    )}
                  >
                    {formatWeekday(day)}
                  </span>
                  <span
                    className={cn(
                      "flex size-7 items-center justify-center rounded-full text-sm font-medium",
                      today && "bg-brand font-semibold text-white",
                    )}
                  >
                    {day.getDate()}
                  </span>
                </div>
              )
            })}
          </div>

          <div className="grid" style={gridColumns}>
            <div>
              {HOURS.map((hour) => (
                <div key={hour} className="relative" style={{ height: HOUR_HEIGHT }}>
                  {hour > 0 && (
                    <span className="absolute -top-2 right-2 text-[11px] text-muted-foreground">
                      {formatHour(hour)}
                    </span>
                  )}
                </div>
              ))}
            </div>

            {days.map((day) => {
              const today = isSameDay(day, now)
              return (
                <div
                  key={day.toISOString()}
                  role="region"
                  aria-label={formatLongDay(day)}
                  className={cn("relative border-l", today && "bg-accent/40")}
                >
                  {HOURS.map((hour) => {
                    const slot = new Date(day)
                    slot.setHours(hour)
                    return (
                      <button
                        key={hour}
                        type="button"
                        tabIndex={-1}
                        aria-label={`New meeting on ${formatLongDay(day)} at ${formatHour(hour)}`}
                        onClick={() => onCreateAt(slot)}
                        className="block w-full border-b border-border/70 hover:bg-muted/70"
                        style={{ height: HOUR_HEIGHT }}
                      />
                    )
                  })}

                  {layoutDay(meetings, day).map((segment) => (
                    <MeetingBlock
                      key={segment.meeting.id}
                      segment={segment}
                      onEdit={onEdit}
                      onDelete={onDelete}
                      onAgenda={onAgenda}
                    />
                  ))}

                  {today && (
                    <div
                      aria-hidden
                      className="pointer-events-none absolute right-0 left-0 z-10 border-t-2 border-destructive"
                      style={{ top: (minutesSinceMidnight(now) / 60) * HOUR_HEIGHT }}
                    >
                      <span className="absolute -top-[5px] -left-[5px] size-2 rounded-full bg-destructive" />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
