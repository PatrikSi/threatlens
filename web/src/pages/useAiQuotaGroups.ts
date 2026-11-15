import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { resolveApiErrorMessage } from "../api/errors";
import { captureSessionLease } from "../api/sessionLifecycle";
import { useQueryAccessScope } from "../hooks/useQueryAccessScope";
import { createSecureRequestId } from "../utils/secureRandomId";

export interface AiQuotaGroup {
  id: string;
  version: number;
  name: string;
  provider_keys: string[];
  max_concurrent_requests: number;
  hourly_token_budget: number;
  max_concurrent_per_team: number;
  minute_request_budget?: number;
  minute_token_budget?: number;
  team_hourly_token_budgets?: Record<string, number>;
}
type Page = {
  items: AiQuotaGroup[];
  total: number;
  limit: number;
  offset: number;
};
type Draft = {
  name: string;
  provider_keys: string[];
  max_concurrent_requests: string;
  hourly_token_budget: string;
  max_concurrent_per_team: string;
  minute_request_budget: string;
  minute_token_budget: string;
  team_hourly_token_budgets: string;
};
type QuotaEditor = {
  id: string;
  baseline?: AiQuotaGroup;
  draft: Draft;
  readGeneration: number;
};
const draftFor = (group?: AiQuotaGroup): Draft => ({
  name: group?.name ?? "",
  provider_keys: [...(group?.provider_keys ?? [])],
  max_concurrent_requests: String(group?.max_concurrent_requests ?? 0),
  hourly_token_budget: String(group?.hourly_token_budget ?? 0),
  max_concurrent_per_team: String(group?.max_concurrent_per_team ?? 1),
  minute_request_budget: String(group?.minute_request_budget ?? 0),
  minute_token_budget: String(group?.minute_token_budget ?? 0),
  team_hourly_token_budgets: JSON.stringify(
    group?.team_hourly_token_budgets ?? {},
    null,
    2,
  ),
});

export function useAiQuotaGroups(enabled: boolean) {
  const client = useQueryClient();
  const access = useQueryAccessScope(["ai", "quota-groups"]);
  const [editor, setEditor] = useState<QuotaEditor | null>(null);
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(
    null,
  );
  const pending = useRef(false);
  const query = useQuery({
    queryKey: ["ai", "quota-groups"],
    enabled,
    ...access.queryOptions(({ signal }) =>
      apiFetch<Page>("/ai/quota-groups?limit=100", { signal })),
  });
  const dirty = Boolean(
    editor &&
      JSON.stringify(editor.draft) !==
        JSON.stringify(draftFor(editor.baseline)),
  );
  const currentGroup = editor?.baseline
    ? access.currentData<Page>(client.getQueryData(["ai", "quota-groups"]))?.items.find((group) => group.id === editor.id)
    : undefined;
  const baselineRetired = Boolean(editor?.baseline && !access.currentData({
    generation: editor.readGeneration, value: true,
  }));
  const editorHidden = Boolean(editor?.baseline) &&
    (access.denied || access.generation > 0 && !currentGroup || baselineRetired && !dirty);
  const readUnavailable = query.isError || access.denied ||
    Boolean(editor?.baseline && (!currentGroup || baselineRetired));
  useEffect(() => {
    if (dirty || !baselineRetired || query.isError) return;
    const current = access.currentData<Page>(client.getQueryData(["ai", "quota-groups"]))?.items.find((group) => group.id === editor?.id);
    if (!current) return;
    setEditor({ id: current.id, baseline: current, draft: draftFor(current), readGeneration: access.captureResult()(true).generation });
  }, [access, baselineRetired, client, dirty, editor?.id, query.isError]);
  const invalid =
    editor &&
    (!editor.draft.name.trim() ||
      editor.draft.name.trim().length > 120 ||
      (
        [
          "max_concurrent_requests",
          "hourly_token_budget",
          "max_concurrent_per_team",
          "minute_request_budget",
          "minute_token_budget",
        ] as const
      ).some((key) => {
        const value = editor.draft[key].trim();
        return (
          !value ||
          !Number.isSafeInteger(Number(value)) ||
          Number(value) < 0 ||
          Number(value) >
            (key.includes("token")
              ? 1e12
              : key === "minute_request_budget"
                ? 1e6
                : 1000)
        );
      }));
  const save = useMutation({
    mutationFn: async (submitted: NonNullable<typeof editor>) => {
      if (!enabled || query.isError || access.denied || client.getQueryState(["ai", "quota-groups"])?.status === "error") throw new Error("Refresh the current account quotas before saving. Your draft is preserved.");
      const current = access.currentData<Page>(client.getQueryData(["ai", "quota-groups"]));
      if (submitted.baseline && (!access.currentData({ generation: submitted.readGeneration, value: true })
        || !current?.items.some((group) => group.id === submitted.id))) {
        throw new Error("Reload the saved account quota before saving. Your draft is preserved.");
      }
      const capture = access.captureResult();
      const lease = captureSessionLease();
      const result = await apiFetch<AiQuotaGroup>(
        `/ai/quota-groups${submitted.baseline ? `/${submitted.id}` : ""}`,
        {
          method: submitted.baseline ? "PUT" : "POST",
          body: JSON.stringify({
            ...submitted.draft,
            name: submitted.draft.name.trim(),
            max_concurrent_requests: Number(
              submitted.draft.max_concurrent_requests,
            ),
            hourly_token_budget: Number(submitted.draft.hourly_token_budget),
            minute_request_budget: Number(
              submitted.draft.minute_request_budget,
            ),
            minute_token_budget: Number(submitted.draft.minute_token_budget),
            team_hourly_token_budgets: JSON.parse(
              submitted.draft.team_hourly_token_budgets,
            ) as unknown,
            max_concurrent_per_team: Number(
              submitted.draft.max_concurrent_per_team,
            ),
            ...(submitted.baseline
              ? { version: submitted.baseline.version }
              : { id: submitted.id }),
          }),
        },
      );
      lease.assertCurrent();
      return capture(result);
    },
    onSuccess: async (result) => {
      const saved = access.currentData(result);
      if (!saved) return;
      await client.cancelQueries({ queryKey: ["ai", "quota-groups"] });
      if (!access.currentData(result)) return;
      setEditor({ id: saved.id, baseline: saved, draft: draftFor(saved), readGeneration: result.generation });
      setNotice({
        error: false,
        text: "Shared account quota saved. Pending work keeps its provider selection and uses current account limits.",
      });
      void client.invalidateQueries({ queryKey: ["ai", "quota-groups"] });
    },
    onError: (error) => {
      setNotice({
        error: true,
        text: resolveApiErrorMessage(
          error,
          "Could not save the shared account quota. Your draft and request ID are retained.",
        ),
      });
      void client.invalidateQueries({ queryKey: ["ai", "quota-groups"] });
    },
    onSettled: () => {
      pending.current = false;
    },
  });
  return {
    query,
    editor: editorHidden ? null : editor,
    hasEditor: Boolean(editor),
    readGeneration: access.generation,
    readUnavailable,
    dirty,
    invalid,
    notice,
    busy: save.isPending,
    select: (group?: AiQuotaGroup) => {
      if (pending.current || dirty || readUnavailable) return;
      try {
        setEditor({
          id: group?.id ?? createSecureRequestId(),
          baseline: group,
          draft: draftFor(group),
          readGeneration: access.captureResult()(true).generation,
        });
        setNotice(null);
      } catch (error) {
        setNotice({
          error: true,
          text: resolveApiErrorMessage(
            error,
            "Could not prepare a quota group. No request was sent.",
          ),
        });
      }
    },
    discard: () => {
      if (!pending.current) setEditor(null);
    },
    update: <K extends keyof Draft>(key: K, value: Draft[K]) => {
      if (!pending.current)
        setEditor((current) =>
          current
            ? { ...current, draft: { ...current.draft, [key]: value } }
            : null,
        );
    },
    save: () => {
      if (editor && !invalid && !pending.current && !readUnavailable) {
        pending.current = true;
        save.mutate(editor);
      }
    },
  };
}
