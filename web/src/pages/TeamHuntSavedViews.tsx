import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { resolveApiErrorMessage } from "../api/errors";
import { captureSessionLease } from "../api/sessionLifecycle";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { createSecureRequestId } from "../utils/secureRandomId";
import { useQueryAccessScope } from "../hooks/useQueryAccessScope";
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
  const access = useQueryAccessScope(["team-hunt-views", teamId]);
  const [name, setName] = useState("");
  const [selection, setSelection] = useState<{ view: View; admission: { generation: number; value: boolean } } | null>(null);
  const [nameEdited, setNameEdited] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const requestId = useRef<string | null>(null);
  const pending = useRef(false);
  const query = useQuery({
    queryKey: ["team-hunt-views", teamId],
    enabled: !unavailable,
    ...access.queryOptions(({ signal }) =>
      apiFetch<Page>(`/teams/${teamId}/hunts/views`, { signal })),
  });
  const selected = selection && access.currentData(selection.admission) && query.data?.items.some((view) => view.id === selection.view.id) ? selection.view : null;
  useEffect(() => {
    if (!access.denied) return;
    setDeleting(false);
    if (!nameEdited) setName("");
  }, [access.denied, nameEdited]);
  const mutation = useMutation({
    mutationFn: async (remove: boolean) => {
      const state = client.getQueryState(["team-hunt-views", teamId]);
      const page = state?.status === "error" ? undefined : access.currentData<Page>(client.getQueryData(["team-hunt-views", teamId]));
      if (unavailable || !page?.can_manage || access.denied || remove && (!selected || !page.items.some((view) => view.id === selected.id))) throw new Error("Refresh the saved hunt views before changing them. Your name draft is preserved.");
      const capture = access.captureResult();
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
      return capture({ saved: value });
    },
    onSuccess: (result) => {
      const value = access.currentData(result);
      if (!value) return;
      const saved = value.saved;
      requestId.current = null;
      setSelection(saved ? { view: saved, admission: access.captureResult()(true) } : null);
      setNameEdited(false);
      setName(saved?.name ?? "");
      setDeleting(false);
      void client.invalidateQueries({ queryKey: ["team-hunt-views", teamId] });
    },
    onSettled: () => { pending.current = false; },
  });
  const submit = (remove: boolean) => {
    if (pending.current || unavailable || query.isError || access.denied) return;
    pending.current = true;
    mutation.mutate(remove);
  };
  const lost = access.denied;
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
                  query.data?.items.find(
                    (entry) => entry.id === event.target.value,
                  ) ?? null;
                setSelection(view ? { view, admission: access.captureResult()(true) } : null);
                setNameEdited(false);
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
                  onChange={(event) => { setNameEdited(true); setName(event.target.value); }}
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
        open={deleting && Boolean(selected) && !access.denied}
        title="Delete saved team hunt view?"
        description="This removes the shared filter preset. Hunts and reviews are retained."
        confirmLabel="Delete view"
        isConfirming={mutation.isPending}
        confirmDisabled={unavailable || query.isError || access.denied}
        onCancel={() => setDeleting(false)}
        onConfirm={() => submit(true)}
      />
    </section>
  );
}
