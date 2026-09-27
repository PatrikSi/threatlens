import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { resolveApiErrorMessage } from "../api/errors";
import { captureSessionLease } from "../api/sessionLifecycle";
import { useUnsavedChangesWarning } from "../hooks/useUnsavedChangesWarning";
import { TEAM_BUTTON } from "./teamPresentation";
import type { HuntEntry } from "./teamHuntTypes";

type Schedule = NonNullable<HuntEntry["review_schedule"]>;
const empty: Schedule = {
  version: 0,
  priority: "normal",
  due_at: null,
  overdue: false,
  reminded_at: null,
  reminder_acknowledged_at: null,
};
function localTime(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}
export function HuntReviewSchedule({
  entry,
  disabled,
  onDirtyChange,
  paused = false,
}: {
  entry: HuntEntry;
  disabled: boolean;
  paused?: boolean;
  onDirtyChange?: (entry: HuntEntry, dirty: boolean) => void;
}) {
  const client = useQueryClient();
  const current = entry.review_schedule ?? empty;
  const [baseline, setBaseline] = useState(current);
  const [assessmentVersion, setAssessmentVersion] = useState(
    entry.assessment_version,
  );
  const pending = useRef(false);
  const [priority, setPriority] = useState(current.priority);
  const [deadline, setDeadline] = useState(() => localTime(current.due_at));
  const [notice, setNotice] = useState("");
  const save = useMutation({
    mutationFn: async ({
      acknowledge,
      version,
      assessment,
      submittedPriority,
      submittedDeadline,
    }: {
      acknowledge: boolean;
      version: number;
      assessment: number;
      submittedPriority: Schedule["priority"];
      submittedDeadline: string;
    }) => {
      const lease = captureSessionLease();
      const result = await apiFetch<Schedule>(
        `/teams/${entry.team_id}/hunts/${entry.assessment_id}/${entry.hunt.id}/${acknowledge ? "reminder-acknowledgement" : "schedule"}`,
        {
          method: acknowledge ? "POST" : "PATCH",
          body: JSON.stringify({
            expected_version: version,
            expected_assessment_version: assessment,
            ...(acknowledge
              ? {}
              : {
                  priority: submittedPriority,
                  due_at: submittedDeadline
                    ? new Date(submittedDeadline).toISOString()
                    : null,
                }),
          }),
        },
      );
      lease.assertCurrent();
      return { result, acknowledge, assessment };
    },
    onSuccess: ({ result, acknowledge, assessment }) => {
      if (!acknowledge) {
        setBaseline(result);
        setAssessmentVersion(assessment);
        setPriority(result.priority);
        setDeadline(localTime(result.due_at));
      }
      setNotice(
        acknowledge
          ? "Review reminder acknowledged."
          : "Review schedule updated.",
      );
      void client.invalidateQueries({
        queryKey: ["team-hunts", entry.team_id],
      });
    },
    onSettled: () => {
      pending.current = false;
    },
  });
  const dirty = priority !== baseline.priority || deadline !== localTime(baseline.due_at);
  const confirmDiscard = useUnsavedChangesWarning(
    dirty || save.isPending,
    "You have an unsaved hunt review schedule. Leave without saving?",
  );
  useEffect(() => {
    onDirtyChange?.(entry, dirty || save.isPending);
  }, [entry, dirty, save.isPending, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(entry, false), [entry, onDirtyChange]);
  const submit = (acknowledge: boolean) => {
    if (pending.current || disabled || paused || !entry.can_schedule) return;
    pending.current = true;
    save.mutate({
      acknowledge,
      version: acknowledge ? current.version : baseline.version,
      assessment: acknowledge ? entry.assessment_version : assessmentVersion,
      submittedPriority: priority,
      submittedDeadline: deadline,
    });
  };
  return (
    <div className="space-y-2 text-sm">
      {confirmDiscard.discardDialog}
      <p>
        Review priority: {current.priority} · Deadline:{" "}
        {current.due_at ? new Date(current.due_at).toLocaleString() : "Not set"}
        {current.overdue ? " · Overdue" : ""}
      </p>
      {current.reminded_at && !current.reminder_acknowledged_at && (
        <p role="status">
          Review reminder: this suggestion is overdue.{" "}
          {entry.can_schedule && (
            <button
              className={TEAM_BUTTON}
              disabled={disabled || paused || save.isPending}
              onClick={() => submit(true)}
            >
              Acknowledge review reminder
            </button>
          )}
        </p>
      )}
      {save.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            save.error,
            "Review schedule could not be saved. Refresh the queue before retrying.",
          )}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {entry.can_schedule && (
        <details>
          <summary className="cursor-pointer">
            Set review priority and deadline
          </summary>
          {(baseline.version !== current.version ||
            assessmentVersion !== entry.assessment_version) && (
            <p role="status">
              The schedule changed. Reload before saving your draft.
            </p>
          )}
          <fieldset
            className="mt-2 flex flex-wrap items-end gap-3"
            disabled={disabled || save.isPending}
          >
            <label>
              Review priority
              <select
                className="ml-2 rounded border p-2 dark:bg-[#072019]"
                value={priority}
                onChange={(event) =>
                  setPriority(event.target.value as Schedule["priority"])
                }
              >
                {["low", "normal", "high", "urgent"].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Review deadline (local time)
              <input
                type="datetime-local"
                min="2000-01-01T00:00"
                max="2100-12-31T23:59"
                className="ml-2 rounded border p-2 dark:bg-[#072019]"
                value={deadline}
                onChange={(event) => setDeadline(event.target.value)}
              />
            </label>
            <button className={TEAM_BUTTON} disabled={paused} onClick={() => submit(false)}>
              Save review schedule
            </button>
            <button
              className={TEAM_BUTTON}
              onClick={() => confirmDiscard(() => {
                setBaseline(current);
                setAssessmentVersion(entry.assessment_version);
                setPriority(current.priority);
                setDeadline(localTime(current.due_at));
                save.reset();
              })}
            >
              Reload review schedule
            </button>
          </fieldset>
        </details>
      )}
    </div>
  );
}
