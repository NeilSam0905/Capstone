import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// The redesign's design system (ported from "UST Prototype Design/app/styles.css").
// It is the source of truth for colour, type and spacing — screens use its
// class vocabulary (.card, .kpi, .tbl, .tag) rather than ad-hoc utilities.
import './styles/redesign.css'
import App from './App.jsx'

// Tokens the stylesheet keys off. The prototype drove these from its Tweaks
// panel; here they are fixed to the approved direction: gold accent, dark
// chrome, regular density, soft corners.
const root = document.documentElement
root.dataset.accent = 'gold'
root.dataset.chrome = 'dark'
root.dataset.density = 'regular'
root.dataset.corners = 'soft'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
