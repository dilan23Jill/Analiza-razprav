import { createContext, useContext } from 'react'

/**
 * Lightweight context so deeply nested components (argument modal, critique
 * section) can perform owner-only edits without prop-drilling through the
 * whole tree. Provided by DebateViewPage.
 */
export const DebateEditContext = createContext({
  debateId: null,
  canEdit: false,
  refresh: null,
})

export function useDebateEdit() {
  return useContext(DebateEditContext)
}
