export function WebhookArticleTextOption({ checked, disabled, onChange }: {
  checked: boolean
  disabled: boolean
  onChange: (checked: boolean) => void
}) {
  return <div className="space-y-1 rounded border border-slate/25 p-3">
    <label className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-1" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      Include extracted article text
    </label>
    <p className="text-xs">Adds retained plain text to the structured payload, up to 128 KiB, with source revision and explicit availability or truncation status. The destination receives this content. New RSS events may precede article retrieval.</p>
  </div>
}
