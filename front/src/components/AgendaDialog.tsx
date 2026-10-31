import { useEffect, useRef, useState } from "react"
import { useFieldArray, useForm, useWatch, type Control, type FieldPath } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2, Plus, Sparkles, X } from "lucide-react"
import { toast } from "sonner"
import { z } from "zod"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Form, FormControl, FormField, FormItem, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { useSaveAgenda } from "@/hooks/useMeetings"
import {
  MAX_PASTE_LENGTH,
  ParseError,
  parseAgenda,
  parserUrl,
  type ParseResult,
} from "@/lib/agendaParser"
import { ApiError } from "@/lib/api"
import type { Agenda, AgendaSave, Meeting, ParsedAgenda } from "@/types"

const minutes = z
  .string()
  .refine(
    (v) => v === "" || (/^\d+$/.test(v) && Number(v) >= 1 && Number(v) <= 480),
    "1–480 minutes",
  )

// `suggested` lists the fields of a row the model filled and the user has not touched yet.
const agendaSchema = z.object({
  items: z
    .array(
      z.object({
        topic: z.string().trim().min(1, "Topic is required").max(500, "Max 500 characters"),
        owner: z.string().trim().max(200, "Max 200 characters"),
        minutes,
        suggested: z.array(z.string()),
      }),
    )
    .max(50, "At most 50 items"),
  action_items: z
    .array(
      z.object({
        task: z.string().trim().min(1, "Task is required").max(500, "Max 500 characters"),
        owner: z.string().trim().max(200, "Max 200 characters"),
        due: z.string(),
        suggested: z.array(z.string()),
      }),
    )
    .max(50, "At most 50 action items"),
})

type AgendaFormValues = z.infer<typeof agendaSchema>

function formValues(agenda: Agenda | null, fromModel = false): AgendaFormValues {
  const filled = (row: object) =>
    fromModel
      ? Object.entries(row)
          .filter(([, v]) => v !== null && v !== "")
          .map(([k]) => k)
      : []
  return {
    items: (agenda?.items ?? []).map((item) => ({
      topic: item.topic,
      owner: item.owner ?? "",
      minutes: item.minutes === null ? "" : String(item.minutes),
      suggested: filled(item),
    })),
    action_items: (agenda?.action_items ?? []).map((action) => ({
      task: action.task,
      owner: action.owner ?? "",
      due: action.due ?? "",
      suggested: filled(action),
    })),
  }
}

function toAgenda(values: AgendaFormValues): Agenda {
  return {
    items: values.items.map((item) => ({
      topic: item.topic.trim(),
      owner: item.owner.trim() || null,
      minutes: item.minutes === "" ? null : Number(item.minutes),
    })),
    action_items: values.action_items.map((action) => ({
      task: action.task.trim(),
      owner: action.owner.trim() || null,
      due: action.due || null,
    })),
  }
}

function SuggestedBadge() {
  return (
    <Badge
      variant="outline"
      className="gap-1 border-amber-300 bg-amber-50 px-1.5 py-0 text-[10px] font-normal text-amber-800"
      title="Filled in by the model from the pasted text: check it"
    >
      <Sparkles className="size-3" /> suggested
    </Badge>
  )
}

type Section = "items" | "action_items"

interface RowFieldProps {
  control: Control<AgendaFormValues>
  section: Section
  index: number
  field: "topic" | "task" | "owner" | "minutes" | "due"
  label: string
  suggested: boolean
  onTouched: () => void
  input?: React.ComponentProps<typeof Input>
}

/** One input of an agenda row; editing it drops its "suggested" badge. */
function RowField({
  control,
  section,
  index,
  field,
  label,
  suggested,
  onTouched,
  input,
}: RowFieldProps) {
  return (
    <FormField
      control={control}
      name={`${section}.${index}.${field}` as FieldPath<AgendaFormValues>}
      render={({ field: f }) => (
        <FormItem className="gap-1">
          <div className="flex h-4 items-center gap-1 text-xs text-muted-foreground">
            <span aria-hidden>{label}</span>
            {suggested && <SuggestedBadge />}
          </div>
          <FormControl>
            <Input
              aria-label={`${label} ${index + 1}`}
              {...input}
              {...f}
              value={f.value as string}
              onChange={(e) => {
                f.onChange(e)
                onTouched()
              }}
            />
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

function formatSuggestedStart(startsAt: string): string {
  const date = new Date(startsAt)
  if (Number.isNaN(date.getTime())) return startsAt
  return date.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })
}

/** The parts of the suggestion that belong to the meeting itself, not its agenda: shown, not saved. */
function MeetingHints({ agenda }: { agenda: ParsedAgenda }) {
  const hints = [
    agenda.title && `title “${agenda.title}”`,
    agenda.starts_at && `starts ${formatSuggestedStart(agenda.starts_at)}`,
    agenda.duration_minutes && `${agenda.duration_minutes} min`,
  ].filter(Boolean)
  return (
    <p className="text-xs text-muted-foreground">
      Language: {agenda.language}
      {hints.length > 0 && <> · The text also says: {hints.join(", ")}</>}
    </p>
  )
}

interface AgendaDialogProps {
  /** Opens the dialog for this meeting; null closes it. */
  meeting: Meeting | null
  onOpenChange: (open: boolean) => void
}

export function AgendaDialog({ meeting, onOpenChange }: AgendaDialogProps) {
  return (
    <Dialog open={meeting !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
        {/* Mounted per opening, so a pasted text or a suggestion never carries over. */}
        {meeting && (
          <AgendaEditor key={meeting.id} meeting={meeting} onClose={() => onOpenChange(false)} />
        )}
      </DialogContent>
    </Dialog>
  )
}

function AgendaEditor({ meeting, onClose }: { meeting: Meeting; onClose: () => void }) {
  const saveAgenda = useSaveAgenda()
  const form = useForm<AgendaFormValues>({
    resolver: zodResolver(agendaSchema),
    defaultValues: formValues(meeting.agenda),
  })
  const items = useFieldArray({ control: form.control, name: "items" })
  const actions = useFieldArray({ control: form.control, name: "action_items" })
  const itemValues = useWatch({ control: form.control, name: "items" })
  const actionValues = useWatch({ control: form.control, name: "action_items" })

  const [text, setText] = useState("")
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  const [suggestion, setSuggestion] = useState<ParseResult | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Closing the dialog cancels a parse that is still running.
  useEffect(() => () => abortRef.current?.abort(), [])

  const untouch = (section: Section, index: number, field: string) => () => {
    const path = `${section}.${index}.suggested` as const
    const current = form.getValues(path)
    if (current.includes(field))
      form.setValue(
        path,
        current.filter((f) => f !== field),
      )
  }

  const parse = async () => {
    const controller = new AbortController()
    abortRef.current = controller
    setParsing(true)
    setParseError(null)
    try {
      const result = await parseAgenda(text, controller.signal)
      form.reset(formValues(result.agenda, true))
      setSuggestion(result)
    } catch (error) {
      if (controller.signal.aborted) return
      setParseError(error instanceof ParseError ? error.message : "Could not read the text.")
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
        setParsing(false)
      }
    }
  }

  const cancelParse = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setParsing(false)
    setParseError("Cancelled. Nothing was changed.")
  }

  const onSubmit = async (values: AgendaFormValues) => {
    const data: AgendaSave = {
      ...toAgenda(values),
      // What the model suggested, kept next to what the user saved: every correction is feedback.
      ai_suggestion: suggestion
        ? {
            agenda: suggestion.agenda,
            model: suggestion.model,
            prompt_version: suggestion.prompt_version,
          }
        : null,
    }
    try {
      await saveAgenda.mutateAsync({ id: meeting.id, data })
      toast.success("Agenda saved")
      onClose()
    } catch (error) {
      toast.error(
        error instanceof ApiError && error.status === 422
          ? `Could not save the agenda: ${error.message}`
          : error instanceof Error
            ? error.message
            : "Could not save the agenda",
      )
    }
  }

  const tooLong = text.length > MAX_PASTE_LENGTH
  const showParser = parserUrl() !== ""

  return (
    <>
      <DialogHeader>
        <DialogTitle>Agenda: {meeting.title}</DialogTitle>
        <DialogDescription>
          {showParser
            ? "Paste an email or notes to fill the agenda in for you, then check it and save."
            : "Add the topics and the action items for this meeting."}
        </DialogDescription>
      </DialogHeader>

      {showParser && (
        <section className="space-y-2 rounded-sm border bg-muted/40 p-3">
          <Label htmlFor="agenda-paste">Paste an agenda</Label>
          <Textarea
            id="agenda-paste"
            rows={4}
            placeholder="Mon 10:00, pricing sync, 45 min. 1) new Pro plans — Olena, 15 min 2) …"
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={parsing}
          />
          <div className="flex flex-wrap items-center gap-2">
            {parsing ? (
              <>
                <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Reading the agenda… this usually takes a few seconds.
                </p>
                <Button type="button" variant="outline" size="sm" onClick={cancelParse}>
                  Cancel
                </Button>
              </>
            ) : (
              <Button
                type="button"
                size="sm"
                onClick={parse}
                disabled={text.trim() === "" || tooLong}
              >
                <Sparkles /> Parse
              </Button>
            )}
            <span
              className={`ml-auto text-xs ${tooLong ? "text-destructive" : "text-muted-foreground"}`}
            >
              {text.length.toLocaleString("en")} / {MAX_PASTE_LENGTH.toLocaleString("en")}
            </span>
          </div>
          {tooLong && (
            <p role="alert" className="text-sm text-destructive">
              {new ParseError("too_long").message}
            </p>
          )}
          {parseError && (
            <p role="alert" className="text-sm text-destructive">
              {parseError}
            </p>
          )}
          {suggestion && !parsing && <MeetingHints agenda={suggestion.agenda} />}
        </section>
      )}

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-5" noValidate>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Topics</legend>
            {items.fields.length === 0 && (
              <p className="text-sm text-muted-foreground">No topics yet.</p>
            )}
            {items.fields.map((row, index) => {
              const suggested = itemValues?.[index]?.suggested ?? []
              return (
                <div
                  key={row.id}
                  className="grid grid-cols-[1fr_auto] gap-2 sm:grid-cols-[2fr_1fr_6rem_auto]"
                >
                  <RowField
                    control={form.control}
                    section="items"
                    index={index}
                    field="topic"
                    label="Topic"
                    suggested={suggested.includes("topic")}
                    onTouched={untouch("items", index, "topic")}
                  />
                  <RowField
                    control={form.control}
                    section="items"
                    index={index}
                    field="owner"
                    label="Owner"
                    suggested={suggested.includes("owner")}
                    onTouched={untouch("items", index, "owner")}
                  />
                  <RowField
                    control={form.control}
                    section="items"
                    index={index}
                    field="minutes"
                    label="Minutes"
                    suggested={suggested.includes("minutes")}
                    onTouched={untouch("items", index, "minutes")}
                    input={{ type: "number", min: 1, max: 480, inputMode: "numeric" }}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="mt-5"
                    aria-label={`Remove topic ${index + 1}`}
                    onClick={() => items.remove(index)}
                  >
                    <X />
                  </Button>
                </div>
              )
            })}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => items.append({ topic: "", owner: "", minutes: "", suggested: [] })}
            >
              <Plus /> Add topic
            </Button>
          </fieldset>

          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Action items</legend>
            {actions.fields.length === 0 && (
              <p className="text-sm text-muted-foreground">No action items yet.</p>
            )}
            {actions.fields.map((row, index) => {
              const suggested = actionValues?.[index]?.suggested ?? []
              return (
                <div
                  key={row.id}
                  className="grid grid-cols-[1fr_auto] gap-2 sm:grid-cols-[2fr_1fr_9rem_auto]"
                >
                  <RowField
                    control={form.control}
                    section="action_items"
                    index={index}
                    field="task"
                    label="Task"
                    suggested={suggested.includes("task")}
                    onTouched={untouch("action_items", index, "task")}
                  />
                  <RowField
                    control={form.control}
                    section="action_items"
                    index={index}
                    field="owner"
                    label="Owner"
                    suggested={suggested.includes("owner")}
                    onTouched={untouch("action_items", index, "owner")}
                  />
                  <RowField
                    control={form.control}
                    section="action_items"
                    index={index}
                    field="due"
                    label="Due"
                    suggested={suggested.includes("due")}
                    onTouched={untouch("action_items", index, "due")}
                    input={{ type: "date" }}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="mt-5"
                    aria-label={`Remove action item ${index + 1}`}
                    onClick={() => actions.remove(index)}
                  >
                    <X />
                  </Button>
                </div>
              )
            })}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => actions.append({ task: "", owner: "", due: "", suggested: [] })}
            >
              <Plus /> Add action item
            </Button>
          </fieldset>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Close
            </Button>
            <Button type="submit" disabled={saveAgenda.isPending || parsing}>
              {saveAgenda.isPending ? "Saving…" : "Save agenda"}
            </Button>
          </DialogFooter>
        </form>
      </Form>
    </>
  )
}
