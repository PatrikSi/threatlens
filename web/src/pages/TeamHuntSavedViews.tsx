import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "../api/client";
import { resolveApiErrorMessage } from "../api/errors";
import { captureSessionLease } from "../api/sessionLifecycle";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { createSecureRequestId } from "../utils/secureRandomId";
import { TEAM_BUTTON } from "./teamPresentation";

export type HuntFilters = {
  status: string | null;
  ownership: string;
  order: string;
  priority: string | null;
  overdue: boolean;
};
type View = { id: string; version: number; name: string; filters: HuntFilters };
type Page = { items: View[]; can_manage: boolean };
export function TeamHuntSavedViews({
  teamId,
  filters,
  onApply,
  unavailable,
}: {
  teamId: string;
  filters: HuntFilters;
  onApply: (filters: HuntFilters) => void;
  unavailable: boolean;
}) {
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<View | null>(null);
  const [deleting, setDeleting] = useState(false);
  const requestId = useRef<string | null>(null);
  const pending = useRef(false);
  const query = useQuery({
    queryKey: ["team-hunt-views", teamId],
    enabled: !unavailable,
    queryFn: ({ signal }) =>
      apiFetch<Page>(`/teams/${teamId}/hunts/views`, { signal }),
  });
  const mutation = useMutation({
    mutationFn: async (remove: boolean) => {
      const lease = captureSessionLease();
      if (!requestId.current) requestId.current = createSecureRequestId();
      const id = selected?.id ?? requestId.current;
      const value = await apiFetch<View | undefined>(
        `/teams/${teamId}/hunts/views/${id}${remove ? `?expected_version=${selected?.version}` : ""}`,
        {
          method: remove ? "DELETE" : "PUT",
          ...(!remove
            ? {
                body: JSON.stringify({
                  name: name.trim(),
                  expected_version: selected?.version ?? 0,
                  filters,
                }),
              }
            : {}),
        },
      );
      lease.assertCurrent();
      return value;
    },
    onSuccess: (saved) => {
      requestId.current = null;
      setSelected(saved ?? null);
      setName(saved?.name ?? "");
      setDeleting(false);
      void client.invalidateQueries({ queryKey: ["team-hunt-views", teamId] });
    },
    onSettled: () => { pending.current = false; },
  });
  const submit = (remove: boolean) => {
    if (pending.current || unavailable || query.isError) return;
    pending.current = true;
    mutation.mutate(remove);
  };
  const lost =
    query.error instanceof ApiError &&
    [401, 403, 404].includes(query.error.status);
  return (
    <section className="space-y-2" aria-label="Saved team hunt filters">
      {query.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            query.error,
            "Saved hunt views unavailable. Current queue filters still work.",
          )}{" "}
          <button className={TEAM_BUTTON} onClick={() => void query.refetch()}>
            Retry saved hunt views
          </button>
        </p>
      )}
      {mutation.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            mutation.error,
            "The saved hunt view could not be changed. Reload its version before retrying.",
          )}
        </p>
      )}
      {!lost && query.data && (
        <fieldset
          disabled={unavailable || query.isError || mutation.isPending}
          className="flex flex-wrap items-end gap-3"
        >
          <label className="text-sm">
            Saved team hunt view
            <select
              className="ml-2 rounded border p-2 dark:bg-[#072019]"
              value={selected?.id ?? ""}
              onChange={(event) => {
                const view =
                  query.data.items.find(
                    (entry) => entry.id === event.target.value,
                  ) ?? null;
                setSelected(view);
                setName(view?.name ?? "");
                requestId.current = null;
                if (view) onApply(view.filters);
              }}
            >
              <option value="">Custom filters</option>
              {query.data.items.map((view) => (
                <option key={view.id} value={view.id}>
                  {view.name}
                </option>
              ))}
            </select>
          </label>
          {query.data.can_manage && (
            <>
              <label className="text-sm">
                View name
                <input
                  className="ml-2 rounded border p-2 dark:bg-[#072019]"
                  maxLength={80}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <button
                className={TEAM_BUTTON}
                disabled={!name.trim()}
                onClick={() => submit(false)}
              >
                {selected ? "Update saved hunt view" : "Save team hunt view"}
              </button>
              {selected && (
                <button
                  className={TEAM_BUTTON}
                  onClick={() => setDeleting(true)}
                >
                  Delete saved hunt view
                </button>
              )}
            </>
          )}
        </fieldset>
      )}
      <ConfirmDialog
        open={deleting}
        title="Delete saved team hunt view?"
        description="This removes the shared filter preset. Hunts and reviews are retained."
        confirmLabel="Delete view"
        isConfirming={mutation.isPending}
        onCancel={() => setDeleting(false)}
        onConfirm={() => submit(true)}
      />
    </section>
  );
}
