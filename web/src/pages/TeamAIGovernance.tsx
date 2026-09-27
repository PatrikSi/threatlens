import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "../api/client";
import { resolveApiErrorMessage } from "../api/errors";
import { captureSessionLease } from "../api/sessionLifecycle";
import { useUnsavedChangesWarning } from "../hooks/useUnsavedChangesWarning";
import { TEAM_BUTTON } from "./teamPresentation";
import { TeamAIProviderPicker } from "./TeamAIProviderPicker";

type Policy = {
  team_id: string;
  version: number;
  configured: boolean;
  approved_provider_keys: string[];
  selected_provider_key: string | null;
  label_destinations: Record<string, string[]>;
  destinations: { key: string; name: string; available: boolean }[];
  can_manage: boolean;
  can_approve: boolean;
};
const fieldClass = "mt-1 block w-full rounded border p-2 dark:bg-[#072019]";
const draftFor = (policy: Policy) => ({
  selected: policy.selected_provider_key ?? "",
  approved: policy.approved_provider_keys.join("\n"),
  labels: JSON.stringify(policy.label_destinations, null, 2),
});

export function TeamAIGovernance({
  teamId,
  admin = false,
  unavailable = false,
}: {
  teamId: string;
  admin?: boolean;
  unavailable?: boolean;
}) {
  const path = admin
    ? `/ai/team-governance/${teamId}`
    : `/teams/${teamId}/ai-governance`;
  const query = useQuery({
    queryKey: ["teams", "ai-governance", teamId, admin],
    enabled: !unavailable,
    queryFn: ({ signal }) => apiFetch<Policy>(path, { signal }),
    refetchInterval: 30_000,
  });
  const lost =
    query.error instanceof ApiError &&
    [401, 403, 404].includes(query.error.status);
  return (
    <section className="space-y-3" aria-label="Team AI destinations">
      <h3 className="font-display text-xl">Team AI destinations</h3>
      <p className="text-sm">
        Administrators approve providers and handling restrictions. Team
        managers choose an approved assessment destination. Pending work keeps
        its accepted provider; current restrictions are checked before every
        request.
      </p>
      {query.isLoading && <p role="status">Loading destination policy…</p>}
      {query.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            query.error,
            "Destination policy could not be refreshed. Editing is paused.",
          )}{" "}
          <button className={TEAM_BUTTON} onClick={() => void query.refetch()}>
            Retry destination policy
          </button>
        </p>
      )}
      {!lost && query.data && (
        <PolicyEditor
          key={`${teamId}:${admin}`}
          policy={query.data}
          path={path}
          admin={admin}
          disabled={unavailable || query.isError}
        />
      )}
    </section>
  );
}

function PolicyEditor({
  policy,
  path,
  admin,
  disabled,
}: {
  policy: Policy;
  path: string;
  admin: boolean;
  disabled: boolean;
}) {
  const client = useQueryClient();
  const [baseline, setBaseline] = useState(policy);
  const [draft, setDraft] = useState(() => draftFor(policy));
  const [notice, setNotice] = useState("");
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftFor(baseline));
  const discard = useUnsavedChangesWarning(
    dirty,
    "Discard unsaved AI destination policy?",
  );
  const save = useMutation({
    mutationFn: async () => {
      const lease = captureSessionLease();
      const saved = await apiFetch<Policy>(path, {
        method: admin ? "PUT" : "PATCH",
        body: JSON.stringify({
          expected_version: baseline.version,
          selected_provider_key: draft.selected || null,
          ...(admin
            ? {
                approved_provider_keys: draft.approved
                  .split("\n")
                  .map((key) => key.trim())
                  .filter(Boolean),
                label_destinations: JSON.parse(draft.labels) as unknown,
              }
            : {}),
        }),
      });
      lease.assertCurrent();
      return saved;
    },
    onSuccess: (saved) => {
      setBaseline(saved);
      setDraft(draftFor(saved));
      setNotice(
        "Destination policy saved. New work uses the selected route; queued work is checked against the new restrictions.",
      );
      void client.invalidateQueries({
        queryKey: ["teams", "ai-governance", policy.team_id],
      });
    },
  });
  const allowed = admin
    ? policy.can_approve
    : policy.can_manage && policy.configured;
  return (
    <div className="space-y-3">
      {discard.discardDialog}
      {!policy.configured && (
        <p className="text-sm">
          No team policy is configured. Installation routing applies. An
          administrator can approve a restricted destination list; an empty
          approved list blocks team AI requests.
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {save.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            save.error,
            "Could not save destination policy. Check provider keys and label rules; your draft is retained.",
          )}
        </p>
      )}
      {policy.version !== baseline.version && (
        <p role="status">
          The policy changed elsewhere. This draft retains revision{" "}
          {baseline.version}; reload before saving newer policy.
        </p>
      )}
      <fieldset
        disabled={disabled || !allowed || save.isPending}
        className="space-y-3"
      >
        {admin && (
          <>
            <TeamAIProviderPicker
              onAdd={(key) =>
                setDraft((current) => ({
                  ...current,
                  approved: [
                    ...new Set([
                      ...current.approved
                        .split("\n")
                        .map((value) => value.trim())
                        .filter(Boolean),
                      key,
                    ]),
                  ].join("\n"),
                }))
              }
            />
            <label className="block text-sm">
              Approved provider keys
              <textarea
                className={fieldClass}
                rows={4}
                maxLength={6500}
                value={draft.approved}
                onChange={(event) =>
                  setDraft({ ...draft, approved: event.target.value })
                }
              />
            </label>
            <p className="text-xs">
              One key per line: legacy or profile:&lt;provider UUID&gt;. Copy
              provider IDs from AI settings. Removing a key stops future
              requests from pending work using it.
            </p>
            <label className="block text-sm">
              Handling destination restrictions (JSON)
              <textarea
                className={fieldClass}
                rows={5}
                maxLength={65536}
                value={draft.labels}
                onChange={(event) =>
                  setDraft({ ...draft, labels: event.target.value })
                }
              />
            </label>
            <p className="text-xs">
              Map handling-label UUIDs to approved provider-key arrays. An empty
              array denies AI for that label. Omitted labels use the approved
              list. Both captured and current labels apply.
            </p>
          </>
        )}
        <label className="block text-sm">
          Assessment destination
          {admin ? (
            <input
              className={fieldClass}
              value={draft.selected}
              maxLength={64}
              placeholder="Inherit installation route"
              onChange={(event) =>
                setDraft({ ...draft, selected: event.target.value })
              }
            />
          ) : (
            <select
              className={fieldClass}
              value={draft.selected}
              onChange={(event) =>
                setDraft({ ...draft, selected: event.target.value })
              }
            >
              <option value="">
                Inherit installation route (subject to approval)
              </option>
              {policy.destinations.map((destination) => (
                <option key={destination.key} value={destination.key}>
                  {destination.name}
                  {destination.available ? "" : " — unavailable"}
                </option>
              ))}
            </select>
          )}
        </label>
        <button
          className={TEAM_BUTTON}
          disabled={!dirty && policy.configured}
          onClick={() => save.mutate()}
        >
          {save.isPending
            ? "Saving policy…"
            : admin
              ? "Approve destination policy"
              : "Save assessment destination"}
        </button>
      </fieldset>
      {(dirty || baseline.version !== policy.version) && (
        <button
          className={TEAM_BUTTON}
          disabled={save.isPending}
          onClick={() =>
            discard(() => {
              setBaseline(policy);
              setDraft(draftFor(policy));
              save.reset();
            })
          }
        >
          Reload destination policy
        </button>
      )}
    </div>
  );
}
