import { createContext, useContext } from 'react'

export const DebateEditContext = createContext({
  debateId: null,
  canEdit: false,
  refresh: null,
})

export function useDebateEdit() {
  return useContext(DebateEditContext)
}
