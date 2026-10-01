import { useId, useRef } from 'react'
import type {
  WebhookCondition,
  WebhookConditionGroup,
} from '../types/webhookAutomation'
import {
  countConditions,
  newCondition,
  validateConditions,
  isIndicatorGroup,
} from './webhookConditionModel'
import { ConditionRow } from './WebhookConditionRow'

const INPUT =
  'rounded border border-slate/30 bg-white p-2 text-sm dark:bg-[#072019]'
const BUTTON =
  'rounded border border-slate/30 px-3 py-1.5 text-sm disabled:opacity-50'

export function WebhookConditionBuilder({
  value,
  onChange,
  disabled,
}: {
  value: WebhookConditionGroup | null
  onChange: (value: WebhookConditionGroup | null) => void
  disabled: boolean
}) {
  const validation = validateConditions(value)
  return (
    <fieldset disabled={disabled} className="min-w-0 space-y-2">
      <legend className="font-semibold">Event conditions</legend>
      <p className="text-xs">
        These conditions narrow the selected event and feed scope. Missing
        evidence does not match, including exclusions.
      </p>
      {value ? (
        <>
          <ConditionGroup
            value={value}
            onChange={onChange}
            depth={1}
            remaining={32 - countConditions(value)}
          />
          <button
            type="button"
            className={BUTTON}
            onClick={() => onChange(null)}
          >
            Remove all conditions
          </button>
        </>
      ) : (
        <button
          type="button"
          className={BUTTON}
          onClick={() => onChange({ op: 'all', conditions: [newCondition()] })}
        >
          Add event conditions
        </button>
      )}
      {validation && (
        <p role="alert" className="text-sm text-red-600">
          {validation}
        </p>
      )}
    </fieldset>
  )
}

function ConditionGroup({
  value,
  onChange,
  depth,
  remaining,
  indicatorScope = false,
}: {
  value: WebhookConditionGroup
  onChange: (value: WebhookConditionGroup) => void
  depth: number
  remaining: number
  indicatorScope?: boolean
}) {
  const id = useId()
  const groupRef = useRef<HTMLDivElement>(null)
  const replace = (index: number, node: WebhookCondition) =>
    onChange({
      ...value,
      conditions: value.conditions.map((entry, candidate) =>
        candidate === index ? node : entry,
      ),
    })
  const wrapsChildren = value.conditions.length > 1
  const canExclude =
    !wrapsChildren || (remaining >= 1 && depth + conditionDepth(value) <= 4)
  const remove = (index: number) => {
    onChange({
      ...value,
      conditions: value.conditions.filter(
        (_, candidate) => candidate !== index,
      ),
    })
    requestAnimationFrame(() =>
      groupRef.current?.querySelector<HTMLSelectElement>('select')?.focus(),
    )
  }
  return (
    <div
      ref={groupRef}
      className="min-w-0 space-y-2 rounded border border-slate/25 p-3"
    >
      <label htmlFor={id} className="mr-2 text-sm">
        Match
      </label>
      <select
        id={id}
        className={`${INPUT} max-w-full`}
        value={value.op}
        onChange={(event) => {
          const op = event.target.value as WebhookConditionGroup['op']
          if (op === 'not' && !canExclude) return
          onChange({
            op,
            conditions:
              op === 'not' && value.conditions.length > 1
                ? [{ op: 'all', conditions: value.conditions }]
                : value.conditions,
          })
        }}
      >
        <option value="all">All conditions (AND)</option>
        <option value="any">Any condition (OR)</option>
        {!indicatorScope && <>
          <option value="indicators_any">Any eligible indicator matches all conditions</option>
          <option value="indicators_all">Every eligible indicator matches all conditions</option>
        </>}
        <option value="not" disabled={!canExclude}>
          Exclude matching condition (NOT)
        </option>
      </select>
      {isIndicatorGroup(value) && (
        <p className="text-xs">Conditions below apply to the same indicator. Excluded indicators cannot match. The event payload remains complete.</p>
      )}
      {value.conditions.map((node, index) => (
        <div key={index} className="min-w-0 space-y-2">
          {'conditions' in node ? (
            <ConditionGroup
              value={node}
              onChange={(next) => replace(index, next)}
              depth={depth + 1}
              remaining={remaining}
              indicatorScope={indicatorScope || isIndicatorGroup(value)}
            />
          ) : (
            <ConditionRow
              indicatorScope={indicatorScope || isIndicatorGroup(value)}
              value={node}
              onChange={(next) => replace(index, next)}
            />
          )}
          <button
            type="button"
            className={BUTTON}
            aria-label={`Remove condition ${index + 1}`}
            onClick={() => remove(index)}
          >
            Remove condition
          </button>
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className={BUTTON}
          disabled={
            remaining < 1 ||
            (value.op === 'not' && value.conditions.length > 0) ||
            depth >= 4
          }
          onClick={() =>
            onChange({
              ...value,
              conditions: [...value.conditions, newCondition()],
            })
          }
        >
          Add condition
        </button>
        <button
          type="button"
          className={BUTTON}
          disabled={
            remaining < 2 ||
            depth >= 3 ||
            (value.op === 'not' && value.conditions.length > 0)
          }
          onClick={() =>
            onChange({
              ...value,
              conditions: [
                ...value.conditions,
                { op: 'all', conditions: [newCondition()] },
              ],
            })
          }
        >
          Add group
        </button>
      </div>
    </div>
  )
}

function conditionDepth(node: WebhookCondition): number {
  return 'conditions' in node
    ? 1 + Math.max(0, ...node.conditions.map(conditionDepth))
    : 1
}
