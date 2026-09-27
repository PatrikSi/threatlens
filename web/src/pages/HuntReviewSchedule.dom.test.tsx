// @vitest-environment jsdom
import { act, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch, ApiError } from "../api/client";
import { HuntReviewSchedule } from "./HuntReviewSchedule";
import type { HuntEntry } from "./teamHuntTypes";
import {
  assessmentFixture,
  deferred,
  intelButton,
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
const entry: HuntEntry = {
  assessment_id: "assessment",
  assessment_version: 7,
  team_id: "team-1",
  item_id: "item",
  item_title: "Source",
  status: "pending",
  generated_at: null,
  evidence_age_seconds: 0,
  hunt: assessmentFixture.assessment!.result!.hunts[0],
  claim: { version: 1, owner_user_id: null },
  owner_name: null,
  reviewer_name: null,
  reviewed_at: null,
  can_claim: true,
  can_release: false,
  can_schedule: true,
  investigation: null,
  review_schedule: {
    version: 2,
    priority: "normal",
    due_at: null,
    overdue: false,
    reminded_at: null,
    reminder_acknowledged_at: null,
  },
};
let view: Awaited<ReturnType<typeof mountIntel>> | undefined;
afterEach(() => {
  view?.close();
  view = undefined;
  vi.clearAllMocks();
});
describe("hunt review scheduling", () => {
  it("pins scheduling and assessment revisions while editing and preserves conflict drafts", async () => {
    let refresh!: (entry: HuntEntry) => void;
    function Harness() {
      const [value, setValue] = useState(entry);
      refresh = setValue;
      return <HuntReviewSchedule entry={value} disabled={false} />;
    }
    const pending = deferred<unknown>();
    vi.mocked(apiFetch).mockReturnValue(pending.promise);
    view = await mountIntel(<Harness />);
    const input = view.host.querySelector("input")!;
    act(() => {
      Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )!.set!.call(input, "2026-10-01T12:00");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() =>
      refresh({
        ...entry,
        assessment_version: 8,
        review_schedule: {
          ...entry.review_schedule!,
          version: 3,
          priority: "urgent",
        },
      }),
    );
    act(() => intelButton(view!.host, "Save review schedule").click());
    await settle();
    expect(input.matches(":disabled")).toBe(true);
    expect(
      JSON.parse(String(vi.mocked(apiFetch).mock.calls[0][1]?.body)),
    ).toMatchObject({
      expected_version: 2,
      expected_assessment_version: 7,
      priority: "normal",
    });
    await act(async () =>
      pending.reject(
        new ApiError("Review schedule changed.", 409, "/schedule"),
      ),
    );
    await settle();
    expect(input.value).toBe("2026-10-01T12:00");
    expect(view.host.textContent).toContain("Review schedule changed.");
  });
  it("acknowledges only the current reminder revision and disables repeat clicks", async () => {
    const pending = deferred<unknown>();
    vi.mocked(apiFetch).mockReturnValue(pending.promise);
    view = await mountIntel(
      <HuntReviewSchedule
        entry={{
          ...entry,
          review_schedule: {
            ...entry.review_schedule!,
            reminded_at: "2026-09-26T10:00:00Z",
          },
        }}
        disabled={false}
      />,
    );
    act(() => intelButton(view!.host, "Acknowledge review reminder").click());
    await settle();
    expect(intelButton(view.host, "Acknowledge review reminder").disabled).toBe(
      true,
    );
    expect(
      JSON.parse(String(vi.mocked(apiFetch).mock.calls[0][1]?.body)),
    ).toEqual({ expected_version: 2, expected_assessment_version: 7 });
  });
  it("does not erase a deadline draft when acknowledging a reminder", async () => {
    const schedule = {
      ...entry.review_schedule!,
      reminded_at: "2026-09-26T10:00:00Z",
      reminder_acknowledged_at: "2026-09-27T10:00:00Z",
    };
    vi.mocked(apiFetch).mockResolvedValue(schedule);
    view = await mountIntel(
      <HuntReviewSchedule
        entry={{
          ...entry,
          review_schedule: { ...schedule, reminder_acknowledged_at: null },
        }}
        disabled={false}
      />,
    );
    const input = view.host.querySelector("input")!;
    act(() => {
      Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )!.set!.call(input, "2026-10-02T14:00");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() => {
      intelButton(view!.host, "Acknowledge review reminder").click();
      intelButton(view!.host, "Acknowledge review reminder").click();
    });
    await settle();
    expect(vi.mocked(apiFetch)).toHaveBeenCalledTimes(1);
    expect(input.value).toBe("2026-10-02T14:00");
    expect(view.host.textContent).toContain("Review reminder acknowledged.");
  });
});
