import type {
  WebhookCondition,
  WebhookConditionGroup,
} from '../types/webhookAutomation'
import { CONDITION_FIELDS } from './webhookConditionModel'

const INPUT =
  'rounded border border-slate/30 bg-white p-2 text-sm dark:bg-[#072019]'

export function ConditionRow({
  value,
  onChange,
}: {
  value: Exclude<WebhookCondition, WebhookConditionGroup>
  onChange: (value: WebhookCondition) => void
}) {
  const field = CONDITION_FIELDS.find((entry) => entry.value === value.field)!
  return (
    <div className="space-y-1">
      <div className="grid gap-2 md:grid-cols-[1fr_1fr_2fr]">
        <label className="text-xs">
          Field
          <select
            className={`${INPUT} block w-full`}
            value={value.field}
            onChange={(event) => {
              const selected = CONDITION_FIELDS.find(
                (entry) => entry.value === event.target.value,
              )!
              onChange({
                field: selected.value,
                operator: selected.numeric ? 'gte' : 'in',
                value: selected.numeric ? 0 : [''],
              })
            }}
          >
            {CONDITION_FIELDS.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          Comparison
          <select
            className={`${INPUT} block w-full`}
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
        <label className="text-xs">
          {field.numeric ? 'Threshold' : 'Values'}
          <input
            className={`${INPUT} block w-full`}
            type={field.numeric ? 'number' : 'text'}
            min={0}
            max={
              field.numeric
                ? value.field === 'freshness_seconds'
                  ? 31_536_000
                  : 1
                : undefined
            }
            step="any"
            value={
              Array.isArray(value.value)
                ? value.value.join(',')
                : Number.isFinite(value.value)
                  ? value.value
                  : ''
            }
            onChange={(event) =>
              onChange({
                ...value,
                value: field.numeric
                  ? event.target.value === ''
                    ? Number.NaN
                    : Number(event.target.value)
                  : event.target.value.split(','),
              })
            }
          />
        </label>
      </div>
      <p className="text-xs text-slate dark:text-slate-300">{field.hint}</p>
    </div>
  )
}
