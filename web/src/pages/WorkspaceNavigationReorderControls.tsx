import { GripVertical } from 'lucide-react'
import type { DragEventHandler, KeyboardEvent, ReactNode } from 'react'

export function NavigationDragHandle({
  active,
  count,
  describedBy,
  disabled,
  label,
  onStop,
  onDragEnd,
  onDragStart,
  onMove,
  onToggle,
  position,
}: {
  active: boolean
  count: number
  describedBy: string
  disabled: boolean
  label: string
  onStop: () => void
  onDragEnd: DragEventHandler<HTMLButtonElement>
  onDragStart: DragEventHandler<HTMLButtonElement>
  onMove: (direction: -1 | 1) => void
  onToggle: () => void
  position: number
}) {
  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (!active) return
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      onMove(-1)
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      onMove(1)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      onStop()
    }
  }

  const action = active ? 'Finish reordering' : 'Drag'
  const accessibleName = `${action} ${label}. Position ${position} of ${count}.`

  return (
    <button
      type="button"
      className={`inline-flex h-11 w-11 shrink-0 cursor-grab items-center justify-center rounded border text-slate transition active:cursor-grabbing sm:h-8 sm:w-8 dark:text-slate-300 ${
        active
          ? 'border-cyan bg-cyan/10 text-cyan dark:border-cyan-500/60 dark:text-cyan-100'
          : 'border-slate/20 hover:border-slate/40 hover:bg-slate/5 dark:border-white/10 dark:hover:border-cyan-700/50 dark:hover:bg-white/[0.04]'
      }`}
      aria-label={accessibleName}
      aria-describedby={describedBy}
      aria-pressed={active}
      disabled={disabled}
      draggable={!disabled}
      title={`${action} ${label}`}
      onClick={onToggle}
      onDragEnd={onDragEnd}
      onDragStart={onDragStart}
      onKeyDown={handleKeyDown}
    >
      <GripVertical className="h-4 w-4" aria-hidden="true" />
    </button>
  )
}

export function NavigationOrderButton({
  children,
  disabled,
  label,
  onClick,
}: {
  children: ReactNode
  disabled: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className="inline-flex h-11 w-11 items-center justify-center rounded border border-slate/25 text-slate disabled:opacity-35 sm:h-8 sm:w-8 dark:border-white/10 dark:text-slate-200"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  )
}
