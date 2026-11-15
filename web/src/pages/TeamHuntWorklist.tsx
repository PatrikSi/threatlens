import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { apiFetch } from "../api/client";
import { resolveApiErrorMessage } from "../api/errors";
import { captureSessionLease } from "../api/sessionLifecycle";
import { DialogSurface } from "../components/ConfirmDialog";
import type { HuntEntry } from "./teamHuntTypes";
import { HuntReviewSchedule } from "./HuntReviewSchedule";
import { TeamHuntSavedViews, type HuntFilters } from "./TeamHuntSavedViews";
import { AssessmentWorkspace } from "./ArticleTeamAssessment";
import { useQueryAccessScope } from "../hooks/useQueryAccessScope";
import { TEAM_BUTTON } from "./teamPresentation";

interface HuntPage {
  items: HuntEntry[];
  next_cursor: string | null;
  has_more: boolean;
  limit: number;
}
type Command = { kind: "claim" | "unclaim"; entry: HuntEntry };

export function TeamHuntWorklist({
  teamId,
  writable,
  canInvestigate,
  unavailable,
}: {
  teamId: string;
  writable: boolean;
  canInvestigate: boolean;
  unavailable: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const access = useQueryAccessScope(["team-hunts", teamId]);
  const readable = useRef<{ generation: number; entries: HuntEntry[] }>({ generation: access.generation, entries: [] });
  const location = useLocation();
  // Polling may remove a row while it is being edited. Keep that editor mounted
  // until the analyst saves/discards it; explicit URL transitions are guarded.
  const [retained, setRetained] = useState<{ scope: string; entries: Record<string, { entry: HuntEntry; generation: number }> }>({ scope: location.key, entries: {} });
  const retainDraft = useCallback((entry: HuntEntry, dirty: boolean) => {
    const key = `${entry.assessment_id}:${entry.hunt.id}`;
    setRetained((current) => {
      const entries = current.scope === location.key ? current.entries : {};
      if (!dirty && !entries[key]) return current;
      if (dirty && entries[key]?.entry === entry) return current;
      if (dirty && !readable.current.entries.some((current) => current.assessment_id === entry.assessment_id && current.hunt.id === entry.hunt.id)) return current;
      const next = { ...entries };
      if (dirty) next[key] = { entry, generation: readable.current.generation };
      else delete next[key];
      return { scope: location.key, entries: next };
    });
  }, [location.key]);
  const client = useQueryClient();
  const [reviewIntent, setReview] = useState<{ entry: HuntEntry; admission: { generation: number; value: boolean } } | null>(null);
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(
    null,
  );
  const pending = useRef(false);
  const status = ["pending", "stale", "accepted", "rejected"].includes(
    params.get("hunt_status") ?? "",
  )
    ? params.get("hunt_status")!
    : "";
  const ownership = ["mine", "unclaimed"].includes(
    params.get("hunt_owner") ?? "",
  )
    ? params.get("hunt_owner")!
    : "all";
  const order = ["newest", "oldest", "due"].includes(
    params.get("hunt_order") ?? "",
  )
    ? params.get("hunt_order")!
    : "oldest";
  const priority = ["low", "normal", "high", "urgent"].includes(
    params.get("hunt_priority") ?? "",
  )
    ? params.get("hunt_priority")!
    : "";
  const overdue = params.get("hunt_overdue") === "true";
  const cursor = params.get("hunt_cursor") ?? "";
  const queryKey = [
      "team-hunts",
      teamId,
      status,
      ownership,
      cursor,
      order,
      priority,
      overdue,
    ];
  const query = useQuery({
    queryKey,
    enabled: !unavailable,
    ...access.queryOptions(({ signal }) =>
      apiFetch<HuntPage>(
        `/teams/${teamId}/hunts?${new URLSearchParams({ limit: "25", ownership, order, ...(priority ? { priority } : {}), ...(overdue ? { overdue: "true" } : {}), ...(status ? { status } : {}), ...(cursor ? { cursor } : {}) })}`,
        { signal },
      )),
    refetchInterval: 30_000,
  });
  useEffect(() => {
    if (!query.data) return;
    const freshEntries = query.data.items;
    setRetained((current) => {
      if (current.scope !== location.key) return current;
      let changed = false;
      const entries = { ...current.entries };
      for (const [key, row] of Object.entries(entries)) {
        const fresh = freshEntries.find((entry) => entry.assessment_id === row.entry.assessment_id && entry.hunt.id === row.entry.hunt.id);
        if (fresh && (fresh !== row.entry || row.generation !== access.generation)) {
          entries[key] = { entry: fresh, generation: access.generation };
          changed = true;
        }
      }
      return changed ? { ...current, entries } : current;
    });
  }, [access.generation, location.key, query.data]);
  const lostAccess = access.denied;
  const blocked = unavailable || query.isError || access.denied;
  const visibleEntries = query.data?.items ?? [];
  readable.current = { generation: access.generation, entries: visibleEntries };
  const retainedRows = Object.values(retained.scope === location.key ? retained.entries : {}).filter(
    ({ entry }) => !visibleEntries.some((visible) => visible.assessment_id === entry.assessment_id && visible.hunt.id === entry.hunt.id),
  );
  const retainedEntries = retainedRows.map(({ entry }) => entry);
  const visibleRetainedEntries = retainedRows.filter((row) => row.generation === access.generation && !lostAccess).map(({ entry }) => entry);
  const review = reviewIntent && access.currentData(reviewIntent.admission) && visibleEntries.find((entry) => entry.assessment_id === reviewIntent.entry.assessment_id && entry.hunt.id === reviewIntent.entry.hunt.id) || null;
  const currentEntry = (entry: HuntEntry) => {
    const state = client.getQueryState(queryKey);
    const current = state?.status === 'error' ? undefined : access.currentData<HuntPage>(client.getQueryData(queryKey));
    return current?.items.find((row) => row.assessment_id === entry.assessment_id && row.hunt.id === entry.hunt.id && row.assessment_version === entry.assessment_version && row.claim.version === entry.claim.version);
  };
  function updateFilter(key: string, value: string) {
    setReview(null);
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== "hunt_cursor") next.delete("hunt_cursor");
      return next;
    });
  }
  function applyView(filters: HuntFilters) {
    setReview(null);
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries({
        hunt_status: filters.status ?? "",
        hunt_owner: filters.ownership,
        hunt_order: filters.order,
        hunt_priority: filters.priority ?? "",
        hunt_overdue: String(filters.overdue),
      })) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      next.delete("hunt_cursor");
      return next;
    });
  }
  const action = useMutation({
    mutationFn: async (command: Command) => {
      if (blocked || !writable || !currentEntry(command.entry)) throw new Error("Refresh the current hunt before changing it.");
      const capture = access.captureResult();
      const lease = captureSessionLease();
      await apiFetch(
        `/teams/${teamId}/hunts/${command.entry.assessment_id}/${command.entry.hunt.id}/claim`,
        {
          method: "POST",
          body: JSON.stringify({
            action: command.kind,
            expected_version: command.entry.claim.version,
            expected_assessment_version: command.entry.assessment_version,
          }),
        },
      );
      lease.assertCurrent();
      return capture(true);
    },
    onSuccess: (result) => {
      if (!access.currentData(result)) return;
      setNotice({ error: false, text: "Hunt queue updated." });
      void client.invalidateQueries({ queryKey: ["team-hunts", teamId] });
      void client.invalidateQueries({ queryKey: ["team-assessments"] });
    },
    onError: (error) => {
      setNotice({
        error: true,
        text: resolveApiErrorMessage(
          error,
          "The hunt action could not be confirmed. Refresh the queue before retrying.",
        ),
      });
      void client.invalidateQueries({ queryKey: ["team-hunts", teamId] });
    },
    onSettled: () => {
      pending.current = false;
    },
  });
  const run = (command: Command) => {
    if (pending.current || blocked || !writable || !currentEntry(command.entry)) return;
    pending.current = true;
    setNotice(null);
    action.mutate(command);
  };
  return (
    <section className="space-y-4" aria-labelledby="team-hunts-heading">
      <header>
        <h3 id="team-hunts-heading" className="font-display text-xl">
          Team hunt queue
        </h3>
        <p className="mt-1 text-sm">
          Review evidence, claim suggestions and promote accepted hunts into
          team investigations. Investigation assignment and outcomes remain in
          the investigation workspace.
        </p>
      </header>
      <TeamHuntSavedViews
        teamId={teamId}
        unavailable={blocked || action.isPending}
        filters={{
          status: status || null,
          ownership,
          order,
          priority: priority || null,
          overdue,
        }}
        onApply={applyView}
      />
      <fieldset disabled={action.isPending} className="flex flex-wrap gap-4">
        <label className="text-sm">
          Hunt status
          <select
            className="ml-2 rounded border p-2 dark:bg-[#072019]"
            value={status}
            onChange={(event) =>
              updateFilter("hunt_status", event.target.value)
            }
          >
            <option value="">All statuses</option>
            <option value="pending">Pending review</option>
            <option value="stale">Stale evidence or context</option>
            <option value="accepted">Accepted</option>
            <option value="rejected">Rejected</option>
          </select>
        </label>
        <label className="text-sm">
          Hunt ownership
          <select
            className="ml-2 rounded border p-2 dark:bg-[#072019]"
            value={ownership}
            onChange={(event) => updateFilter("hunt_owner", event.target.value)}
          >
            <option value="all">All owners</option>
            <option value="mine">Claimed by me</option>
            <option value="unclaimed">Unclaimed or former member</option>
          </select>
        </label>
        <label className="text-sm">
          Review order
          <select
            className="ml-2 rounded border p-2 dark:bg-[#072019]"
            value={order}
            onChange={(event) => updateFilter("hunt_order", event.target.value)}
          >
            <option value="oldest">Oldest evidence first</option>
            <option value="newest">Newest evidence first</option>
            <option value="due">Earliest deadline first</option>
          </select>
        </label>
        <label className="text-sm">
          Priority
          <select
            className="ml-2 rounded border p-2 dark:bg-[#072019]"
            value={priority}
            onChange={(event) =>
              updateFilter("hunt_priority", event.target.value)
            }
          >
            <option value="">All priorities</option>
            {["low", "normal", "high", "urgent"].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <input
            type="checkbox"
            checked={overdue}
            onChange={(event) =>
              updateFilter("hunt_overdue", event.target.checked ? "true" : "")
            }
          />{" "}
          Overdue reviews only
        </label>
      </fieldset>
      {notice && <p role={notice.error ? "alert" : "status"}>{notice.text}</p>}
      {query.isLoading && <p role="status">Loading team hunts…</p>}
      {query.isError && (
        <div role="alert">
          {resolveApiErrorMessage(
            query.error,
            "The hunt queue could not be loaded. Actions are paused.",
          )}{" "}
          <button
            type="button"
            className={TEAM_BUTTON}
            onClick={() => void query.refetch()}
          >
            Retry hunt queue
          </button>{" "}
          {cursor && (
            <button
              type="button"
              className={TEAM_BUTTON}
              onClick={() => updateFilter("hunt_cursor", "")}
            >
              Return to first hunt page
            </button>
          )}
        </div>
      )}
      {!lostAccess && query.data && (
        <>
          <p className="text-xs">
            Showing {query.data.items.length} accessible suggestions on this
            page. Evidence permissions may leave a page empty; continue when
            more pages are available.
          </p>
          {visibleRetainedEntries.length > 0 && <p role="status" className="text-sm">
            A hunt with an unsaved schedule left this page after a refresh. Its draft remains below; reload it to discard the draft. Actions for that hunt are paused until it returns to the queue.
          </p>}
          <nav className="flex gap-3" aria-label="Hunt queue pages">
            <button
              type="button"
              className={TEAM_BUTTON}
              disabled={!cursor || action.isPending}
              onClick={() => updateFilter("hunt_cursor", "")}
            >
              First page
            </button>
            <button
              type="button"
              className={TEAM_BUTTON}
              disabled={!query.data.next_cursor || action.isPending}
              onClick={() =>
                updateFilter("hunt_cursor", query.data!.next_cursor!)
              }
            >
              Next page
            </button>
          </nav>
        </>
      )}
          <div className="space-y-3" key={location.key}>
            {[...visibleEntries, ...retainedEntries].map((entry) => (
              <HuntReviewSchedule
                key={`${entry.assessment_id}:${entry.hunt.id}`}
                entry={entry}
                hidden={!query.data || lostAccess || retainedEntries.includes(entry) && !visibleRetainedEntries.includes(entry)}
                disabled={blocked || !writable || action.isPending}
                paused={retainedEntries.includes(entry)}
                canSubmit={() => Boolean(currentEntry(entry))}
                onDirtyChange={retainDraft}
              >{(schedule) => <article
                className="space-y-2 rounded border border-slate/25 p-3"
                aria-label={entry.hunt.title}
              >
                <div className="flex flex-wrap justify-between gap-2">
                  <h4 className="font-semibold">{entry.hunt.title}</h4>
                  <span className="tl-chip capitalize">{entry.status}</span>
                </div>
                <p className="text-sm">Source: {entry.item_title}</p>
                <p className="text-xs">
                  Owner:{" "}
                  {entry.owner_name ??
                    (entry.claim.owner_user_id
                      ? "Former account"
                      : "Unclaimed")}{" "}
                  · Evidence age:{" "}
                  {entry.evidence_age_seconds === null
                    ? "unavailable"
                    : `${Math.floor(entry.evidence_age_seconds / 3600)} hours`}{" "}
                  · Reviewer: {entry.reviewer_name ?? "Not recorded"}
                </p>
                {entry.reviewed_at && (
                  <p className="text-xs">
                    Reviewed {new Date(entry.reviewed_at).toLocaleString()}
                  </p>
                )}
                {entry.status === "stale" && (
                  <p className="text-sm">
                    Source evidence or team context changed. Regenerate the
                    assessment from the article before acting.
                  </p>
                )}
                {schedule}
                {entry.investigation && (
                  <p className="text-sm">
                    <Link
                      className="text-cyan underline"
                      to={`/investigations/${entry.investigation.id}`}
                    >
                      Open investigation
                    </Link>{" "}
                    · {entry.investigation.status}
                    {entry.investigation.disposition
                      ? ` · ${entry.investigation.disposition}`
                      : ""}
                  </p>
                )}
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className={TEAM_BUTTON}
                    disabled={blocked || action.isPending || retainedEntries.includes(entry)}
                    onClick={() => { if (currentEntry(entry)) setReview({ entry, admission: access.captureResult()(true) }); }}
                  >
                    Review hunt evidence
                  </button>
                  {writable && entry.can_claim && (
                    <button
                      type="button"
                      className={TEAM_BUTTON}
                      disabled={blocked || action.isPending || retainedEntries.includes(entry)}
                      onClick={() => run({ kind: "claim", entry })}
                    >
                      Claim hunt
                    </button>
                  )}
                  {writable && entry.can_release && (
                    <button
                      type="button"
                      className={TEAM_BUTTON}
                      disabled={blocked || action.isPending || retainedEntries.includes(entry)}
                      onClick={() => run({ kind: "unclaim", entry })}
                    >
                      Release hunt
                    </button>
                  )}
                </div>
              </article>}</HuntReviewSchedule>
            ))}
          </div>

      {review && !lostAccess && (
        <DialogSurface
          open
          title="Review team hunt"
          onClose={() => setReview(null)}
          panelClassName="max-w-3xl"
          describeBody={false}
        >
          <p className="text-sm">
            Source: {review.item_title}. Review notes use the existing session
            draft recovery.
          </p>
          <AssessmentWorkspace
            key={`${review.item_id}-${teamId}`}
            itemId={review.item_id}
            teamId={teamId}
            canWrite={writable && !blocked}
            canCreate={canInvestigate && !blocked}
            verifyAccess
          />
        </DialogSurface>
      )}
    </section>
  );
}
