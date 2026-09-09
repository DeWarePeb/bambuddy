import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Clock, FileBox, Loader2, Printer, Search, Upload, X } from 'lucide-react';
import { api, type LibraryFileListItem } from '../api/client';
import { isSlicedLibraryFile } from '../utils/libraryFiles';
import { formatDuration, parseUTCDate } from '../utils/date';
import { formatFileSize } from '../utils/file';
import { Button } from './Button';

interface PrintFilePickerModalProps {
  /** Shown in the header so a multi-printer wall makes clear which card opened this. */
  printerName: string;
  /** Mapped model code (e.g. "P1S"), used to grey out files sliced for another machine. */
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
 * same "is this sliced" test as the File Manager's own Print button, and the
 * same printer-model check the upload path applies after the fact — except that
 * here it can be shown before the click instead of as a rejection afterwards.
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

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return printable;
    return printable.filter(
      (f) =>
        f.filename.toLowerCase().includes(q) ||
        (f.print_name ?? '').toLowerCase().includes(q),
    );
  }, [printable, search]);

  const incompatibleReason = (file: LibraryFileListItem): string | null => {
    const slicedFor = file.sliced_for_model;
    if (!slicedFor || !printerModel) return null;
    if (slicedFor.toLowerCase() === printerModel.toLowerCase()) return null;
    return t('printers.incompatibleFile', {
      slicedFor,
      printerModel,
    });
  };

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
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto p-4 space-y-2">
          {isLoading ? (
            <div className="flex items-center justify-center gap-2 py-10 text-bambu-gray text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('common.loading')}
            </div>
          ) : visible.length === 0 ? (
            <p className="py-10 text-center text-sm text-bambu-gray">
              {printable.length === 0
                ? t('printers.printSource.empty')
                : t('printers.printSource.noMatches')}
            </p>
          ) : (
            visible.map((file) => {
              const reason = incompatibleReason(file);
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
