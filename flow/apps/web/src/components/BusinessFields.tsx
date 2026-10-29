import { ENTITY_TYPES } from '../lib/profileDraft'
import { AddressFields } from './AddressFields'
import { FieldShell } from './FieldShell'
import { inputProps, type FormCtx } from './formCtx'

export function BusinessFields({ ctx }: { ctx: FormCtx }) {
  const { draft, set } = ctx
  return (
    <div className="grid gap-5">
      <FieldShell id="f-legalName" label="公司名称" rollKey="legalName" errorKey="legalName" ctx={ctx}>
        <input {...inputProps(ctx, 'f-legalName', 'legalName')} autoComplete="off" value={draft.legalName} onChange={(e) => set({ legalName: e.target.value })} placeholder="Northwind Coffee LLC" />
      </FieldShell>
      <div className="grid gap-5 sm:grid-cols-2">
        <FieldShell id="f-entityType" label="企业类型" rollKey="entityType" errorKey="entityType" ctx={ctx}>
          <select {...inputProps(ctx, 'f-entityType', 'entityType')} value={draft.entityType} onChange={(e) => set({ entityType: e.target.value })}>
            <option value="">请选择</option>
            {ENTITY_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </FieldShell>
        <FieldShell id="f-industry" label="行业" rollKey="industry" errorKey="industry" ctx={ctx}>
          <input {...inputProps(ctx, 'f-industry', 'industry')} autoComplete="off" value={draft.industry} onChange={(e) => set({ industry: e.target.value })} placeholder="Specialty coffee retail" />
        </FieldShell>
      </div>
      <FieldShell id="f-description" label="业务描述" rollKey="description" errorKey="description" ctx={ctx}>
        <textarea {...inputProps(ctx, 'f-description', 'description')} rows={3} value={draft.description} onChange={(e) => set({ description: e.target.value })} placeholder="What the business sells and to whom (English)" />
      </FieldShell>
      <div className="grid gap-5 sm:grid-cols-2">
        <FieldShell id="f-founded" label="成立月份" rollKey="founded" errorKey="founded" ctx={ctx}>
          <input {...inputProps(ctx, 'f-founded', 'founded')} type="month" value={draft.founded} onChange={(e) => set({ founded: e.target.value })} />
        </FieldShell>
        <FieldShell id="f-employeeCount" label="员工人数" rollKey="employeeCount" errorKey="employeeCount" ctx={ctx} hint="0–500">
          <input {...inputProps(ctx, 'f-employeeCount', 'employeeCount')} type="number" inputMode="numeric" value={draft.employeeCount} onChange={(e) => set({ employeeCount: e.target.value })} />
        </FieldShell>
      </div>
      <AddressFields ctx={ctx} />
    </div>
  )
}
