import { TaggingDefaultsPanel, TaggingPageHeader, TaggingReapplyPanel } from './TaggingDefaultsPanels'
import { TaggingRuleEditor, TaggingRulePreview, TaggingRulesList } from './TaggingRulePanels'
import { TaggingSettingsDialogs } from './TaggingSettingsDialogs'
import { useTaggingSettingsController } from './useTaggingSettingsController'

export function TaggingSettingsPage() {
  const controller = useTaggingSettingsController()

  return (
    <div className="space-y-4">
      <TaggingPageHeader controller={controller} />
      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <TaggingDefaultsPanel controller={controller} />
        <TaggingReapplyPanel controller={controller} />
      </div>
      <div className="grid gap-4 xl:grid-cols-[320px_1fr]">
        <TaggingRulesList controller={controller} />
        <div className="space-y-4">
          <TaggingRuleEditor controller={controller} />
          <TaggingRulePreview controller={controller} />
        </div>
      </div>
      <TaggingSettingsDialogs controller={controller} />
    </div>
  )
}
