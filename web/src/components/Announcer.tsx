import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

// One polite live region for the whole app. It exists from the first render,
// so screen readers reliably pick up changes (a region added at the same
// moment as its text is often missed).

type Announce = (message: string) => void;

const AnnouncerContext = createContext<Announce>(() => {});

export function AnnouncerProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const announce = useCallback<Announce>((text) => {
    window.clearTimeout(timer.current);
    // Clear first so the same message twice in a row is still announced.
    setMessage("");
    timer.current = window.setTimeout(() => setMessage(text), 60);
  }, []);

  return (
    <AnnouncerContext.Provider value={announce}>
      {children}
      <div className="visually-hidden" aria-live="polite" aria-atomic="true" data-testid="announcer">
        {message}
      </div>
    </AnnouncerContext.Provider>
  );
}

export function useAnnounce(): Announce {
  return useContext(AnnouncerContext);
}
