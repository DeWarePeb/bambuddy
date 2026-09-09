import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, ChevronRight, Loader2, Search, Terminal, X } from 'lucide-react';
import { api } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface KlipperConsoleModalProps {
  printerId: number;
  printerName: string;
  /** From the card's own status, so the banner agrees with what the card shows. */
  isPrinting: boolean;
  canControl: boolean;
  onClose: () => void;
}

/**
 * Macros and a G-code console for a Klipper printer (Voron patch series, C7).
 *
 * The card covers jog, home, extrude and the two temperatures. Everything else
 * a Voron owner does daily is a macro in printer.cfg or a line they type, and
 * without this the answer was "keep Mainsail open in another tab".
 *
 * The log comes from Moonraker's own G-code store, not from what this tab has
 * sent: a command issued from Mainsail, from a macro or by the queue belongs in
 * the same history, and after a reload there is still something to read.
 */
export function KlipperConsoleModal({
  printerId,
  printerName,
  isPrinting,
  canControl,
  onClose,
}: KlipperConsoleModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [line, setLine] = useState('');
  const [confirmDuringPrint, setConfirmDuringPrint] = useState(false);
  // What was typed before, newest first — the arrow keys walk it, like a shell.
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const logRef = useRef<HTMLDivElement>(null);

  const macrosQuery = useQuery({
    queryKey: ['klipper-macros', printerId],
    queryFn: () => api.getKlipperMacros(printerId),
  });

  const consoleQuery = useQuery({
    queryKey: ['klipper-console', printerId],
    queryFn: () => api.getKlipperConsole(printerId, 200),
    // Klipper answers in its own time — a macro can take a minute to say
    // anything — so the log polls rather than waiting on the send to return,
    // and picks up whatever else is talking to the printer meanwhile.
    refetchInterval: 3000,
  });

  const entries = consoleQuery.data?.entries ?? [];

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  const sendMutation = useMutation({
    mutationFn: (script: string) => api.sendKlipperGcode(printerId, script, confirmDuringPrint),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['klipper-console', printerId] });
    },
    onError: (error: Error) => {
      showToast(t('printers.console.sendFailed', { error: error.message }), 'error');
    },
  });

  const blocked = isPrinting && !confirmDuringPrint;

  const send = (script: string) => {
    const trimmed = script.trim();
    if (!trimmed || blocked || !canControl) return;
    setHistory((previous) => [trimmed, ...previous].slice(0, 50));
    setHistoryIndex(-1);
    sendMutation.mutate(trimmed);
  };

  const macros = useMemo(() => {
    const needle = search.trim().toUpperCase();
    const all = macrosQuery.data?.macros ?? [];
    if (!needle) return all;
    return all.filter(
      (macro) => macro.name.includes(needle) || macro.description.toUpperCase().includes(needle),
    );
  }, [macrosQuery.data, search]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      send(line);
      setLine('');
      return;
    }
    if (event.key === 'ArrowUp' && history.length) {
      event.preventDefault();
      const next = Math.min(historyIndex + 1, history.length - 1);
      setHistoryIndex(next);
      setLine(history[next]);
      return;
    }
    if (event.key === 'ArrowDown' && historyIndex >= 0) {
      event.preventDefault();
      const next = historyIndex - 1;
      setHistoryIndex(next);
      setLine(next < 0 ? '' : history[next]);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="w-full max-w-3xl max-h-[85vh] flex flex-col bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary overflow-hidden"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary flex-shrink-0">
          <div className="flex items-center gap-3">
            <Terminal className="w-5 h-5 text-bambu-green" />
            <div>
              <h2 className="text-lg font-semibold text-white">{t('printers.console.title')}</h2>
              <p className="text-sm text-bambu-gray">{printerName}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {isPrinting && (
          <label className="flex items-start gap-3 px-4 py-3 bg-amber-500/10 border-b border-amber-500/30 text-sm text-amber-200 cursor-pointer">
            <input
              type="checkbox"
              checked={confirmDuringPrint}
              onChange={(event) => setConfirmDuringPrint(event.target.checked)}
              className="mt-0.5 accent-amber-400"
            />
            <span>
              <AlertTriangle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
              {t('printers.console.printingWarning')}
            </span>
          </label>
        )}

        <div className="flex-1 min-h-0 flex flex-col md:flex-row">
          {/* The printer's own macros */}
          <div className="md:w-64 flex-shrink-0 border-b md:border-b-0 md:border-r border-bambu-dark-tertiary flex flex-col max-h-48 md:max-h-none">
            <div className="p-3 flex-shrink-0">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder={t('printers.console.searchMacros')}
                  className="w-full pl-8 pr-2 py-1.5 text-sm bg-bambu-dark rounded border border-bambu-dark-tertiary text-white placeholder:text-bambu-gray focus:outline-none focus:border-bambu-green"
                />
              </div>
            </div>
            <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-1">
              {macrosQuery.isLoading && <Loader2 className="w-4 h-4 animate-spin text-bambu-gray" />}
              {macrosQuery.isError && <p className="text-xs text-red-400">{t('printers.console.unavailable')}</p>}
              {!macrosQuery.isLoading && !macrosQuery.isError && macros.length === 0 && (
                <p className="text-xs text-bambu-gray">{t('printers.console.noMacros')}</p>
              )}
              {macros.map((macro) => (
                <button
                  key={macro.name}
                  onClick={() => send(macro.name)}
                  disabled={blocked || !canControl || sendMutation.isPending}
                  title={macro.description || macro.name}
                  className="w-full text-left px-2 py-1.5 rounded text-xs font-mono text-indigo-200 bg-indigo-500/10 hover:bg-indigo-500/25 transition-colors disabled:cursor-not-allowed disabled:opacity-40 truncate"
                >
                  {macro.name}
                </button>
              ))}
            </div>
          </div>

          {/* Moonraker's G-code store, and one line back */}
          <div className="flex-1 min-w-0 flex flex-col">
            <div ref={logRef} className="flex-1 overflow-y-auto p-3 font-mono text-xs space-y-0.5 min-h-[12rem]">
              {entries.length === 0 && <p className="text-bambu-gray">{t('printers.console.emptyLog')}</p>}
              {entries.map((entry, index) => (
                <div
                  key={`${entry.time ?? index}-${index}`}
                  className={
                    entry.type === 'command'
                      ? 'text-white'
                      : entry.message.startsWith('!!')
                        ? 'text-red-400'
                        : 'text-bambu-gray'
                  }
                >
                  {entry.type === 'command' && (
                    <ChevronRight className="inline w-3 h-3 -mt-0.5 text-bambu-green" />
                  )}
                  <span className="whitespace-pre-wrap break-all">{entry.message}</span>
                </div>
              ))}
            </div>
            <div className="p-3 border-t border-bambu-dark-tertiary flex items-center gap-2 flex-shrink-0">
              <input
                value={line}
                onChange={(event) => setLine(event.target.value)}
                onKeyDown={onKeyDown}
                disabled={!canControl}
                placeholder={canControl ? t('printers.console.placeholder') : t('printers.permission.noControl')}
                className="flex-1 px-3 py-1.5 text-sm font-mono bg-bambu-dark rounded border border-bambu-dark-tertiary text-white placeholder:text-bambu-gray focus:outline-none focus:border-bambu-green disabled:opacity-50"
              />
              <Button
                size="sm"
                onClick={() => {
                  send(line);
                  setLine('');
                }}
                disabled={!line.trim() || blocked || !canControl || sendMutation.isPending}
                title={blocked ? t('printers.console.confirmRequired') : undefined}
              >
                {sendMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : t('printers.console.send')}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
