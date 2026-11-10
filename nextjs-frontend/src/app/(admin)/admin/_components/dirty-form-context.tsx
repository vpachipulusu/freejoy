'use client';

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

type DirtyFormState = {
  dirty: boolean;
  setDirty: (dirty: boolean) => void;
  /** The form saved and the page is reloading the saved version (separate from `dirty`: nothing is unsaved). */
  busy: boolean;
  setBusy: (busy: boolean) => void;
};

const DirtyFormContext = createContext<DirtyFormState>({
  dirty: false,
  setDirty: () => undefined,
  busy: false,
  setBusy: () => undefined,
});

/** Lets a form tell its siblings (the status buttons next to the offer form) that it holds unsaved edits. */
export function DirtyFormProvider({ children }: { children: ReactNode }) {
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <DirtyFormContext.Provider value={{ dirty, setDirty, busy, setBusy }}>
      {children}
    </DirtyFormContext.Provider>
  );
}

export function useFormDirty(): boolean {
  return useContext(DirtyFormContext).dirty;
}

export function useFormBusy(): boolean {
  return useContext(DirtyFormContext).busy;
}

/** Reports that the form's save is still being reloaded into the page (cleared when it stops or goes away). */
export function useReportFormBusy(busy: boolean): void {
  const { setBusy } = useContext(DirtyFormContext);
  useEffect(() => {
    setBusy(busy);
    return () => setBusy(false);
  }, [busy, setBusy]);
}

/** Reports a form's dirty state to the nearest provider (and clears it when the form goes away). */
export function useReportFormDirty(dirty: boolean): void {
  const { setDirty } = useContext(DirtyFormContext);
  useEffect(() => {
    setDirty(dirty);
    return () => setDirty(false);
  }, [dirty, setDirty]);
}
