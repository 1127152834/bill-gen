import { Navigate, NavLink, Route, Routes } from 'react-router-dom'
import Generation from './pages/Generation'
import History from './pages/History'
import New from './pages/New'
import Settings from './pages/Settings'

const navClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm font-medium ${isActive ? 'bg-accent-soft text-accent-strong' : 'text-muted hover:text-ink'}`

export default function App() {
  return (
    <div className="min-h-svh">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <span className="font-semibold tracking-tight">账单猎手</span>
          <nav className="flex gap-1" aria-label="主导航">
            <NavLink to="/new" className={navClass}>
              新建
            </NavLink>
            <NavLink to="/history" className={navClass}>
              历史
            </NavLink>
            <NavLink to="/settings" className={navClass}>
              模型
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6 sm:py-10">
        <Routes>
          <Route path="/" element={<Navigate to="/new" replace />} />
          <Route
            path="/new"
            element={
              <div className="mx-auto max-w-3xl">
                <New />
              </div>
            }
          />
          <Route path="/g/:id" element={<Generation />} />
          <Route path="/history" element={<History />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/new" replace />} />
        </Routes>
      </main>
    </div>
  )
}
