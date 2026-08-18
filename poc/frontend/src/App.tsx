import { Route, Routes } from "react-router-dom"
import { DocumentHistory } from "./pages/DocumentHistory"
import { DocumentReview } from "./pages/DocumentReview"

function App() {
  return (
    <Routes>
      <Route path="/" element={<DocumentHistory />} />
      <Route path="/documents/:documentId" element={<DocumentReview />} />
    </Routes>
  )
}

export default App
