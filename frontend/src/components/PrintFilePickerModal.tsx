import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Clock, FileBox, Loader2, Printer, Search, Upload, X } from 'lucide-react';
import { api, type LibraryFileListItem } from '../api/client';
import { isSlicedLibraryFile } from '../utils/libraryFiles';
import { isGcodeCompatible } from '../utils/printer';
import { formatDuration, parseUTCDate } from '../utils/date';
import { formatFileSize } from '../utils/file';
import { Button } from './Button';

interface PrintFilePickerModalProps {
  /** Shown in the header so a multi-printer wall makes clear which card opened this. */
  printerName: string;
  /** Mapped model code (e.g. "P1S"). Files whose G-code this printer cannot run
   *  are hidden behind a checkbox. Undefined means "unknown", and nothing is hidden. */
  printerModel?: string;
  onSelect: (file: LibraryFileListItem) => void;
  /** Switch to the upload flow. Omitted when the user may not upload. */
  onUpload?: () => void;
  onClose: () => void;
}

/**
 * Pick an already-uploaded library file to print.
 *
 * The Print button on a printer card used to go straight to the upload modal,
 * which meant a file already sitting in the library had to be uploaded a second
 * time to be printed from here. Everything the library knows is reused: the
 * same "is this sliced" test as the File Manager's own Print button, and
 * `isGcodeCompatible` — the rule dispatch itself enforces — to decide what this
 * particular printer can run, before the click rather than as a rejection
 * afterwards.
 */
export function PrintFilePickerModal({
  printerName,
  printerModel,
  onSelect,
  onUpload,
  onClose,
}: PrintFilePickerModalProps) {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  // A file this printer cannot run is noise on a card you opened to print
  // something, so the list hides it -- but "my file is not in the list" is a
  // worse puzzle than a greyed-out row, so the filter is a checkbox, not a
  // rule, and the hidden ones are one click away with the reason on them.
  const [showOthers, setShowOthers] = useState(false);

  // folderId null + includeRoot false is the backend's "every file, flat"
  // form. A picker wants one searchable list, not the folder tree — the File
  // Manager is where folders are for.
  const { data: files, isLoading } = useQuery({
    queryKey: ['libraryFiles', 'printPicker'],
    queryFn: () => api.getLibraryFiles(null, false),
  });

  const printable = useMemo(() => {
    const sliced = (files ?? []).filter(isSlicedLibraryFile);
    // Newest first: the file someone just sliced is the one they came for,
    // while the backend orders by filename for the alphabetical folder view.
    return sliced.sort((a, b) => {
      const at = parseUTCDate(a.fs_modified_at ?? a.created_at)?.getTime() ?? 0;
      const bt = parseUTCDate(b.fs_modified_at ?? b.created_at)?.getTime() ?? 0;
      return bt - at;
    });
  }, [files]);

  const searched = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return printable;
    return printable.filter(
      (f) =>
        f.filename.toLowerCase().includes(q) ||
        (f.print_name ?? '').toLowerCase().includes(q),
    );
  }, [printable, search]);

  // `isGcodeCompatible` is the app's own rule and mirrors the backend's, so
  // this hides exactly what dispatch would refuse -- and nothing more. An X1C
  // plate on a P1S stays on offer because that pair is one G-code family;
  // exact-name matching, which the upload path does, would have dropped it.
  const [fits, others] = useMemo(() => {
    const ok: LibraryFileListItem[] = [];
    const no: LibraryFileListItem[] = [];
    for (const f of searched) {
      (isGcodeCompatible(f.sliced_for_model, printerModel) ? ok : no).push(f);
    }
    return [ok, no];
  }, [searched, printerModel]);

  // Appended rather than merged, so ticking the box adds to the list instead
  // of reshuffling the row the user was about to click.
  const rows = showOthers ? [...fits, ...others] : fits;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div
        className="w-full max-w-2xl max-h-[85vh] flex flex-col rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex-shrink-0 flex items-start justify-between gap-3 px-4 pt-4 pb-3 border-b border-bambu-dark-tertiary">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-white truncate">
              {t('printers.printSource.title')}
            </h2>
            <p className="text-xs text-bambu-gray mt-1 truncate">
              {t('printers.printSource.hint', { printer: printerName })}
            </p>
          </div>
          <button
            onClick={onClose}
            className="flex-shrink-0 p-1 hover:bg-bambu-dark rounded"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        {/* Search */}
        <div className="flex-shrink-0 px-4 pt-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('fileManager.searchFiles')}
              className="w-full pl-9 pr-3 py-2 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
            />
          </div>
          {/* Only worth showing when something is actually being held back --
              a checkbox that never changes anything is one more thing to read. */}
          {others.length > 0 && (
            <label className="flex items-center gap-2 mt-2 cursor-pointer text-xs text-bambu-gray">
              <input
                type="checkbox"
                checked={showOthers}
                onChange={(e) => setShowOthers(e.target.checked)}
                className="w-4 h-4 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
              />
              <span>
                {t('printers.printSource.showOthers')} ({others.length})
              </span>
            </label>
          )}
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto p-4 space-y-2">
          {isLoading ? (
            <div className="flex items-center justify-center gap-2 py-10 text-bambu-gray text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('common.loading')}
            </div>
          ) : rows.length === 0 ? (
            <p className="py-10 text-center text-sm text-bambu-gray">
              {printable.length === 0
                ? t('printers.printSource.empty')
                : searched.length === 0
                  ? t('printers.printSource.noMatches')
                  : /* Everything that matched is sliced for another machine and
                       the checkbox above can bring it back. */
                    t('printers.printSource.noneForThisPrinter')}
            </p>
          ) : (
            rows.map((file) => {
              const reason = isGcodeCompatible(file.sliced_for_model, printerModel)
                ? null
                : t('printers.incompatibleFile', {
                    slicedFor: file.sliced_for_model,
                    printerModel,
                  });
              return (
                <button
                  key={file.id}
                  type="button"
                  disabled={!!reason}
                  onClick={() => onSelect(file)}
                  title={reason ?? undefined}
                  className={`w-full flex items-center gap-3 p-2 rounded-lg border text-left transition-colors ${
                    reason
                      ? 'border-bambu-dark-tertiary bg-bambu-dark opacity-50 cursor-not-allowed'
                      : 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-green/60'
                  }`}
                >
                  {file.thumbnail_path ? (
                    <img
                      src={api.getLibraryFileThumbnailUrl(file.id)}
                      alt={file.filename}
                      className="w-12 h-12 rounded object-cover bg-bambu-dark-tertiary flex-shrink-0"
                    />
                  ) : (
                    <div className="w-12 h-12 rounded bg-bambu-dark-tertiary flex items-center justify-center flex-shrink-0">
                      <FileBox className="w-5 h-5 text-bambu-gray" />
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-white font-medium truncate">
                      {file.print_name || file.filename}
                    </p>
                    <div className="flex items-center gap-3 mt-0.5 text-xs text-bambu-gray">
                      <span>{formatFileSize(file.file_size)}</span>
                      {file.print_time_seconds != null && (
                        <span className="flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {formatDuration(file.print_time_seconds)}
                        </span>
                      )}
                      {file.sliced_for_model && (
                        <span className="flex items-center gap-1">
                          <Printer className="w-3 h-3" />
                          {file.sliced_for_model}
                        </span>
                      )}
                    </div>
                    {reason && (
                      <p className="text-xs text-red-700 dark:text-red-400 mt-1 break-words">{reason}</p>
                    )}
                  </div>
                </button>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="flex-shrink-0 p-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          {onUpload && (
            <Button onClick={onUpload}>
              <Upload className="w-4 h-4 mr-2" />
              {t('printers.printSource.uploadInstead')}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
