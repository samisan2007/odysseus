# Library folders

In Library > Documents, choose a folder from the folder selector. **New folder** accepts nested paths such as `Work/Notes`; within a folder the button becomes **New subfolder**. Use a document's **Move to folder** action or select several documents and click **Move**. New documents and imported files go into the selected folder. **All documents** searches across folders; **Library root** shows unfiled documents.

Agents use existing document tools:

```json
{"action":"create_folder","folder":"Work/Notes"}
{"action":"list_folders"}
{"action":"list","folder":"Work/Notes"}
{"action":"move","document_id":"...","folder":"Work/Notes"}
```

These are `manage_documents` actions. `create_document` also accepts `folder`. An empty folder string selects the library root; omitting the list filter includes all folders. Folder actions remain scoped to the calling user.

PDF viewing falls back to the browser's original-PDF view when PyMuPDF is unavailable. Advanced rendering, form filling, and annotation export use the optional PyMuPDF package. In the Docker deployment, installing it as the `odysseus` user persists it in the existing `/app/.local` volume; restart the app after installation to refresh cached imports.
## Reusable tags

In **Library > Documents**, tag filters can be combined; selecting several tags shows documents that have every selected tag. Use a document's **Tags** action to replace its tags, or select several documents and choose **Add tags**. New documents and imports inherit the active tag filters. The **Tags** tab creates, renames, and deletes reusable tags. Deleting a tag removes the label only and keeps every document.

Agents use the same library tools:

```json
{"action":"list_tags"}
{"action":"create_tag","name":"Research"}
{"action":"set_tags","document_id":"...","tag_ids":["..."]}
{"action":"list","tag_ids":["...","..."]}
{"action":"delete_tag","tag_id":"..."}
```

`create_document` also accepts `tag_ids`. Tag names are unique per user without regard to case, and list filtering requires all supplied tag IDs.
