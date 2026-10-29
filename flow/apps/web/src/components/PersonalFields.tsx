import { MARITAL_STATUSES, MAX_HOBBIES } from '../lib/profileDraft'
import { AddressFields } from './AddressFields'
import { FieldShell } from './FieldShell'
import { inputProps, type FormCtx } from './formCtx'
import { TagInput } from './TagInput'

const MARITAL_LABEL: Record<(typeof MARITAL_STATUSES)[number], string> = {
  single: '单身 (single)',
  married: '已婚 (married)',
  partnered: '同居伴侣 (partnered)',
}

export function PersonalFields({ ctx }: { ctx: FormCtx }) {
  const { draft, set } = ctx
  return (
    <div className="grid gap-5">
      <FieldShell id="f-name" label="姓名" rollKey="name" errorKey="name" ctx={ctx}>
        <input {...inputProps(ctx, 'f-name', 'name')} autoComplete="off" value={draft.name} onChange={(e) => set({ name: e.target.value })} placeholder="Jane Doe" />
      </FieldShell>
      <FieldShell id="f-age" label="年龄" rollKey="age" errorKey="age" ctx={ctx} hint="18–90">
        <input {...inputProps(ctx, 'f-age', 'age')} type="number" inputMode="numeric" value={draft.age} onChange={(e) => set({ age: e.target.value })} />
      </FieldShell>
      <AddressFields ctx={ctx} />
      <FieldShell id="f-occupation" label="职业" rollKey="occupation" errorKey="occupation" ctx={ctx}>
        <input {...inputProps(ctx, 'f-occupation', 'occupation')} autoComplete="off" value={draft.occupation} onChange={(e) => set({ occupation: e.target.value })} placeholder="Freelance photographer" />
      </FieldShell>
      <div className="grid gap-5 sm:grid-cols-2">
        <FieldShell id="f-maritalStatus" label="婚姻状况" rollKey="maritalStatus" errorKey="maritalStatus" ctx={ctx}>
          <select {...inputProps(ctx, 'f-maritalStatus', 'maritalStatus')} value={draft.maritalStatus} onChange={(e) => set({ maritalStatus: e.target.value })}>
            <option value="">请选择</option>
            {MARITAL_STATUSES.map((m) => (
              <option key={m} value={m}>
                {MARITAL_LABEL[m]}
              </option>
            ))}
          </select>
        </FieldShell>
        <FieldShell id="f-children" label="子女数量" rollKey="children" errorKey="children" ctx={ctx} hint="0–6">
          <input {...inputProps(ctx, 'f-children', 'children')} type="number" inputMode="numeric" value={draft.children} onChange={(e) => set({ children: e.target.value })} />
        </FieldShell>
      </div>
      <FieldShell id="f-hobbies" label="爱好" rollKey="hobbies" ctx={ctx}>
        <TagInput id="f-hobbies" tags={draft.hobbies} max={MAX_HOBBIES} placeholder="hiking" onChange={(hobbies) => set({ hobbies })} />
      </FieldShell>
    </div>
  )
}
