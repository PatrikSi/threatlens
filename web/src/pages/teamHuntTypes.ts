import type { HuntSuggestion } from "../types/articleIntelligence";
export interface HuntEntry {
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
  review_schedule?: {
    version: number;
    priority: "low" | "normal" | "high" | "urgent";
    due_at: string | null;
    overdue: boolean;
    reminded_at: string | null;
    reminder_acknowledged_at: string | null;
  };
  can_schedule?: boolean;
  investigation: {
    id: string;
    status: string;
    disposition: string | null;
    assignee_user_id: string | null;
  } | null;
}
