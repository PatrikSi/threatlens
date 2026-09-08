# Workflow remediation

## Article previews

Original-article previews block remote images, stylesheets, fonts, and media by
default. The source document is still retrieved by the backend. Analysts can
enable **Load external resources for this preview** when needed; this allows
publishers and third parties to observe the browser's IP address and viewing
activity. The choice resets for each article and can be turned off again.
Scripts, forms, embedded frames, and direct script connections remain blocked.

The preview API accepts `external_resources=true` to opt in to this behavior.
Existing item permissions and handling-label access checks apply to both modes.

## Dashboard layout

In layout edit mode, floating panels expose a **Move** button and a resize
handle. Focus either control and use the arrow keys; hold Shift for larger
steps. Geometry stays within the workspace and respects minimum sizes. Focusing
any control inside a panel brings that panel to the front. Existing mouse and
snap-layout controls continue to work.

## Saves with subsequent edits

Feed details, SMTP destinations and webhook editors apply accepted server values
only to fields unchanged since that submission. Later edits remain in the draft
and keep navigation protection active. Completions for another selected record
update its cached saved configuration without replacing the current editor.
New-feed submission clears only fields that still match the submitted form.

## Report drafts and library

Template queries initialize a new builder once. Subsequent cache refreshes preserve
the report draft; a changed or deleted template is shown explicitly. Updates use
the revision loaded into the builder, so background refresh cannot silently bypass
an edit conflict. Loading another template or its latest revision requires
confirmation when the draft is dirty. Browser navigation and reload are protected.
A queued report opens automatically only when the submitted draft is still current;
newer edits stay in the builder and the queued report remains in the library.

The report library pages through every report available under the existing data
access policy, 25 per page, with one extra result to determine whether Next is
available. It shows the actual range without inventing a total, supports status
and creation-date filters, and resets to page one when scope changes. The additive
API parameters `created_from` (inclusive) and `created_before` (exclusive) accept
timestamps, interpret missing offsets as UTC, and reject reversed or empty ranges.
The UI's through date includes that full UTC day. Sorting breaks creation-time
ties by report ID for stable offset paging.
