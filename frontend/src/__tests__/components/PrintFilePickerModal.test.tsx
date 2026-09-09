/**
 * The printer card's Print button used to open the upload modal and nothing
 * else, so printing a file that was already in the library meant uploading it
 * a second time. These pin what the picker in front of it promises: only
 * sliced files, newest first, searchable, and a file sliced for another model
 * refused before the click rather than after the upload.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { PrintFilePickerModal } from '../../components/PrintFilePickerModal';
import type { LibraryFileListItem } from '../../api/client';

const makeFile = (overrides: Partial<LibraryFileListItem>): LibraryFileListItem => ({
  id: 1,
  folder_id: null,
  is_external: false,
  filename: 'part.gcode.3mf',
  file_type: 'gcode.3mf',
  file_size: 1024,
  thumbnail_path: null,
  print_count: 0,
  duplicate_count: 0,
  created_by_id: null,
  created_by_username: null,
  created_at: '2026-01-01T00:00:00Z',
  fs_modified_at: null,
  print_name: null,
  print_time_seconds: null,
  filament_used_grams: null,
  sliced_for_model: null,
  tags: [],
  ...overrides,
});

function serveFiles(files: LibraryFileListItem[]) {
  server.use(http.get('/api/v1/library/files', () => HttpResponse.json(files)));
}

describe('PrintFilePickerModal', () => {
  beforeEach(() => {
    serveFiles([]);
  });

  it('lists sliced files and leaves source-only files out', async () => {
    serveFiles([
      makeFile({ id: 1, filename: 'bracket.gcode.3mf' }),
      makeFile({ id: 2, filename: 'model.stl', file_type: 'stl' }),
    ]);
    render(
      <PrintFilePickerModal printerName="Eddy" onSelect={() => {}} onClose={() => {}} />,
    );

    expect(await screen.findByText('bracket.gcode.3mf')).toBeInTheDocument();
    expect(screen.queryByText('model.stl')).toBeNull();
  });

  it('hands the picked file back to the caller', async () => {
    const onSelect = vi.fn();
    serveFiles([makeFile({ id: 9, filename: 'bracket.gcode.3mf' })]);
    render(
      <PrintFilePickerModal printerName="Eddy" onSelect={onSelect} onClose={() => {}} />,
    );

    await userEvent.click(await screen.findByText('bracket.gcode.3mf'));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 9 }));
  });

  it('filters on the search box', async () => {
    serveFiles([
      makeFile({ id: 1, filename: 'bracket.gcode.3mf' }),
      makeFile({ id: 2, filename: 'keychain.gcode.3mf' }),
    ]);
    render(
      <PrintFilePickerModal printerName="Eddy" onSelect={() => {}} onClose={() => {}} />,
    );
    await screen.findByText('bracket.gcode.3mf');

    await userEvent.type(screen.getByPlaceholderText('Search files...'), 'key');

    await waitFor(() => expect(screen.queryByText('bracket.gcode.3mf')).toBeNull());
    expect(screen.getByText('keychain.gcode.3mf')).toBeInTheDocument();
  });

  it('refuses a file sliced for another printer model up front', async () => {
    const onSelect = vi.fn();
    serveFiles([makeFile({ id: 3, filename: 'voron.gcode', sliced_for_model: 'Voron' })]);
    render(
      <PrintFilePickerModal
        printerName="Eddy"
        printerModel="P1S"
        onSelect={onSelect}
        onClose={() => {}}
      />,
    );

    const row = (await screen.findByText('voron.gcode')).closest('button') as HTMLButtonElement;
    expect(row).toBeDisabled();
    await userEvent.click(row, { pointerEventsCheck: 0 });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('offers the upload route only when the caller allows it', async () => {
    serveFiles([]);
    const { unmount } = render(
      <PrintFilePickerModal printerName="Eddy" onSelect={() => {}} onClose={() => {}} />,
    );
    expect(await screen.findByText('No sliced files in your library yet')).toBeInTheDocument();
    expect(screen.queryByText('Upload a file')).toBeNull();
    unmount();

    const onUpload = vi.fn();
    render(
      <PrintFilePickerModal
        printerName="Eddy"
        onSelect={() => {}}
        onUpload={onUpload}
        onClose={() => {}}
      />,
    );
    await userEvent.click(await screen.findByText('Upload a file'));
    expect(onUpload).toHaveBeenCalled();
  });
});
