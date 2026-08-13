import { resolveApiErrorMessage } from '../api/errors'
import { resolveMutationError } from './feedPageUtils'
import { useFeedsPageController } from './useFeedsPageController'

type FeedsController = ReturnType<typeof useFeedsPageController>

export function FeedManagementStatus({ controller }: { controller: FeedsController }) {
  const {
    canDelete,
    encryptedDataHealthQuery,
    exportFeeds,
    exportNotice,
    importData,
    importError,
    importFeeds,
    importFilename,
    importPreviewSummary,
    importWarning,
    lastImportResult,
    managementError,
    managementNotice,
    overwriteExisting,
  } = controller
  return (
    <>
      {importFilename && <p className="mt-2 text-xs text-slate dark:text-slate-300">Loaded: {importFilename} ({importData?.length ?? 0} entries)</p>}
      {importPreviewSummary && (
        <div className="mt-2 rounded border border-slate/20 bg-white/60 px-2 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-[#072019] dark:text-slate-200">
          <p>
            Import preflight: {importPreviewSummary.createCount} new, {importPreviewSummary.overwriteCount} overwrite,{' '}
            {importPreviewSummary.skipCount} skip, {importPreviewSummary.duplicateEntries} duplicate entr
            {importPreviewSummary.duplicateEntries === 1 ? 'y' : 'ies'} ignored from {importPreviewSummary.uniqueEntries} unique URL
            {importPreviewSummary.uniqueEntries === 1 ? '' : 's'}.
          </p>
          {importPreviewSummary.matchingExistingFeeds.length > 0 && (
            <p className="mt-1 text-slate dark:text-slate-300">
              {overwriteExisting
                ? 'Existing feeds below will be rewritten from the import file after confirmation.'
                : 'Existing feeds below will be skipped unless overwrite is enabled.'}
            </p>
          )}
        </div>
      )}
      {importError && <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">Import parse error: {importError}</p>}
      {importWarning && <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-amber-600">{importWarning}</p>}
      {lastImportResult && (
        <div role="status" aria-live="polite" aria-atomic="true" className="mt-2 rounded border border-slate/20 bg-white/60 px-2 py-2 text-xs text-slate dark:border-cyan-900/40 dark:bg-[#072019] dark:text-slate-200">
          <p>Import result: created {lastImportResult.created}, updated {lastImportResult.updated}, skipped {lastImportResult.skipped}, errors {lastImportResult.errors.length}</p>
          {lastImportResult.created + lastImportResult.updated === 0 && (
            <p className="mt-1 text-amber-600">No feeds were created or updated. This usually means all entries already existed and overwrite was disabled, or entries were rejected.</p>
          )}
          {lastImportResult.errors.length > 0 && (
            <ul className="mt-1 list-disc pl-4 text-red-600">
              {lastImportResult.errors.map((entry, index) => <li key={`${entry}-${index}`}>{entry}</li>)}
            </ul>
          )}
        </div>
      )}
      {importFeeds.isError && <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">{resolveMutationError(importFeeds.error, 'Feed import could not be completed')}</p>}
      {managementNotice && <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-emerald-700 dark:text-emerald-300">{managementNotice}</p>}
      {exportNotice && <p role="status" aria-live="polite" aria-atomic="true" className="mt-2 text-xs text-amber-700 dark:text-amber-300">{exportNotice}</p>}
      {exportFeeds.isError && <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">{resolveMutationError(exportFeeds.error, 'Feed export could not be completed')}</p>}
      {canDelete && encryptedDataHealthQuery.isError && (
        <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">{resolveApiErrorMessage(encryptedDataHealthQuery.error, 'Encrypted data health could not be loaded')}</p>
      )}
      {managementError && <p role="alert" aria-live="assertive" aria-atomic="true" className="mt-2 text-xs text-red-600">{resolveApiErrorMessage(managementError, 'One or more feed management actions could not be completed')}</p>}
    </>
  )
}
