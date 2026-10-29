import { US_STATES } from '../lib/usStates'
import { DiceButton, RollFailure } from './FieldShell'
import { errorFor, inputProps, type FormCtx } from './formCtx'

function Sub({ id, label, ctx, errorKey, className, children }: {
  id: string
  label: string
  ctx: FormCtx
  errorKey: 'street' | 'city' | 'state' | 'zip'
  className?: string
  children: React.ReactNode
}) {
  const error = errorFor(ctx, errorKey)
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">
        {label}
      </label>
      {children}
      {error && (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      )}
    </div>
  )
}

/** Street, city, state and zip. One 🎲 for the group: the four parts have to agree with each other. */
export function AddressFields({ ctx }: { ctx: FormCtx }) {
  const { draft, set } = ctx
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <div className="mb-1 flex items-center justify-between gap-2">
        <legend className="field-label float-left">地址</legend>
        <DiceButton label="地址" rollKey="address" ctx={ctx} />
      </div>
      <div className="clear-both grid grid-cols-6 gap-x-3 gap-y-2">
        <Sub id="f-street" label="街道" ctx={ctx} errorKey="street" className="col-span-6">
          <input {...inputProps(ctx, 'f-street', 'street')} autoComplete="off" value={draft.street} onChange={(e) => set({ street: e.target.value })} placeholder="123 Main St" />
        </Sub>
        <Sub id="f-city" label="城市" ctx={ctx} errorKey="city" className="col-span-6 sm:col-span-3">
          <input {...inputProps(ctx, 'f-city', 'city')} autoComplete="off" value={draft.city} onChange={(e) => set({ city: e.target.value })} />
        </Sub>
        <Sub id="f-state" label="州" ctx={ctx} errorKey="state" className="col-span-3 sm:col-span-1">
          <select {...inputProps(ctx, 'f-state', 'state')} value={draft.state} onChange={(e) => set({ state: e.target.value })}>
            <option value="">请选择</option>
            {US_STATES.map(([code, name]) => (
              <option key={code} value={code} title={name}>
                {code}
              </option>
            ))}
          </select>
        </Sub>
        <Sub id="f-zip" label="邮编" ctx={ctx} errorKey="zip" className="col-span-3 sm:col-span-2">
          <input {...inputProps(ctx, 'f-zip', 'zip')} inputMode="numeric" maxLength={10} autoComplete="off" value={draft.zip} onChange={(e) => set({ zip: e.target.value })} placeholder="12345" />
        </Sub>
      </div>
      <RollFailure ctx={ctx} rollKey="address" />
    </fieldset>
  )
}
