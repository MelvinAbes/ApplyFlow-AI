import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components'
import { ApplicationDetailPage, ApplicationReviewPage, ApplicationsPage } from './pages/Applications'
import { DashboardPage } from './pages/Dashboard'
import { JobDetailPage, JobsPage } from './pages/Jobs'
import { ProfilePage } from './pages/Profile'
import { ResumesPage } from './pages/Resumes'
import { SettingsPage } from './pages/Settings'

export default function App() {
  return <Layout><Routes>
    <Route path="/" element={<DashboardPage/>}/>
    <Route path="/profile" element={<ProfilePage/>}/>
    <Route path="/resumes" element={<ResumesPage/>}/>
    <Route path="/jobs" element={<JobsPage/>}/>
    <Route path="/jobs/:id" element={<JobDetailPage/>}/>
    <Route path="/applications" element={<ApplicationsPage/>}/>
    <Route path="/applications/:id" element={<ApplicationDetailPage/>}/>
    <Route path="/applications/:id/review" element={<ApplicationReviewPage/>}/>
    <Route path="/settings" element={<SettingsPage/>}/>
    <Route path="*" element={<Navigate to="/" replace/>}/>
  </Routes></Layout>
}
