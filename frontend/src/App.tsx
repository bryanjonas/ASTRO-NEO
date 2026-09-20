import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Overview from './pages/Overview'
import Hardware from './pages/Hardware'
import SkyView from './pages/SkyView'
import Psv from './pages/Psv'

export default function App() {
  return (
    <Routes>
      <Route path="/app" element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="hardware" element={<Hardware />} />
        <Route path="sky" element={<SkyView />} />
        <Route path="psv" element={<Psv />} />
      </Route>
    </Routes>
  )
}
