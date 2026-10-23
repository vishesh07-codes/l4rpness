import { useCallback, useEffect, useState } from "react"

import { api, type WorkFile } from "@/lib/api"

/** Loads a campaign's work-dir file list; `null` while first loading. */
export function useWorkFiles(campaignId: number) {
  const [files, setFiles] = useState<WorkFile[] | null>(null)

  const refresh = useCallback(() => {
    api
      .listWorkFiles(campaignId)
      .then(setFiles)
      .catch(() => setFiles([]))
  }, [campaignId])

  useEffect(() => {
    setFiles(null)
    refresh()
  }, [refresh])

  return { files, refresh }
}
