/**
 * "Assign to the next loaded AMS slot" (voron B8).
 *
 * Three pieces that share one query key:
 *  - NextSlotBadge: the LOCATION-column chip for a spool that is waiting,
 *    with an inline cancel.
 *  - NextSlotButton: the small action that opens the modal for a spool that
 *    is neither assigned nor waiting.
 *  - AssignNextSlotModal: pick the printer and how long to wait; the backend
 *    completes the request when that printer reports a loaded, unassigned
 *    tray, and the websocket event refreshes this list.
 *
 * Everything stops click propagation: the inventory table row opens the edit
 * form on click, and these live inside it.
 */

import { useEffect, useMemo, useState, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Hourglass, Loader2, X } from 'lucide-react';
import { api, type InventorySpool, type PendingSlotAssignment } from '../api/client';
import { Button } from './Button';
import { Card, CardContent } from './Card';
import { useToast } from '../contexts/ToastContext';

export const PENDING_SLOT_QUERY_KEY = ['pending-slot-assignments'] as const;

const TIMEOUT_OPTIONS: Array<{ seconds: number; labelKey: string }> = [
  { seconds: 900, labelKey: 'inventory.nextSlot.timeoutOptions.m15' },
  { seconds: 1800, labelKey: 'inventory.nextSlot.timeoutOptions.m30' },
  { seconds: 7200, labelKey: 'inventory.nextSlot.timeoutOptions.h2' },
  { seconds: 86400, labelKey: 'inventory.nextSlot.timeoutOptions.h24' },
];
const DEFAULT_TIMEOUT = 1800;
const ANY_PRINTER = 'any';

export function usePendingSlotAssignments(enabled = true) {
  return useQuery({
    queryKey: PENDING_SLOT_QUERY_KEY,
    queryFn: () => api.getPendingSlotAssignments(),
    enabled,
    refetchInterval: 30000,
  });
}

function spoolLabel(spool: InventorySpool): string {
  return [spool.brand, spool.material, spool.subtype, spool.color_name].filter(Boolean).join(' ') || `#${spool.id}`;
}

function stop(e: MouseEvent) {
  e.stopPropagation();
}

export function NextSlotBadge({ pending }: { pending: PendingSlotAssignment }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const cancel = useMutation({
    mutationFn: () => api.cancelPendingSlotAssignment(pending.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: PENDING_SLOT_QUERY_KEY });
      showToast(t('inventory.nextSlot.cancelled'), 'success');
    },
    onError: (err) => {
      showToast(err instanceof Error ? err.message : t('inventory.nextSlot.cancelError'), 'error');
    },
  });

  const printer = pending.printer_name || t('inventory.nextSlot.anyPrinter');
  const expires = pending.expires_at ? new Date(pending.expires_at).toLocaleTimeString() : '?';

  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400"
      title={t('inventory.nextSlot.badgeTitle', { expires })}
      onClick={stop}
    >
      <Hourglass className="w-3 h-3 shrink-0" />
      <span className="truncate">{t('inventory.nextSlot.badge', { printer })}</span>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          cancel.mutate();
        }}
        disabled={cancel.isPending}
        aria-label={t('inventory.nextSlot.cancel')}
        title={t('inventory.nextSlot.cancel')}
        className="ml-0.5 rounded hover:text-red-600 dark:hover:text-red-400 disabled:opacity-50"
      >
        {cancel.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <X className="w-3 h-3" />}
      </button>
    </span>
  );
}

/** Icon-only so a long unassigned list stays quiet; the tooltip carries the words. */
export function NextSlotButton({ spool }: { spool: InventorySpool }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <span className="inline-flex items-center gap-1" onClick={stop}>
      <span className="text-sm text-bambu-gray">-</span>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        title={t('inventory.nextSlot.action')}
        aria-label={t('inventory.nextSlot.action')}
        className="p-1 rounded text-bambu-gray opacity-40 hover:opacity-100 hover:text-amber-600 dark:hover:text-amber-400 hover:bg-amber-500/10 transition-all"
      >
        <Hourglass className="w-3.5 h-3.5" />
      </button>
      <AssignNextSlotModal isOpen={open} onClose={() => setOpen(false)} spool={spool} />
    </span>
  );
}

interface AssignNextSlotModalProps {
  isOpen: boolean;
  onClose: () => void;
  spool: InventorySpool;
  /** Preselect this printer (the slot dialog passes the printer it was opened for). */
  defaultPrinterId?: number | null;
  /** Called after the request was created, before onClose. */
  onCreated?: () => void;
}

export function AssignNextSlotModal({ isOpen, onClose, spool, defaultPrinterId, onCreated }: AssignNextSlotModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [printerChoice, setPrinterChoice] = useState<string | null>(null);
  const [timeout, setTimeoutSeconds] = useState(DEFAULT_TIMEOUT);

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
    enabled: isOpen,
    staleTime: 60 * 1000,
  });

  // Fresh defaults every time the dialog opens; the preselected printer wins,
  // then the only printer there is, then "any".
  useEffect(() => {
    if (!isOpen) {
      setPrinterChoice(null);
      setTimeoutSeconds(DEFAULT_TIMEOUT);
      return;
    }
    if (printerChoice !== null || !printers) return;
    if (defaultPrinterId != null) setPrinterChoice(String(defaultPrinterId));
    else if (printers.length === 1) setPrinterChoice(String(printers[0].id));
    else setPrinterChoice(ANY_PRINTER);
  }, [isOpen, printers, defaultPrinterId, printerChoice]);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  const selectedPrinterName = useMemo(() => {
    if (printerChoice === null || printerChoice === ANY_PRINTER) return t('inventory.nextSlot.anyPrinter');
    return printers?.find((p) => String(p.id) === printerChoice)?.name ?? printerChoice;
  }, [printerChoice, printers, t]);

  const create = useMutation({
    mutationFn: () =>
      api.createPendingSlotAssignment({
        spool_id: spool.id,
        printer_id: printerChoice && printerChoice !== ANY_PRINTER ? Number(printerChoice) : null,
        timeout_seconds: timeout,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: PENDING_SLOT_QUERY_KEY });
      showToast(t('inventory.nextSlot.created', { printer: selectedPrinterName }), 'success');
      onCreated?.();
      onClose();
    },
    onError: (err) => {
      showToast(err instanceof Error ? err.message : t('inventory.nextSlot.createError'), 'error');
    },
  });

  if (!isOpen) return null;

  const selectClass =
    'w-full px-3 py-2 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary text-white text-sm focus:outline-none focus:border-bambu-green';

  return createPortal(
    <div
      className="fixed inset-0 z-[100] bg-black/50 flex items-center justify-center p-4"
      onClick={(e) => {
        e.stopPropagation();
        if (!create.isPending) onClose();
      }}
      data-testid="next-slot-modal"
    >
      <Card className="w-full max-w-md" onClick={stop}>
        <CardContent className="p-6">
          <div className="flex items-start gap-4">
            <div className="p-2 rounded-full bg-bambu-dark text-amber-500">
              <Hourglass className="w-6 h-6" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-lg font-semibold text-white mb-2">{t('inventory.nextSlot.modalTitle')}</h3>
              <p className="text-bambu-gray text-sm">
                {t('inventory.nextSlot.modalIntro', { spool: spoolLabel(spool) })}
              </p>
            </div>
          </div>

          <div className="mt-5 space-y-4">
            <label className="block">
              <span className="block text-xs font-medium text-bambu-gray mb-1">{t('inventory.nextSlot.printerLabel')}</span>
              <select
                className={selectClass}
                value={printerChoice ?? ANY_PRINTER}
                onChange={(e) => setPrinterChoice(e.target.value)}
                aria-label={t('inventory.nextSlot.printerLabel')}
              >
                <option value={ANY_PRINTER}>{t('inventory.nextSlot.anyPrinter')}</option>
                {(printers ?? []).map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="block text-xs font-medium text-bambu-gray mb-1">{t('inventory.nextSlot.timeoutLabel')}</span>
              <select
                className={selectClass}
                value={timeout}
                onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
                aria-label={t('inventory.nextSlot.timeoutLabel')}
              >
                {TIMEOUT_OPTIONS.map((o) => (
                  <option key={o.seconds} value={o.seconds}>
                    {t(o.labelKey)}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-xs text-bambu-gray/80">{t('inventory.nextSlot.rfidNote')}</p>
          </div>

          <div className="flex gap-3 mt-6">
            <Button variant="secondary" onClick={onClose} className="flex-1" disabled={create.isPending}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => create.mutate()} className="flex-1" disabled={create.isPending}>
              {create.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  {t('inventory.nextSlot.submitting')}
                </>
              ) : (
                t('inventory.nextSlot.submit')
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>,
    document.body
  );
}
