import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { resolveApiErrorMessage } from "../api/errors";

type ProviderPage = {
  items: { id: string; name: string; enabled: boolean }[];
  total: number;
};
export function TeamAIProviderPicker({
  onAdd,
}: {
  onAdd: (key: string) => void;
}) {
  const query = useQuery({
    queryKey: ["ai", "team-provider-choices"],
    queryFn: ({ signal }) =>
      apiFetch<ProviderPage>("/ai/providers?limit=100", { signal }),
  });
  return (
    <div className="space-y-1">
      <label className="block text-sm">
        Add an approved destination
        <select
          className="mt-1 block w-full rounded border p-2 dark:bg-[#072019]"
          value=""
          onChange={(event) => {
            if (event.target.value) onAdd(event.target.value);
          }}
        >
          <option value="">Choose a configured provider</option>
          <option value="legacy">Legacy provider settings</option>
          {(query.data?.items ?? []).map((provider) => (
            <option key={provider.id} value={`profile:${provider.id}`}>
              {provider.name}
              {provider.enabled ? "" : " (disabled)"}
            </option>
          ))}
        </select>
      </label>
      {query.isError && (
        <p role="alert">
          {resolveApiErrorMessage(
            query.error,
            "Provider choices could not be loaded. Existing policy keys are preserved.",
          )}
        </p>
      )}
      {(query.data?.total ?? 0) > 100 && (
        <p className="text-xs">
          Showing the first 100 providers. Add other provider IDs in the
          approved list below.
        </p>
      )}
    </div>
  );
}
