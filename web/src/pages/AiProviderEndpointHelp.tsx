export function AiProviderEndpointHelp({ id, legacy = false }: { id: string; legacy?: boolean }) {
  return (
    <span id={id} className="mt-1 block text-xs text-slate dark:text-white/70">
      Gemini compatibility base:{' '}
      <code className="break-all">https://generativelanguage.googleapis.com/v1beta/openai/</code>. Native{' '}
      <code>:generateContent</code> endpoints use a different request format and cannot be used here. Enter the model
      identifier separately.
      {legacy && (
        <span className="mt-1 block">
          The environment API key is limited to the origin set by <code>AI_API_KEY_BASE_URL</code>. Named providers use
          their own saved keys.
        </span>
      )}
    </span>
  )
}
