import { z } from 'zod';
import { isValidDate } from '../date.js';

/** `YYYY-MM-DD` calendar date string. */
export const DateStringSchema = z
  .string()
  .regex(/^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/, 'expected YYYY-MM-DD')
  .refine(isValidDate, 'not a real calendar date');
export type DateString = z.infer<typeof DateStringSchema>;

/** `YYYY-MM` month string. */
export const MonthStringSchema = z
  .string()
  .regex(/^\d{4}-(0[1-9]|1[0-2])$/, 'expected YYYY-MM');
export type MonthString = z.infer<typeof MonthStringSchema>;

/** Monthly transaction-count range for one account (hard constraint downstream). */
export const CountRangeSchema = z
  .object({
    min: z.number().int().min(1),
    max: z.number().int().max(200),
  })
  .refine((r) => r.min <= r.max, { message: 'min must be <= max', path: ['min'] });
export type CountRange = z.infer<typeof CountRangeSchema>;

export const AddressSchema = z.object({
  street: z.string().min(1),
  city: z.string().min(1),
  state: z.string().regex(/^[A-Z]{2}$/, 'expected 2-letter uppercase state'),
  zip: z.string().regex(/^\d{5}$/, 'expected 5-digit zip'),
});
export type Address = z.infer<typeof AddressSchema>;

export const PersonalProfileSchema = z.object({
  name: z.string().min(1),
  age: z.number().int().min(18).max(90),
  address: AddressSchema,
  occupation: z.string().min(1),
  household: z.object({
    maritalStatus: z.enum(['single', 'married', 'partnered']),
    children: z.number().int().min(0).max(6),
  }),
  hobbies: z.array(z.string().min(1)).max(8),
});
export type PersonalProfile = z.infer<typeof PersonalProfileSchema>;

export const BusinessProfileSchema = z.object({
  legalName: z.string().min(1),
  entityType: z.enum(['LLC', 'S-Corp', 'Sole Prop']),
  industry: z.string().min(1),
  description: z.string().min(1),
  founded: MonthStringSchema,
  employeeCount: z.number().int().min(0).max(500),
  address: AddressSchema,
});
export type BusinessProfile = z.infer<typeof BusinessProfileSchema>;

export const ProfileSchema = z.union([PersonalProfileSchema, BusinessProfileSchema]);
export type Profile = z.infer<typeof ProfileSchema>;

export const SubjectTypeSchema = z.enum(['personal', 'business']);
export type SubjectType = z.infer<typeof SubjectTypeSchema>;

export const GenerationRequestSchema = z
  .object({
    subjectType: SubjectTypeSchema,
    /** Omitted = a later task generates a random profile. */
    profile: ProfileSchema.optional(),
    accounts: z.object({
      checking: CountRangeSchema.optional(),
      credit: CountRangeSchema.optional(),
    }),
    months: z.union([z.literal(1), z.literal(3), z.literal(6)]),
    /**
     * 支票账户结单日（开户日）。给了就按它滚账期，不给仍是自然月 ——
     * 老数据/老 fixture 依赖自然月，不能悄悄改。
     */
    checkingAnchorDay: z.number().int().min(1).max(28).optional(),
    endMonth: MonthStringSchema.optional(),
    seed: z.string().optional(),
    /**
     * 出片版式（按顺序尝试）。省略 = 用服务端 `BILLGEN_TEMPLATES`；含 `auto` 时交给推荐器。
     * 页面上的「做哪家银行的账单」就是这一项，所以它可以按次指定，不该只藏在进程环境里。
     */
    templates: z.array(z.string().min(1)).max(6).optional(),
    /** `auto` 时每个账户保留几套版式。 */
    templateLimit: z.number().int().min(1).max(11).optional(),
    /** 账单上印的银行名；给了就覆盖版式自带的行名（联系方式按这个名字派生）。 */
    bankName: z.string().min(1).max(60).optional(),
  })
  .superRefine((req, ctx) => {
    if (!req.accounts.checking && !req.accounts.credit) {
      ctx.addIssue({
        code: 'custom',
        path: ['accounts'],
        message: 'at least one of checking / credit is required',
      });
    }
    if (req.profile) {
      const profileSchema =
        req.subjectType === 'personal' ? PersonalProfileSchema : BusinessProfileSchema;
      if (!profileSchema.safeParse(req.profile).success) {
        ctx.addIssue({
          code: 'custom',
          path: ['profile'],
          message: `profile shape does not match subjectType "${req.subjectType}"`,
        });
      }
    }
  });
export type GenerationRequest = z.infer<typeof GenerationRequestSchema>;
