import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './i18n' // Initialize i18n
import App from './App.tsx'
import { loadBrand } from './brand'

// Display brand (name + logo) comes from /brand.json; resolve it before the
// first render so the sidebar and login page never flash the default.
loadBrand().finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
