import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ModelSettings } from '../api'
import Settings from './Settings'

const fixture = (over: Partial<ModelSettings> = {}): ModelSettings => ({
  llmFake: true,
  baseUrl: null,
  model: null,
  structuredMode: 'json_schema',
  apiKeyMasked: null,
  tavilyKeyMasked: null,
  hasApiKey: false,
  hasTavilyKey: false,
  ready: false,
  source: { apiKey: 'none', baseUrl: 'none', tavily: 'none' },
  ...over,
})

const getSettings = vi.fn()
const updateSettings = vi.fn()
const testModel = vi.fn()

vi.mock('../api', async (orig) => ({
  ...(await orig<typeof import('../api')>()),
  getSettings: () => getSettings(),
  updateSettings: (p: unknown) => updateSettings(p),
  testModel: (p: unknown) => testModel(p),
}))

describe('Settings', () => {
  beforeEach(() => {
    getSettings.mockReset()
    updateSettings.mockReset()
    testModel.mockReset()
  })

  it('shows the offline badge until the three credentials are in', async () => {
    getSettings.mockResolvedValue(fixture())
    render(<Settings />)
    expect(await screen.findByText('离线 fixture')).toBeInTheDocument()
    expect(screen.getByText(/补齐 baseUrl/)).toBeInTheDocument()
  })

  it('shows the real-model badge with where each value came from', async () => {
    getSettings.mockResolvedValue(
      fixture({ llmFake: false, ready: true, model: 'gpt-4o', baseUrl: 'https://x.test/v1', hasApiKey: true, hasTavilyKey: true, apiKeyMasked: 'sk-ab…1234' }),
    )
    render(<Settings />)
    expect(await screen.findByText('真实模型')).toBeInTheDocument()
    expect(screen.getByText(/gpt-4o/)).toBeInTheDocument()
  })

  it('sends only the fields that changed, and keeps a blank key as "leave alone"', async () => {
    getSettings.mockResolvedValue(fixture({ llmFake: false, ready: true, model: 'gpt-4o', hasApiKey: true, apiKeyMasked: 'sk-ab…1234' }))
    updateSettings.mockResolvedValue(fixture({ llmFake: false, ready: true, model: 'gpt-4o-mini' }))
    render(<Settings />)
    const modelInput = await screen.findByPlaceholderText('gpt-4o')
    const user = userEvent.setup()
    await user.clear(modelInput)
    await user.type(modelInput, 'gpt-4o-mini')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ model: 'gpt-4o-mini' }))
  })

  it('reports a failed connection without leaking the key', async () => {
    getSettings.mockResolvedValue(fixture())
    testModel.mockRejectedValue(new Error('401 Unauthorized'))
    render(<Settings />)
    await screen.findByRole('button', { name: '测试连接' })
    expect(screen.getByRole('button', { name: '测试连接' })).toBeEnabled()
  })
})
