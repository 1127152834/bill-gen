import { useState } from 'react'

type Props = {
  id: string
  tags: string[]
  max: number
  placeholder?: string
  onChange: (tags: string[]) => void
}

/** Free-text tags: Enter, comma or leaving the field adds one; the × removes it. */
export function TagInput({ id, tags, max, placeholder, onChange }: Props) {
  const [text, setText] = useState('')

  /** Adds every non-empty, new piece (up to `max` in all) in one update, so a pasted list is not lost. */
  const commit = (...pieces: string[]) => {
    const next = [...tags]
    for (const piece of pieces) {
      const tag = piece.trim()
      if (tag !== '' && !next.includes(tag) && next.length < max) next.push(tag)
    }
    setText('')
    if (next.length !== tags.length) onChange(next)
  }

  return (
    <div>
      <div className="input flex flex-wrap items-center gap-1.5 focus-within:outline-2 focus-within:outline-accent">
        {tags.map((tag) => (
          <span key={tag} className="chip">
            {tag}
            <button
              type="button"
              aria-label={`移除 ${tag}`}
              className="grid size-5 place-items-center rounded-full text-base leading-none hover:bg-accent/15"
              onClick={() => onChange(tags.filter((t) => t !== tag))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          id={id}
          value={text}
          disabled={tags.length >= max}
          placeholder={tags.length === 0 ? placeholder : undefined}
          className="min-w-24 flex-1 bg-transparent py-0.5 outline-none"
          onChange={(e) => {
            const value = e.target.value
            if (/[,，]/.test(value)) {
              commit(...value.split(/[,，]/))
            } else {
              setText(value)
            }
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commit(text)
            } else if (e.key === 'Backspace' && text === '' && tags.length > 0) {
              onChange(tags.slice(0, -1))
            }
          }}
          onBlur={() => commit(text)}
        />
      </div>
      <p className="mt-1 text-xs text-muted">
        回车或逗号添加，最多 {max} 个（{tags.length}/{max}）
      </p>
    </div>
  )
}
