import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"

import { AppSidebar } from "@/components/AppSidebar"
import { Campaign } from "@/pages/Campaign"
import { Dashboard } from "@/pages/Dashboard"
import { NewCampaign } from "@/pages/NewCampaign"

export default function App() {
  return (
    <BrowserRouter>
      <AppSidebar />
      <main className="pl-56">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/campaigns/new" element={<NewCampaign />} />
          <Route path="/campaigns/:id" element={<Campaign />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
