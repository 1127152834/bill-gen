import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PersonalProfile } from '@bh/core/schemas'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, createGeneration: vi.fn(), randomPersona: vi.fn() }
})

import { ApiError, createGeneration, randomPersona } from '../api'
import New from './New'

const createMock = vi.mocked(createGeneration)
const randomMock = vi.mocked(randomPersona)

const RANDOM_PERSON: PersonalProfile = {
  name: 'Dana Whitfield',
  age: 52,
  address: { street: '88 Pine Ave', city: 'Denver', state: 'CO', zip: '80203' },
  occupation: 'Electrician',
  household: { maritalStatus: 'married', children: 2 },
  hobbies: ['fishing', 'chess'],
}

function renderWizard() {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={['/new']}>
      <Routes>
        <Route path="/new" element={<New />} />
        <Route path="/g/:id" element={<p>任务页面</p>} />
      </Routes>
    </MemoryRouter>,
  )
  return user
}

type User = ReturnType<typeof userEvent.setup>

async function fill(user: User, label: string, value: string) {
  const input = screen.getByLabelText(label)
  await user.clear(input)
  await user.type(input, value)
}

async function chooseSubject(user: User, name: '个人' | '企业') {
  await user.click(screen.getByRole('radio', { name: new RegExp(name) }))
  await user.click(screen.getByRole('button', { name: '下一步' }))
}

/** Step 1 -> step 2 (personal) -> let the system randomize the persona -> step 3. */
async function gotoStep3(user: User) {
  await chooseSubject(user, '个人')
  await user.click(screen.getByLabelText('让系统随机生成人设'))
  await user.click(screen.getByRole('button', { name: '下一步' }))
}

async function fillPersonalProfile(user: User) {
  await fill(user, '姓名', 'Alice Brown')
  await fill(user, '年龄', '41')
  await fill(user, '街道', '12 Oak St')
  await fill(user, '城市', 'Austin')
  await user.selectOptions(screen.getByLabelText('州'), 'TX')
  await fill(user, '邮编', '78701')
  await fill(user, '职业', 'Plumber')
  await user.selectOptions(screen.getByLabelText('婚姻状况'), 'single')
  await fill(user, '子女数量', '0')
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'], now: new Date(2026, 9, 29, 12) })
  createMock.mockReset()
  randomMock.mockReset()
})
afterEach(() => vi.useRealTimers())

describe('step 1 and 3: type and accounts', () => {
  it('needs a subject type before moving on', async () => {
    const user = renderWizard()
    expect(screen.getByRole('button', { name: '下一步' })).toBeDisabled()
    await user.click(screen.getByRole('radio', { name: /企业/ }))
    expect(screen.getByRole('button', { name: '下一步' })).toBeEnabled()
  })

  it('shows a Chinese error and disables submit when min is greater than max', async () => {
    const user = renderWizard()
    await gotoStep3(user)
    await fill(user, '支票账户 最小值', '9')
    await fill(user, '支票账户 最大值', '8')
    expect(screen.getByText('最小值不能大于最大值')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生成账单' })).toBeDisabled()
  })

  it('defaults the checking account on, and blocks submit once it is unticked', async () => {
    const user = renderWizard()
    await gotoStep3(user)
    expect(screen.getByLabelText('支票账户')).toBeChecked()
    expect(screen.getByRole('button', { name: '生成账单' })).toBeEnabled()
    await user.click(screen.getByLabelText('支票账户'))
    expect(screen.getByText('请至少选择一个账户')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生成账单' })).toBeDisabled()
    await user.click(screen.getByLabelText('支票账户'))
    expect(screen.queryByText('请至少选择一个账户')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生成账单' })).toBeEnabled()
  })

  it('rejects counts outside 1-200', async () => {
    const user = renderWizard()
    await gotoStep3(user)
    await fill(user, '支票账户 最大值', '201')
    expect(screen.getByText('请输入 1 到 200 之间的整数')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生成账单' })).toBeDisabled()
  })

  it('keeps the two slider thumbs in step with the number inputs', async () => {
    const user = renderWizard()
    await gotoStep3(user)
    await fill(user, '支票账户 最大值', '60')
    expect(screen.getByLabelText('支票账户 最大值滑块')).toHaveValue('60')
  })

  it('defaults the end month to last month and the period to 3 months', async () => {
    const user = renderWizard()
    await gotoStep3(user)
    expect(screen.getByLabelText('结束月份')).toHaveValue('2026-09')
    expect(screen.getByRole('radio', { name: '3 个月' })).toBeChecked()
  })
})

describe('step 2: persona fields', () => {
  it('replaces only the name when its dice is clicked and passes the other fields as partial', async () => {
    const user = renderWizard()
    await chooseSubject(user, '个人')
    await fill(user, '年龄', '41')
    await fill(user, '职业', 'Plumber')
    await fill(user, '姓名', 'Zed')
    randomMock.mockResolvedValue(RANDOM_PERSON)

    await user.click(screen.getByRole('button', { name: '随机生成 姓名' }))

    await waitFor(() => expect(screen.getByLabelText('姓名')).toHaveValue('Dana Whitfield'))
    expect(screen.getByLabelText('年龄')).toHaveValue(41)
    expect(screen.getByLabelText('职业')).toHaveValue('Plumber')
    expect(screen.getByLabelText('城市')).toHaveValue('')
    expect(randomMock).toHaveBeenCalledWith('personal', { age: 41, occupation: 'Plumber' })
  })

  it('disables the dice and shows a spinner while the call is in flight', async () => {
    const user = renderWizard()
    await chooseSubject(user, '个人')
    let resolve!: (p: PersonalProfile) => void
    randomMock.mockReturnValue(new Promise((r) => (resolve = r)))

    await user.click(screen.getByRole('button', { name: '随机生成 职业' }))
    const dice = screen.getByRole('button', { name: '随机生成 职业' })
    expect(dice).toBeDisabled()
    expect(within(dice).getByRole('status')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '随机生成 姓名' })).toBeEnabled()

    resolve(RANDOM_PERSON)
    await waitFor(() => expect(screen.getByLabelText('职业')).toHaveValue('Electrician'))
    expect(screen.getByRole('button', { name: '随机生成 职业' })).toBeEnabled()
  })

  it('shows a retry message when the randomizer fails with 502', async () => {
    const user = renderWizard()
    await chooseSubject(user, '个人')
    randomMock.mockRejectedValue(new ApiError(502, 'UPSTREAM_ERROR', 'The language model request failed'))
    await user.click(screen.getByRole('button', { name: '随机生成 姓名' }))
    expect(await screen.findByText('随机生成失败，请重试')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '随机生成 姓名' })).toBeEnabled()
  })

  it('"全部随机" sends an empty partial and fills every field', async () => {
    const user = renderWizard()
    await chooseSubject(user, '个人')
    await fill(user, '姓名', 'Zed')
    randomMock.mockResolvedValue(RANDOM_PERSON)

    await user.click(screen.getByRole('button', { name: '全部随机' }))

    await waitFor(() => expect(screen.getByLabelText('姓名')).toHaveValue('Dana Whitfield'))
    expect(randomMock).toHaveBeenCalledWith('personal', {})
    expect(screen.getByLabelText('州')).toHaveValue('CO')
    expect(screen.getByLabelText('邮编')).toHaveValue('80203')
    expect(screen.getByLabelText('婚姻状况')).toHaveValue('married')
    expect(screen.getByText('fishing')).toBeInTheDocument()
  })

  it('only sends an address group that is complete, and rerolls it as one unit', async () => {
    const user = renderWizard()
    await chooseSubject(user, '个人')
    await fill(user, '城市', 'Austin')
    randomMock.mockResolvedValue(RANDOM_PERSON)
    await user.click(screen.getByRole('button', { name: '随机生成 姓名' }))
    await waitFor(() => expect(randomMock).toHaveBeenCalledWith('personal', {}))

    await fill(user, '街道', '12 Oak St')
    await user.selectOptions(screen.getByLabelText('州'), 'TX')
    await fill(user, '邮编', '78701')
    await user.click(screen.getByRole('button', { name: '随机生成 职业' }))
    await waitFor(() =>
      expect(randomMock).toHaveBeenLastCalledWith('personal', {
        name: 'Dana Whitfield',
        address: { street: '12 Oak St', city: 'Austin', state: 'TX', zip: '78701' },
      }),
    )
  })

  it('blocks "下一步" with Chinese errors when required fields are empty', async () => {
    const user = renderWizard()
    await chooseSubject(user, '个人')
    await user.click(screen.getByRole('button', { name: '下一步' }))
    expect(screen.getAllByText('此项必填').length).toBeGreaterThan(3)
    expect(screen.queryByRole('button', { name: '生成账单' })).not.toBeInTheDocument()
  })

  it('flags a bad zip code under the field', async () => {
    const user = renderWizard()
    await chooseSubject(user, '个人')
    await fill(user, '邮编', '123')
    expect(screen.getByText('邮编必须是 5 位数字')).toBeInTheDocument()
  })

  it('collects hobbies as tags', async () => {
    const user = renderWizard()
    await chooseSubject(user, '个人')
    await user.type(screen.getByLabelText('爱好'), 'hiking{Enter}cooking,')
    expect(screen.getByText('hiking')).toBeInTheDocument()
    expect(screen.getByText('cooking')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '移除 hiking' }))
    expect(screen.queryByText('hiking')).not.toBeInTheDocument()
  })

  it('shows the business field set', async () => {
    const user = renderWizard()
    await chooseSubject(user, '企业')
    for (const label of ['公司名称', '企业类型', '行业', '业务描述', '成立月份', '员工人数', '街道', '城市', '州', '邮编']) {
      expect(screen.getByLabelText(label)).toBeInTheDocument()
    }
    expect(within(screen.getByLabelText('企业类型')).getAllByRole('option').map((o) => o.textContent)).toEqual(
      expect.arrayContaining(['LLC', 'S-Corp', 'Sole Prop']),
    )
  })
})

describe('submit', () => {
  it('submits without a profile when the system randomizes it, then opens the task page', async () => {
    createMock.mockResolvedValue({ id: 'abc-123' })
    const user = renderWizard()
    await gotoStep3(user)
    await fill(user, '支票账户 最小值', '10')
    await fill(user, '支票账户 最大值', '30')
    await user.click(screen.getByRole('radio', { name: '6 个月' }))
    await user.click(screen.getByRole('button', { name: '生成账单' }))

    await waitFor(() => expect(screen.getByText('任务页面')).toBeInTheDocument())
    expect(createMock).toHaveBeenCalledTimes(1)
    expect(createMock).toHaveBeenCalledWith({
      subjectType: 'personal',
      accounts: { checking: { min: 10, max: 30 } },
      months: 6,
      endMonth: '2026-09',
      templates: ['auto'],
      templateLimit: 2,
    })
  })

  it('submits the filled profile, the chosen layout and an optional seed', async () => {
    createMock.mockResolvedValue({ id: 'xyz' })
    const user = renderWizard()
    await chooseSubject(user, '个人')
    await fillPersonalProfile(user)
    await user.click(screen.getByRole('button', { name: '下一步' }))
    await user.selectOptions(screen.getByLabelText('目标版式'), 'chase-personal')
    await fill(user, '账单行名', 'Harborline Bank')
    await user.click(screen.getByText('高级选项'))
    await user.type(screen.getByLabelText('随机种子'), 'demo-1')
    await user.click(screen.getByRole('button', { name: '生成账单' }))

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1))
    expect(createMock).toHaveBeenCalledWith({
      subjectType: 'personal',
      profile: {
        name: 'Alice Brown',
        age: 41,
        address: { street: '12 Oak St', city: 'Austin', state: 'TX', zip: '78701' },
        occupation: 'Plumber',
        household: { maritalStatus: 'single', children: 0 },
        hobbies: [],
      },
      accounts: { checking: { min: 20, max: 40 } },
      months: 3,
      endMonth: '2026-09',
      templates: ['chase-personal'],
      bankName: 'Harborline Bank',
      seed: 'demo-1',
    })
  })

  it('shows the server INFEASIBLE_REQUEST message at the top and stays on the form', async () => {
    createMock.mockRejectedValue(new ApiError(400, 'INFEASIBLE_REQUEST', 'checking max (1) is below the base required count'))
    const user = renderWizard()
    await gotoStep3(user)
    await user.click(screen.getByRole('button', { name: '生成账单' }))

    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText('checking max (1) is below the base required count')).toBeInTheDocument()
    expect(screen.queryByText('任务页面')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生成账单' })).toBeEnabled()
  })

  it('prevents double submission while the request is pending', async () => {
    let resolve!: (v: { id: string }) => void
    createMock.mockReturnValue(new Promise((r) => (resolve = r)))
    const user = renderWizard()
    await gotoStep3(user)
    await user.click(screen.getByRole('button', { name: '生成账单' }))
    expect(screen.getByRole('button', { name: /正在提交/ })).toBeDisabled()
    resolve({ id: 'q' })
    await waitFor(() => expect(screen.getByText('任务页面')).toBeInTheDocument())
    expect(createMock).toHaveBeenCalledTimes(1)
  })
})
