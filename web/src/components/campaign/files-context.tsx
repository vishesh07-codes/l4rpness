import { createContext, useContext } from "react"

export type FilesContextValue = {
  /** Paths that exist in the campaign work dir (rendered as clickable chips). */
  paths: Set<string>
  /** Open a work-dir path in the Workspace pane. */
  open: (path: string) => void
}

const FilesContext = createContext<FilesContextValue>({
  paths: new Set(),
  open: () => {},
})

export function useFiles() {
  return useContext(FilesContext)
}

export { FilesContext }
