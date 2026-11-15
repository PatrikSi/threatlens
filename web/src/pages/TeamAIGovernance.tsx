import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { resolveApiErrorMessage } from "../api/errors";
import { captureSessionLease } from "../api/sessionLifecycle";
import { useQueryAccessScope } from "../hooks/useQueryAccessScope";
import { useUnsavedChangesWarning } from "../hooks/useUnsavedChangesWarning";
import { TEAM_BUTTON } from "./teamPresentation";
import { TeamAIProviderPicker } from "./TeamAIProviderPicker";
import { TeamAIHandlingRestrictions } from './TeamAIHandlingRestrictions';

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
  labels: structuredClone(policy.label_destinations),
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
  const access = useQueryAccessScope(["teams", "ai-governance", teamId, admin]);
  const retained = useRef<Policy | undefined>(undefined);
  const query = useQuery({
    queryKey: ["teams", "ai-governance", teamId, admin],
    enabled: !unavailable,
    ...access.queryOptions(({ signal }) => apiFetch<Policy>(path, { signal })),
    refetchInterval: 30_000,
  });
  if (query.data) retained.current = query.data;
  const policy = query.data ?? (retained.current?.team_id === teamId ? retained.current : undefined);
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
      {(query.isError || access.denied) && (
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
      {policy && (
        <PolicyEditor
          key={`${teamId}:${admin}`}
          policy={policy}
          hidden={!query.data}
          path={path}
          admin={admin}
          disabled={unavailable || query.isError || access.denied}
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
  hidden,
}: {
  policy: Policy;
  path: string;
  admin: boolean;
  disabled: boolean;
  hidden: boolean;
}) {
  const client = useQueryClient();
  const access = useQueryAccessScope(["teams", "ai-governance", policy.team_id, admin]);
  const [baseline, setBaseline] = useState(policy);
  const [baselineGeneration, setBaselineGeneration] = useState(access.generation);
  const [draft, setDraft] = useState(() => draftFor(policy));
  const [notice, setNotice] = useState("");
  const approved = [...new Set(draft.approved.split('\n').map((key) => key.trim()).filter(Boolean))];
  const invalidKey = approved.some((key) => !/^(legacy|profile:[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12})$/i.test(key));
  const validation = admin && (invalidKey ? 'Choose a configured provider or enter a valid legacy/profile provider key.'
    : draft.selected && !approved.includes(draft.selected) ? 'The assessment destination must be approved. Choose another destination or inherit the installation route.'
      : Object.values(draft.labels).some((keys) => keys.some((key) => !approved.includes(key))) ? 'Remove unapproved providers from handling restrictions before saving.' : null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftFor(baseline));
  const cleanRecovery = !dirty && baselineGeneration !== access.generation;
  useEffect(() => {
    if (hidden || !cleanRecovery) return;
    const current = access.currentData<Policy>(client.getQueryData(["teams", "ai-governance", policy.team_id, admin]));
    if (!current) return;
    setBaseline(current);
    setDraft(draftFor(current));
    setBaselineGeneration(access.captureResult()(true).generation);
    setNotice("");
  }, [access, admin, cleanRecovery, client, hidden, policy.team_id]);
  const discard = useUnsavedChangesWarning(
    dirty,
    "Discard unsaved AI destination policy?",
  );
  const save = useMutation({
    mutationFn: async () => {
      if (validation) throw new Error(validation);
      if (disabled || hidden || access.denied || !allowed || client.getQueryState(["teams", "ai-governance", policy.team_id, admin])?.status === "error") throw new Error("Refresh the current destination policy before changing it. Your draft is preserved.");
      const capture = access.captureResult();
      const lease = captureSessionLease();
      const saved = await apiFetch<Policy>(path, {
        method: admin ? "PUT" : "PATCH",
        body: JSON.stringify({
          expected_version: baseline.version,
          selected_provider_key: draft.selected || null,
          ...(admin
            ? {
                approved_provider_keys: approved,
                label_destinations: draft.labels,
              }
            : {}),
        }),
      });
      lease.assertCurrent();
      return capture(saved);
    },
    onSuccess: (result) => {
      const saved = access.currentData(result);
      if (!saved) return;
      setBaseline(saved);
      setBaselineGeneration(result.generation);
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
  if (hidden || cleanRecovery) return <>{discard.discardDialog}</>;
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
            <TeamAIHandlingRestrictions value={draft.labels} approved={approved} destinations={policy.destinations}
              onChange={(labels) => setDraft({ ...draft, labels })} />
          </>
        )}
        <label className="block text-sm">
          Assessment destination
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
              {[...new Set([...(admin ? approved : policy.destinations.map((entry) => entry.key)), ...(draft.selected ? [draft.selected] : [])])].map((key) => (
                <option key={key} value={key}>
                  {policy.destinations.find((entry) => entry.key === key)?.name ?? (key === 'legacy' ? 'Legacy provider settings' : key)}
                  {policy.destinations.find((entry) => entry.key === key)?.available === false ? ' — unavailable' : ''}
                </option>
              ))}
            </select>
        </label>
        <button
          className={TEAM_BUTTON}
          disabled={Boolean(validation) || (!dirty && policy.configured)}
          onClick={() => save.mutate()}
        >
          {save.isPending
            ? "Saving policy…"
            : admin
              ? "Approve destination policy"
              : "Save assessment destination"}
        </button>
      </fieldset>
      {validation && <p role="alert">{validation}</p>}
      {(dirty || baseline.version !== policy.version) && (
        <button
          className={TEAM_BUTTON}
          disabled={save.isPending}
          onClick={() =>
            discard(() => {
              const admission = access.captureResult()(true);
              const current = access.currentData<Policy>(client.getQueryData(["teams", "ai-governance", policy.team_id, admin]))
                ?? (admission.generation === 0 ? policy : undefined);
              if (!current || !access.currentData(admission)) return;
              setBaseline(current);
              setDraft(draftFor(current));
              setBaselineGeneration(admission.generation);
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
