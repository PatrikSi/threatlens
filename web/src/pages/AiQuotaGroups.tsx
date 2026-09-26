import { useState } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { resolveApiErrorMessage } from "../api/errors";
import type { AiProviderConnectionsController } from "./useAiProviderConnections";
import { TEAM_BUTTON } from "./teamPresentation";

export function AiQuotaGroups({
  controller: c,
}: {
  controller: AiProviderConnectionsController;
}) {
  const q = c.quotas;
  const [discard, setDiscard] = useState(false);
  const selected = c.editor?.baseline;
  const draft = q.editor?.draft;
  const known = [
    ...(c.providers.data?.items ?? []),
    ...(c.assignedProviders.data ?? []),
    ...(selected ? [selected] : []),
  ];
  const name = (key: string) =>
    key === "legacy"
      ? "Legacy provider settings"
      : (known.find((entry) => `profile:${entry.id}` === key)?.name ?? key);
  const addMember = (key: string) =>
    draft &&
    q.update(
      "provider_keys",
      [...new Set([...draft.provider_keys, key])].sort(),
    );
  return (
    <section
      className="space-y-3 border-t border-slate/20 pt-4"
      aria-labelledby="ai-quota-title"
    >
      <h4 id="ai-quota-title" className="font-semibold">
        Shared provider account quotas
      </h4>
      <p className="text-sm">
        Group connections that share an upstream account. Account limits apply
        in addition to each profile’s limits. Team requests take turns; requests
        without a team share one background allocation. Limits are local
        safeguards, not provider billing totals.
      </p>
      {q.query.isLoading && <p role="status">Loading account quotas…</p>}
      {q.query.isError && (
        <div role="alert">
          {resolveApiErrorMessage(
            q.query.error,
            "Account quotas could not be loaded.",
          )}{" "}
          <button
            type="button"
            className={TEAM_BUTTON}
            onClick={() => void q.query.refetch()}
          >
            Retry account quotas
          </button>
        </div>
      )}
      {q.notice && (
        <p role={q.notice.error ? "alert" : "status"}>{q.notice.text}</p>
      )}
      <div className="flex flex-wrap gap-2">
        {q.query.data?.items.map((group) => (
          <button
            type="button"
            key={group.id}
            className={TEAM_BUTTON}
            disabled={q.dirty || q.busy}
            onClick={() => q.select(group)}
          >
            {group.name}
          </button>
        ))}
        <button
          type="button"
          className={TEAM_BUTTON}
          disabled={q.dirty || q.busy || q.query.isError}
          onClick={() => q.select()}
        >
          Add account quota
        </button>
      </div>
      {draft && (
        <fieldset
          disabled={q.busy || q.query.isError}
          className="space-y-3 rounded border border-slate/20 p-3"
        >
          <legend className="px-1 font-semibold">
            {q.editor?.baseline ? "Edit account quota" : "New account quota"}
          </legend>
          <label className="block text-sm">
            Account quota name
            <input
              className="mt-1 block w-full rounded border p-2 dark:bg-[#072019]"
              maxLength={120}
              value={draft.name}
              onChange={(event) => q.update("name", event.target.value)}
            />
          </label>
          <div className="grid gap-3 md:grid-cols-3">
            {(
              [
                ["max_concurrent_requests", "Account concurrent requests"],
                ["hourly_token_budget", "Account tokens per rolling hour"],
                ["max_concurrent_per_team", "Concurrent requests per team"],
              ] as const
            ).map(([key, label]) => (
              <label className="text-sm" key={key}>
                {label}
                <input
                  type="number"
                  min={0}
                  max={key === "hourly_token_budget" ? 1e12 : 1000}
                  step={1}
                  className="mt-1 block w-full rounded border p-2 dark:bg-[#072019]"
                  value={draft[key]}
                  onChange={(event) => q.update(key, event.target.value)}
                />
              </label>
            ))}
          </div>
          <p className="text-xs">
            Use 0 for unlimited. Unknown provider outcomes retain their reserved
            token estimate. Fairness waits expire after three minutes without a
            retry.
          </p>
          <ul className="space-y-2">
            {draft.provider_keys.map((key) => (
              <li
                className="flex items-center justify-between gap-2 text-sm"
                key={key}
              >
                <span>{name(key)}</span>
                <button
                  type="button"
                  className={TEAM_BUTTON}
                  aria-label={`Remove ${name(key)} from account quota`}
                  onClick={() =>
                    q.update(
                      "provider_keys",
                      draft.provider_keys.filter((entry) => entry !== key),
                    )
                  }
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={TEAM_BUTTON}
              disabled={
                !selected ||
                draft.provider_keys.includes(`profile:${selected?.id}`)
              }
              onClick={() => selected && addMember(`profile:${selected.id}`)}
            >
              Add selected provider to quota
            </button>
            <button
              type="button"
              className={TEAM_BUTTON}
              disabled={draft.provider_keys.includes("legacy")}
              onClick={() => addMember("legacy")}
            >
              Add legacy provider to quota
            </button>
          </div>
          <p className="text-xs">
            Select a connection in the provider list above to add it. A
            connection belongs to one account quota. Remove all members to leave
            a quota inactive; recent charges remain retained.
          </p>
          {q.invalid && (
            <p role="alert" className="text-sm">
              Enter a name and nonnegative whole-number limits within the
              displayed maximums.
            </p>
          )}
          {q.editor?.baseline &&
            q.query.data?.items.some(
              (group) =>
                group.id === q.editor?.id &&
                group.version !== q.editor.baseline?.version,
            ) && (
              <p role="alert">
                This quota changed on the server. Discard the draft and select
                it again to load the current version.
              </p>
            )}
          <div className="flex gap-2">
            <button
              type="button"
              className={TEAM_BUTTON}
              disabled={Boolean(q.invalid) || !q.dirty}
              onClick={q.save}
            >
              Save account quota
            </button>
            <button
              type="button"
              className={TEAM_BUTTON}
              onClick={() => (q.dirty ? setDiscard(true) : q.discard())}
            >
              Close quota editor
            </button>
          </div>
        </fieldset>
      )}
      <ConfirmDialog
        open={discard}
        title="Discard account quota changes?"
        description="Your unsaved account quota changes will be discarded."
        confirmLabel="Discard changes"
        onCancel={() => setDiscard(false)}
        onConfirm={() => {
          q.discard();
          setDiscard(false);
        }}
      />
    </section>
  );
}
