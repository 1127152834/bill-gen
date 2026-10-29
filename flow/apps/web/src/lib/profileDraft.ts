import {
  BusinessProfileSchema,
  PersonalProfileSchema,
  type BusinessProfile,
  type PersonalProfile,
  type Profile,
  type SubjectType,
} from '@bh/core/schemas'
import { STATE_CODES } from './usStates'

/** The profile as typed into the form: numbers stay strings until they are validated. */
export type Draft = {
  name: string
  age: string
  occupation: string
  maritalStatus: string
  children: string
  hobbies: string[]
  legalName: string
  entityType: string
  industry: string
  description: string
  founded: string
  employeeCount: string
  street: string
  city: string
  state: string
  zip: string
}

export const EMPTY_DRAFT: Draft = {
  name: '',
  age: '',
  occupation: '',
  maritalStatus: '',
  children: '',
  hobbies: [],
  legalName: '',
  entityType: '',
  industry: '',
  description: '',
  founded: '',
  employeeCount: '',
  street: '',
  city: '',
  state: '',
  zip: '',
}

/** What a 🎲 button rerolls. `address` is one unit: city, state and zip have to agree with each other. */
export type FieldKey =
  | 'name' | 'age' | 'occupation' | 'maritalStatus' | 'children' | 'hobbies'
  | 'legalName' | 'entityType' | 'industry' | 'description' | 'founded' | 'employeeCount'
  | 'address'

export const FIELD_KEYS: Record<SubjectType, readonly FieldKey[]> = {
  personal: ['name', 'age', 'address', 'occupation', 'maritalStatus', 'children', 'hobbies'],
  business: ['legalName', 'entityType', 'industry', 'description', 'founded', 'employeeCount', 'address'],
}

/** An input that can carry its own error message (the address group has four). */
export type InputKey = Exclude<FieldKey, 'address' | 'hobbies'> | 'street' | 'city' | 'state' | 'zip'

const INPUTS_OF: Record<FieldKey, readonly InputKey[]> = {
  name: ['name'], age: ['age'], occupation: ['occupation'], maritalStatus: ['maritalStatus'], children: ['children'],
  hobbies: [], legalName: ['legalName'], entityType: ['entityType'], industry: ['industry'],
  description: ['description'], founded: ['founded'], employeeCount: ['employeeCount'],
  address: ['street', 'city', 'state', 'zip'],
}

export type InputError = { kind: 'missing' | 'invalid'; message: string }
export type InputErrors = Partial<Record<InputKey, InputError>>

const INT = /^\d+$/
const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/
export const MARITAL_STATUSES = ['single', 'married', 'partnered'] as const
export const ENTITY_TYPES = ['LLC', 'S-Corp', 'Sole Prop'] as const
export const MAX_HOBBIES = 8

const missing = (message = '此项必填'): InputError => ({ kind: 'missing', message })
const invalid = (message: string): InputError => ({ kind: 'invalid', message })

function intIn(value: string, min: number, max: number, message: string): InputError | undefined {
  if (value.trim() === '') return missing()
  const n = Number(value)
  return INT.test(value.trim()) && n >= min && n <= max ? undefined : invalid(message)
}

function oneOf(value: string, allowed: readonly string[], emptyMessage: string): InputError | undefined {
  if (value === '') return missing(emptyMessage)
  return allowed.includes(value) ? undefined : invalid(emptyMessage)
}

function text(value: string): InputError | undefined {
  return value.trim() === '' ? missing() : undefined
}

const CHECKS: Record<InputKey, (d: Draft) => InputError | undefined> = {
  name: (d) => text(d.name),
  age: (d) => intIn(d.age, 18, 90, '年龄必须是 18 到 90 之间的整数'),
  occupation: (d) => text(d.occupation),
  maritalStatus: (d) => oneOf(d.maritalStatus, MARITAL_STATUSES, '请选择婚姻状况'),
  children: (d) => intIn(d.children, 0, 6, '子女数量必须是 0 到 6 之间的整数'),
  legalName: (d) => text(d.legalName),
  entityType: (d) => oneOf(d.entityType, ENTITY_TYPES, '请选择企业类型'),
  industry: (d) => text(d.industry),
  description: (d) => text(d.description),
  founded: (d) =>
    d.founded === '' ? missing('请选择成立月份') : MONTH.test(d.founded) ? undefined : invalid('成立月份格式不正确'),
  employeeCount: (d) => intIn(d.employeeCount, 0, 500, '员工人数必须是 0 到 500 之间的整数'),
  street: (d) => text(d.street),
  city: (d) => text(d.city),
  state: (d) => oneOf(d.state, [...STATE_CODES], '请选择州'),
  zip: (d) => (d.zip.trim() === '' ? missing() : /^\d{5}$/.test(d.zip.trim()) ? undefined : invalid('邮编必须是 5 位数字')),
}

const INPUT_KEYS_OF: Record<SubjectType, readonly InputKey[]> = {
  personal: FIELD_KEYS.personal.flatMap((k) => INPUTS_OF[k]),
  business: FIELD_KEYS.business.flatMap((k) => INPUTS_OF[k]),
}

export function inputErrors(subject: SubjectType, draft: Draft): InputErrors {
  const errors: InputErrors = {}
  for (const key of INPUT_KEYS_OF[subject]) {
    const error = CHECKS[key](draft)
    if (error) errors[key] = error
  }
  return errors
}

export const hasErrors = (errors: InputErrors): boolean => Object.keys(errors).length > 0

const groupOk = (key: FieldKey, errors: InputErrors) => INPUTS_OF[key].every((i) => !errors[i])

function address(d: Draft) {
  return { street: d.street.trim(), city: d.city.trim(), state: d.state, zip: d.zip.trim() }
}

/**
 * The fields to hand the persona route as `partial`: every field that is filled in and valid, except
 * the one being rerolled. The server rejects a partial with a bad field, so unfinished ones stay out.
 */
export function buildPartial(subject: SubjectType, d: Draft, exclude?: FieldKey): Record<string, unknown> {
  const errors = inputErrors(subject, d)
  const wants = (key: FieldKey) => key !== exclude && groupOk(key, errors)
  const partial: Record<string, unknown> = {}
  if (subject === 'personal') {
    if (wants('name')) partial.name = d.name.trim()
    if (wants('age')) partial.age = Number(d.age)
    if (wants('address')) partial.address = address(d)
    if (wants('occupation')) partial.occupation = d.occupation.trim()
    // `household` is one object on the wire: it goes along only when both halves are usable.
    if (wants('maritalStatus') && wants('children')) {
      partial.household = { maritalStatus: d.maritalStatus, children: Number(d.children) }
    }
    if (wants('hobbies') && d.hobbies.length > 0) partial.hobbies = d.hobbies
  } else {
    if (wants('legalName')) partial.legalName = d.legalName.trim()
    if (wants('entityType')) partial.entityType = d.entityType
    if (wants('industry')) partial.industry = d.industry.trim()
    if (wants('description')) partial.description = d.description.trim()
    if (wants('founded')) partial.founded = d.founded
    if (wants('employeeCount')) partial.employeeCount = Number(d.employeeCount)
    if (wants('address')) partial.address = address(d)
  }
  return partial
}

const isPersonal = (p: Profile): p is PersonalProfile => 'name' in p
const isBusiness = (p: Profile): p is BusinessProfile => 'legalName' in p

function addressFields(p: Profile): Pick<Draft, 'street' | 'city' | 'state' | 'zip'> {
  return { street: p.address.street, city: p.address.city, state: p.address.state, zip: p.address.zip }
}

/** The draft slice for one rerolled field, taken from a generated profile. */
function fieldFrom(key: FieldKey, p: Profile): Partial<Draft> {
  if (key === 'address') return addressFields(p)
  if (isPersonal(p)) {
    switch (key) {
      case 'name': return { name: p.name }
      case 'age': return { age: String(p.age) }
      case 'occupation': return { occupation: p.occupation }
      case 'maritalStatus': return { maritalStatus: p.household.maritalStatus }
      case 'children': return { children: String(p.household.children) }
      case 'hobbies': return { hobbies: [...p.hobbies] }
    }
  }
  if (isBusiness(p)) {
    switch (key) {
      case 'legalName': return { legalName: p.legalName }
      case 'entityType': return { entityType: p.entityType }
      case 'industry': return { industry: p.industry }
      case 'description': return { description: p.description }
      case 'founded': return { founded: p.founded }
      case 'employeeCount': return { employeeCount: String(p.employeeCount) }
    }
  }
  return {}
}

/** Replaces exactly one field (or the address group) with the generated value; the rest is untouched. */
export function applyField(d: Draft, key: FieldKey, p: Profile): Draft {
  return { ...d, ...fieldFrom(key, p) }
}

/** "全部随机": every field of the generated profile replaces the form. */
export function draftFromProfile(subject: SubjectType, d: Draft, p: Profile): Draft {
  return FIELD_KEYS[subject].reduce((acc, key) => applyField(acc, key, p), d)
}

/** The validated profile, or `undefined` while the form is incomplete or invalid. */
export function buildProfile(subject: SubjectType, d: Draft): Profile | undefined {
  if (hasErrors(inputErrors(subject, d))) return undefined
  if (subject === 'personal') {
    const parsed = PersonalProfileSchema.safeParse({
      name: d.name.trim(),
      age: Number(d.age),
      address: address(d),
      occupation: d.occupation.trim(),
      household: { maritalStatus: d.maritalStatus, children: Number(d.children) },
      hobbies: d.hobbies,
    })
    return parsed.success ? parsed.data : undefined
  }
  const parsed = BusinessProfileSchema.safeParse({
    legalName: d.legalName.trim(),
    entityType: d.entityType,
    industry: d.industry.trim(),
    description: d.description.trim(),
    founded: d.founded,
    employeeCount: Number(d.employeeCount),
    address: address(d),
  })
  return parsed.success ? parsed.data : undefined
}
