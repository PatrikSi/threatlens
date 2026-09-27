// @vitest-environment jsdom
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, ApiError } from "../api/client";
import { TeamAIGovernance } from "./TeamAIGovernance";
import {
  deferred,
  editIntel,
  intelButton,
  intelField,
  mountIntel,
  settle,
} from "./articleIntelligenceTestSupport";
(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("../api/client", async (original) => ({
  ...(await original<object>()),
  apiFetch: vi.fn(),
}));
const policy = {
  team_id: "team-1",
  version: 2,
  configured: true,
  approved_provider_keys: ["legacy"],
  selected_provider_key: "legacy",
  label_destinations: {},
  destinations: [
    { key: "legacy", name: "Legacy provider settings", available: true },
  ],
  can_manage: true,
  can_approve: true,
};
let view: Awaited<ReturnType<typeof mountIntel>> | undefined;
beforeEach(() => {
  vi.mocked(apiFetch).mockResolvedValue(policy);
});
afterEach(() => {
  view?.close();
  view = undefined;
  vi.clearAllMocks();
});
describe("team AI destination governance", () => {
  it("retains the draft baseline across refresh and rejects stale saves without erasing edits", async () => {
    let current = policy;
    const pending = deferred<typeof policy>();
    vi.mocked(apiFetch).mockImplementation((_path, init) =>
      init?.method ? pending.promise : Promise.resolve(current),
    );
    view = await mountIntel(<TeamAIGovernance teamId="team-1" admin />);
    editIntel(
      view.host,
      "Approved provider keys",
      "legacy\nprofile:00000000-0000-4000-8000-000000000123",
    );
    current = { ...policy, version: 3, approved_provider_keys: [] };
    await act(async () => {
      await view!.client.invalidateQueries({
        queryKey: ["teams", "ai-governance"],
      });
    });
    await settle();
    expect(intelField(view.host, "Approved provider keys").value).toContain(
      "000000000123",
    );
    act(() => intelButton(view!.host, "Approve destination policy").click());
    await settle();
    expect(
      intelField(view.host, "Approved provider keys").matches(":disabled"),
    ).toBe(true);
    const sent = vi
      .mocked(apiFetch)
      .mock.calls.find(([, init]) => init?.method === "PUT")!;
    expect(JSON.parse(String(sent[1]?.body)).expected_version).toBe(2);
    await act(async () =>
      pending.reject(
        new ApiError("Reload the current destination policy.", 409, sent[0]),
      ),
    );
    await settle();
    expect(intelField(view.host, "Approved provider keys").value).toContain(
      "000000000123",
    );
    act(() => {
      void view!.router.navigate("/other");
    });
    await settle();
    expect(
      document.querySelector('[role="alertdialog"]')?.textContent,
    ).toContain("Discard unsaved AI destination policy?");
  });
  it("hides cached policy after confirmed membership loss", async () => {
    view = await mountIntel(<TeamAIGovernance teamId="team-1" />);
    expect(view.host.textContent).toContain("Legacy provider settings");
    vi.mocked(apiFetch).mockRejectedValue(
      new ApiError("Membership changed.", 404, "/teams/team-1/ai-governance"),
    );
    await act(async () => {
      await view!.client.invalidateQueries({
        queryKey: ["teams", "ai-governance"],
      });
    });
    await settle();
    expect(view.host.textContent).not.toContain("Legacy provider settings");
    expect(view.host.textContent).toContain("Membership changed");
  });
  it("edits named handling restrictions without parsing JSON and validates removed destinations", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path) => path === '/iam/data-policies'
      ? { labels: [{ id: 'label-1', name: 'Restricted', is_active: true }] } : policy);
    view = await mountIntel(<TeamAIGovernance teamId="team-1" admin />);
    await act(async () => {
      await vi.waitFor(() => expect(view!.host.querySelector('option[value="label-1"]')).not.toBeNull());
    });
    const select = [...view.host.querySelectorAll('label')].find((label) => label.textContent?.startsWith('Restrict a handling label'))!.control as HTMLSelectElement;
    act(() => { select.value = 'label-1'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    const restriction = [...view.host.querySelectorAll('fieldset')].find((fieldset) => fieldset.querySelector('legend')?.textContent === 'Restricted')!;
    expect(restriction.textContent).toContain('Legacy provider settings');
    act(() => (restriction.querySelector('input') as HTMLInputElement).click());
    expect(restriction.textContent).toContain('AI requests are blocked for this label.');
    act(() => intelButton(view!.host, 'Approve destination policy').click());
    await settle();
    const sent = vi.mocked(apiFetch).mock.calls.find(([, init]) => init?.method === 'PUT')!;
    expect(JSON.parse(String(sent[1]?.body)).label_destinations).toEqual({ 'label-1': [] });
    editIntel(view.host, 'Approved provider keys', 'invalid provider');
    expect(intelButton(view.host, 'Approve destination policy').disabled).toBe(true);
    expect(view.host.textContent).toContain('valid legacy/profile provider key');
  });
});
