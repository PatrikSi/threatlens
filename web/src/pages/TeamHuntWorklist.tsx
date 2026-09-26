import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, apiFetch } from "../api/client";
import { resolveApiErrorMessage } from "../api/errors";
import { captureSessionLease } from "../api/sessionLifecycle";
import { DialogSurface } from "../components/ConfirmDialog";
import type { HuntSuggestion } from "../types/articleIntelligence";
import { AssessmentWorkspace } from "./ArticleTeamAssessment";
import { TEAM_BUTTON } from "./teamPresentation";

interface HuntEntry {
  assessment_id: string;
  assessment_version: number;
  item_id: string;
  item_title: string;
  team_id: string;
  status: "pending" | "stale" | "accepted" | "rejected";
  generated_at: string | null;
  evidence_age_seconds: number | null;
  hunt: HuntSuggestion;
  claim: { version: number; owner_user_id: string | null };
  owner_name: string | null;
  reviewer_name: string | null;
  reviewed_at: string | null;
  can_claim: boolean;
  can_release: boolean;
  investigation: {
    id: string;
    status: string;
    disposition: string | null;
    assignee_user_id: string | null;
  } | null;
}
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
  const client = useQueryClient();
  const [review, setReview] = useState<HuntEntry | null>(null);
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
  const cursor = params.get("hunt_cursor") ?? "";
  const query = useQuery({
    queryKey: ["team-hunts", teamId, status, ownership, cursor],
    enabled: !unavailable,
    queryFn: ({ signal }) =>
      apiFetch<HuntPage>(
        `/teams/${teamId}/hunts?${new URLSearchParams({ limit: "25", ownership, ...(status ? { status } : {}), ...(cursor ? { cursor } : {}) })}`,
        { signal },
      ),
    refetchInterval: 30_000,
  });
  const lostAccess =
    query.error instanceof ApiError &&
    [401, 403, 404].includes(query.error.status);
  const blocked = unavailable || query.isError;
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
  const action = useMutation({
    mutationFn: async (command: Command) => {
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
    },
    onSuccess: () => {
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
    if (pending.current || blocked || !writable) return;
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
          <div className="space-y-3">
            {query.data.items.map((entry) => (
              <article
                key={`${entry.assessment_id}:${entry.hunt.id}`}
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
                    disabled={blocked || action.isPending}
                    onClick={() => setReview(entry)}
                  >
                    Review hunt evidence
                  </button>
                  {writable && entry.can_claim && (
                    <button
                      type="button"
                      className={TEAM_BUTTON}
                      disabled={blocked || action.isPending}
                      onClick={() => run({ kind: "claim", entry })}
                    >
                      Claim hunt
                    </button>
                  )}
                  {writable && entry.can_release && (
                    <button
                      type="button"
                      className={TEAM_BUTTON}
                      disabled={blocked || action.isPending}
                      onClick={() => run({ kind: "unclaim", entry })}
                    >
                      Release hunt
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
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
