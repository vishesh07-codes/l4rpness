import { useCallback, useEffect, useState } from "react";

import { api, type WorkFile } from "@/lib/api";

/** Loads a campaign's work-dir file list; `null` while first loading. */
export function useWorkFiles(campaignId: number) {
  const [files, setFiles] = useState<WorkFile[] | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    setError("");
    api
      .listWorkFiles(campaignId)
      .then(setFiles)
      .catch((e: Error) => {
        setFiles([]);
        setError(e.message);
      });
  }, [campaignId]);

  useEffect(() => {
    setFiles(null);
    setError("");
    refresh();
  }, [refresh]);

  return { files, error, refresh };
}
