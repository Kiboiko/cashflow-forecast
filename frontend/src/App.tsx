import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'

import { RequireAuth } from './auth'
import { Layout } from './components/Layout'
import { Spinner } from './components/ui'
import HomePage from './pages/Home'
import LoginPage from './pages/Login'
import NotFoundPage from './pages/NotFound'

// Остальные экраны грузим по требованию: первая страница открывается быстрее
const PaymentsPage = lazy(() => import('./pages/Payments'))
const WhatIfPage = lazy(() => import('./pages/WhatIf'))
const PlanFactPage = lazy(() => import('./pages/PlanFact'))
const OperationsPage = lazy(() => import('./pages/Operations'))
const HowItWorksPage = lazy(() => import('./pages/HowItWorks'))
const SettingsPage = lazy(() => import('./pages/Settings'))

export default function App() {
  return (
    <Suspense fallback={<Spinner />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route index element={<HomePage />} />
          <Route path="payments" element={<PaymentsPage />} />
          <Route path="what-if" element={<WhatIfPage />} />
          <Route path="plan-fact" element={<PlanFactPage />} />
          <Route path="operations" element={<OperationsPage />} />
          <Route path="how-it-works" element={<HowItWorksPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Suspense>
  )
}
