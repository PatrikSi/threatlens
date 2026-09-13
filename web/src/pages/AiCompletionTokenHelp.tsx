export function AiCompletionTokenHelp({ id }: { id: string }) {
  return (
    <span id={id} className="mt-1 block text-xs text-slate dark:text-white/70">
      Initial output allowance for article enrichment and daily briefs, up to 131,072 tokens. Reports use their own
      completion budget below. Choose a value within the model's output and context limits.
    </span>
  )
}
