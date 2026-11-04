import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";
import { ApiError, isAbort } from "../api/client";

/** Sets the browser tab title: "Scan results – Readpath". */
export function useDocumentTitle(title: string | null | undefined): void {
  useEffect(() => {
    document.title = title ? `${title} – Readpath` : "Readpath";
  }, [title]);
}

export interface Resource<T> {
  data: T | undefined;
  error: ApiError | Error | undefined;
  loading: boolean;
  reload: () => void;
  /** Replace the data, or update it from the previous value. */
  setData: (data: T | ((prev: T | undefined) => T)) => void;
}

/** Loads data with fetch-abort on unmount / dependency change. */
export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, deps: DependencyList): Resource<T> {
  const [data, setDataState] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | Error | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    loadRef.current(ctrl.signal).then(
      (result) => {
        if (ctrl.signal.aborted) return;
        setDataState(result);
        setError(undefined);
        setLoading(false);
      },
      (err: unknown) => {
        if (ctrl.signal.aborted || isAbort(err)) return;
        setError(err instanceof Error ? err : new Error(String(err)));
        setLoading(false);
      },
    );
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const setData = useCallback((d: T | ((prev: T | undefined) => T)) => {
    setDataState(d as T);
    setError(undefined);
    setLoading(false);
  }, []);

  return { data, error, loading, reload, setData };
}
