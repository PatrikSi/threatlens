import type { ArticleExportCapabilities } from '../types/api'
import { formatByteSize } from './exportPageModel'
import type { ExportPageController } from './useExportPageController'

interface ExportFormatPanelProps {
  capabilities: ArticleExportCapabilities
  controller: ExportPageController
}

const SELECT_CLASS =
  'mt-1 w-full rounded border border-slate/30 bg-white px-2.5 py-2 text-sm dark:border-cyan-900/40 dark:bg-[#072019]'

export function ExportFormatPanel({ capabilities, controller }: ExportFormatPanelProps) {
  const { format, setFormat, options, updateOptions } = controller
  const capability = capabilities.formats.find((entry) => entry.id === format) ?? capabilities.formats[0]

  return (
    <section className="rounded-lg border border-slate/20 bg-white/80 dark:border-cyan-900/40 dark:bg-[#041612]/90">
      <header className="border-b border-slate/15 px-3 py-3 dark:border-white/10 sm:px-4">
        <h2 className="font-display text-lg">Format and contents</h2>
        <p className="mt-0.5 text-xs text-slate dark:text-slate-400">
          Limit: {format === 'pdf_bundle' ? capabilities.max_pdf_items : capabilities.max_items.toLocaleString()} articles |{' '}
          {formatByteSize(capabilities.max_uncompressed_bytes)} uncompressed
        </p>
      </header>

      <div className="space-y-4 p-3 sm:p-4">
        <fieldset>
          <legend className="sr-only">Export format</legend>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {capabilities.formats.map((entry) => (
              <label
                key={entry.id}
                className={`min-w-0 rounded border p-3 transition ${
                  entry.id === format
                    ? 'border-cyan/50 bg-cyan/10 text-cyan-950 dark:border-cyan-500/40 dark:bg-cyan-500/10 dark:text-cyan-50'
                    : 'border-slate/20 bg-white/40 text-ink hover:border-slate/40 dark:border-cyan-900/35 dark:bg-white/[0.02] dark:text-slate-100'
                }`}
              >
                <span className="flex items-start gap-2">
                  <input
                    type="radio"
                    name="article-export-format"
                    value={entry.id}
                    checked={entry.id === format}
                    onChange={() => setFormat(entry.id)}
                    className="mt-0.5 h-4 w-4 shrink-0 accent-cyan"
                  />
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-baseline justify-between gap-x-2">
                      <span className="font-semibold">{entry.label}</span>
                      <span className="font-mono text-[11px] text-slate dark:text-slate-400">{entry.extension}</span>
                    </span>
                    <span className="mt-1 block text-xs leading-5 text-slate dark:text-slate-300">{entry.description}</span>
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {capability && (
          <fieldset>
            <legend className="text-xs font-bold uppercase text-slate dark:text-slate-300">Included data</legend>
            <div className="mt-1 grid gap-1.5 sm:grid-cols-2">
              {capability.supports_article_text && format !== 'pdf_bundle' && (
                <OptionToggle
                  label="Extracted article text"
                  checked={options.include_article_text}
                  onChange={(include_article_text) => updateOptions({ include_article_text })}
                />
              )}
              {format === 'pdf_bundle' && (
                <OptionToggle
                  label="Full article text in PDFs"
                  checked={options.pdf_include_article_text}
                  onChange={(pdf_include_article_text) => updateOptions({ pdf_include_article_text })}
                />
              )}
              <OptionToggle
                label="AI scores, reasons, and summary"
                checked={options.include_ai_details}
                onChange={(include_ai_details) => updateOptions({ include_ai_details })}
              />
              <OptionToggle
                label="Tag metadata"
                checked={options.include_tag_metadata}
                onChange={(include_tag_metadata) => updateOptions({ include_tag_metadata })}
              />
              {capability.supports_iocs && (
                <OptionToggle
                  label="Extracted IOCs"
                  checked={options.include_iocs}
                  onChange={(include_iocs) => updateOptions({ include_iocs })}
                />
              )}
              {format === 'threat_bundle' && options.include_iocs && (
                <OptionToggle
                  label="Separate iocs.csv"
                  checked={options.include_ioc_csv}
                  onChange={(include_ioc_csv) => updateOptions({ include_ioc_csv })}
                />
              )}
              {capability.supports_user_state && (
                <OptionToggle
                  label="My read and starred state"
                  checked={options.include_user_state}
                  onChange={(include_user_state) => updateOptions({ include_user_state })}
                />
              )}
              {capability.supports_user_state && (
                <OptionToggle
                  label="My private notes"
                  checked={options.include_user_notes}
                  disabled={!options.include_user_state}
                  onChange={(include_user_notes) => updateOptions({ include_user_notes })}
                />
              )}
            </div>
          </fieldset>
        )}

        {format === 'stix' && (
          <label className="block text-xs font-semibold text-slate dark:text-slate-300">
            Traffic Light Protocol marking
            <select
              className={SELECT_CLASS}
              value={options.stix_marking}
              onChange={(event) => updateOptions({ stix_marking: event.target.value as typeof options.stix_marking })}
            >
              <option value="none">No marking</option>
              <option value="TLP:WHITE">TLP:WHITE</option>
              <option value="TLP:GREEN">TLP:GREEN</option>
              <option value="TLP:AMBER">TLP:AMBER</option>
              <option value="TLP:RED">TLP:RED</option>
            </select>
          </label>
        )}

        {format === 'misp' && (
          <label className="block text-xs font-semibold text-slate dark:text-slate-300">
            MISP distribution
            <select
              className={SELECT_CLASS}
              value={options.misp_distribution}
              onChange={(event) => updateOptions({ misp_distribution: Number(event.target.value) })}
            >
              <option value={0}>Your organisation only</option>
              <option value={1}>This community only</option>
              <option value={2}>Connected communities</option>
              <option value={3}>All communities</option>
            </select>
            <span className="mt-1 block font-normal text-slate dark:text-slate-400">Events remain unpublished in the export file.</span>
          </label>
        )}

        <label className="block text-xs font-semibold text-slate dark:text-slate-300">
          Filename prefix
          <input
            className={SELECT_CLASS}
            value={options.filename_prefix ?? ''}
            onChange={(event) => updateOptions({ filename_prefix: event.target.value || null })}
            maxLength={80}
            placeholder="threatlens-export"
          />
        </label>
      </div>
    </section>
  )
}

function OptionToggle({
  label,
  checked,
  onChange,
  disabled = false,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}) {
  return (
    <label
      className={`flex min-h-10 items-center gap-2 rounded border border-slate/15 px-2.5 py-2 text-sm dark:border-white/10 ${
        disabled ? 'opacity-50' : 'hover:bg-slate/5 dark:hover:bg-white/[0.04]'
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 shrink-0 accent-cyan"
      />
      <span>{label}</span>
    </label>
  )
}
