import type {
  WebhookCondition,
  WebhookConditionGroup,
} from '../types/webhookAutomation'
import { WebhookConditionValues } from './WebhookConditionValues'
import { CONDITION_FIELDS, INDICATOR_FIELDS } from './webhookConditionModel'

const INPUT =
  'rounded border border-slate/30 bg-white p-2 text-sm dark:bg-[#072019]'

export function ConditionRow({
  value,
  onChange,
  indicatorScope = false,
}: {
  value: Exclude<WebhookCondition, WebhookConditionGroup>
  onChange: (value: WebhookCondition) => void
  indicatorScope?: boolean
}) {
  const field = CONDITION_FIELDS.find((entry) => entry.value === value.field)!
  return (
    <div className="space-y-1">
      <div className="grid min-w-0 gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,2fr)]">
        <label className="min-w-0 text-xs">
          Field
          <select
            className={`${INPUT} block w-full min-w-0`}
            value={value.field}
            onChange={(event) => {
              const selected = CONDITION_FIELDS.find(
                (entry) => entry.value === event.target.value,
              )!
              onChange({
                field: selected.value,
                operator: selected.value === 'freshness_seconds' ? 'lte' : selected.numeric ? 'gte' : 'in',
                value: selected.value === 'freshness_seconds' ? 86400 : selected.numeric ? 0.8 : [],
              })
            }}
          >
            {CONDITION_FIELDS.map((entry) => (
              <option key={entry.value} value={entry.value} disabled={indicatorScope && !INDICATOR_FIELDS.has(entry.value)}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        <label className="min-w-0 text-xs">
          Comparison
          <select
            className={`${INPUT} block w-full min-w-0`}
            value={value.operator}
            onChange={(event) =>
              onChange({
                ...value,
                operator: event.target.value as typeof value.operator,
              })
            }
          >
            {field.numeric ? (
              <>
                <option value="gte">At least</option>
                <option value="lte">At most</option>
              </>
            ) : (
              <>
                <option value="in">Matches any value</option>
                <option value="not_in">Matches none</option>
              </>
            )}
          </select>
        </label>
        {field.numeric ? <label className="min-w-0 text-xs">
          Threshold
          <input
            className={`${INPUT} block w-full min-w-0`}
            type="number"
            min={0}
            max={value.field === 'freshness_seconds' ? 31_536_000 : 1}
            step="any"
            value={typeof value.value === 'number' && Number.isFinite(value.value) ? value.value : ''}
            onChange={(event) => onChange({ ...value, value: event.target.value === '' ? Number.NaN : Number(event.target.value) })}
          />
        </label> : <WebhookConditionValues
          key={value.field}
          field={value.field}
          values={Array.isArray(value.value) ? value.value : []}
          onChange={(values) => onChange({ ...value, value: values })}
        />}
      </div>
      <p className="text-xs text-slate dark:text-slate-300">{indicatorScope && field.numeric ? '0–1 for this individual indicator; missing confidence does not match.' : field.hint}</p>
    </div>
  )
}
